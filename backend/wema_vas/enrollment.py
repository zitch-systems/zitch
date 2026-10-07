"""Customer-controlled enrollment using existing, durable identity proof.

This never upgrades KYC or converts a Partnership account/balance. The original
bank number remains available for historical settlement and reconciliation.
"""
import re
import secrets

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models import IdentityProof, User, hash_identifier
from wallet.models import Transaction, Wallet, WemaFaceSession, WemaProvisioningAttempt

from .config import (approval_reference_present, config, enrollment_release_policy,
                     validate_configuration, validation_legacy_balance_user_ids,
                     validation_user_ids)
from .identity import encrypt_identity
from .models import MigrationApproval, VirtualAccount

CONSENT_VERSION = "vas-identity-v1"
VALIDATION_CONSENT_VERSION = "vas-validation-identity-v2"
PENDING_MESSAGE = "Your new funding account is being prepared. Please wait before sending money."
SPENDING_MESSAGE = "Payments and transfers are unavailable while the new bank connection is completed."
VALIDATION_MESSAGE = "Account activation pending."


def consent_version(mode=None):
    return VALIDATION_CONSENT_VERSION if (mode or config().get("MODE", "validation")) == VirtualAccount.VALIDATION else CONSENT_VERSION


def enrollment_available(user=None):
    """Customer enrollment/funding visibility, distinct from bank event delivery."""
    values = config()
    policy = enrollment_release_policy(values)
    if (policy["errors"] or policy["phase"] == "closed"
            or values.get("ENABLED") is not True or values.get("ENABLE_ENROLLMENT") is not True
            or values.get("MODE") != "live"
            or not approval_reference_present(values.get("LIVE_APPROVAL_REFERENCE"))
            or not isinstance(values.get("COLLECTION_ACCOUNT"), str)
            or not re.fullmatch(r"[0-9]{10}", values["COLLECTION_ACCOUNT"])
            or getattr(settings, "BANK_ACCOUNT_PROVIDER", "partnership") != "wema_vas"):
        return False
    if user is not None and (not getattr(user, "is_active", False) or not getattr(user, "pk", None)):
        return False
    if policy["phase"] == "pilot" and (
            user is None or getattr(user, "pk", None) not in policy["pilot_user_ids"]):
        return False
    try:
        validate_configuration()
    except ImproperlyConfigured:
        return False
    return True


def _validation_policy_allows(user):
    """Opt-in self-service expands the testers, never the live release policy."""
    values = config()
    self_service = values.get("VALIDATION_SELF_SERVICE", False)
    if (values.get("ENABLED") is not True
            or values.get("ENABLE_VALIDATION_ENROLLMENT") is not True
            or values.get("ENABLE_ENROLLMENT", False) is not False
            or values.get("MODE") != "validation" or values.get("PREFIX") != "711"
            or values.get("RELEASE_PHASE", "closed") != "closed"
            or getattr(settings, "BANK_ACCOUNT_PROVIDER", "partnership") != "wema_vas"
            or not getattr(user, "is_active", False) or not getattr(user, "pk", None)
            or type(self_service) is not bool
            or (not self_service and user.pk not in validation_user_ids(values))):
        return False
    try:
        validate_configuration()
    except (ImproperlyConfigured, TypeError, ValueError):
        return False
    return True


def validation_enrollment_available(user=None):
    """Allow verified users to opt into the isolated 711 validation ledger."""
    return bool(_validation_policy_allows(user)
                and getattr(user, "phone_verified", False) and getattr(user, "email_verified", False))


def customer_enrollment_available(user=None):
    """Customer contact verification and enrollment; never a spending gate."""
    if (not getattr(user, "is_active", False) or not getattr(user, "pk", None)
            or not getattr(user, "phone_verified", False) or not getattr(user, "email_verified", False)):
        return False
    return enrollment_available(user) or validation_enrollment_available(user)


def customer_account_payload(user):
    values = config()
    mode = values.get("MODE", "validation")
    account = VirtualAccount.objects.filter(user=user, mode=mode).first()
    selected = getattr(settings, "BANK_ACCOUNT_PROVIDER", "partnership") == "wema_vas"
    if not selected and not VirtualAccount.objects.filter(user=user, mode=VirtualAccount.LIVE).exists():
        return None
    permitted = customer_enrollment_available(user)
    ready = bool(enrollment_available(user) and account and account.active and account.mode == VirtualAccount.LIVE
                 and account.prefix == values.get("PREFIX"))
    test_mode = values.get("MODE") == "validation"
    state = "ready" if ready else "vas_enrollment_required"
    if account and not account.active:
        state = "restricted"
    elif account and account.mode == VirtualAccount.VALIDATION:
        state = "vas_validation"
    eligibility = enrollment_eligibility(user, account=account)
    return {
        "provider": "wema_vas", "has_account": ready, "available": ready,
        "account_number": account.number if ready else "",
        "account_name": account.display_name if ready else "",
        "bank_name": "Wema Bank" if ready else "", "bank_accounts": [], "bank_tier": 0,
        "account_setup_state": state, "spending_available": False,
        "test_mode": test_mode,
        # Validation details belong to the authenticated bank/operator paths,
        # never customer funding instructions. Retain the fields for old clients.
        "validation_account_number": "",
        "validation_account_name": "",
        "enrollment_available": bool(permitted and account is None and eligibility["enrollment_status"] == "ready"),
        "migration_message": SPENDING_MESSAGE if ready else (
            "Your account is restricted. Please contact support." if state == "restricted" else
            VALIDATION_MESSAGE if test_mode and account else
            eligibility["enrollment_message"] if test_mode else PENDING_MESSAGE),
        "enrollment_endpoint": "/api/wallet/vas/enroll/",
        "consent_version": consent_version(mode),
        **eligibility,
    }


def named_identity_proof(user, kind, digest):
    """Keep the first substantive trusted name; blank historical rows are not names."""
    proofs = IdentityProof.objects.filter(user=user, identity_type=kind, identity_hash=digest,
        source__in=[source for source, _ in IdentityProof.SOURCE_CHOICES]).exclude(
            verified_name="").order_by("pk")
    return next((proof for proof in proofs if " ".join(proof.verified_name.split())), None)


def _identity_proof_ready(user):
    for kind in ("bvn", "nin"):
        digest = getattr(user, f"{kind}_hash", "")
        if not getattr(user, f"{kind}_verified", False) or not digest:
            continue
        if named_identity_proof(user, kind, digest):
            return True
    return False


def _allocation_blockers(user, wallet, *, validation):
    """Read-only initial allocation checks; existing test accounts are not cutovers."""
    from wallet.services import wallet_expected_balance
    blockers = []
    expected_balance = wallet_expected_balance(user.pk)
    retain_legacy_balance = bool(
        validation and _validation_policy_allows(user)
        and user.pk in validation_legacy_balance_user_ids()
        and wallet is not None and wallet.balance == expected_balance
        and expected_balance >= 0
    )
    # A reviewed tester may retain real legacy funds; never copy, erase or
    # reclassify them. Live cutover and unmatched balances keep the zero rule.
    if not retain_legacy_balance and (
            (wallet is not None and wallet.balance != 0) or expected_balance != 0):
        blockers.append("balance_review")
    if Transaction.objects.filter(user=user, transaction_status=Transaction.PENDING).exists():
        blockers.append("pending_transactions")
    pending_issuance = WemaProvisioningAttempt.objects.filter(user=user, status=WemaProvisioningAttempt.PENDING)
    if validation:
        # An expired, unaccepted OTP cannot request bank issuance. Keep its
        # history, while accepted attempts remain pending possible callbacks.
        pending_issuance = pending_issuance.filter(
            Q(expires_at__gt=timezone.now()) | Q(otp_verified_at__isnull=False))
    if (pending_issuance.exists()
            or WemaFaceSession.objects.filter(user=user, account_state="awaiting_callback").exists()
            or WemaFaceSession.objects.filter(user=user, status=WemaFaceSession.PENDING, expires_at__gt=timezone.now()).exists()):
        blockers.append("pending_bank_setup")
    if (not validation and wallet is not None and wallet.account_number
            and not MigrationApproval.objects.filter(user=user, legacy_account_number=wallet.account_number).exists()):
        blockers.append("migration_review")
    return blockers


_BLOCKER_MESSAGES = {
    "phone_verification": "Verify the phone number on your existing profile.",
    "email_verification": "Verify the email address on your existing profile.",
    "identity_verification": "Complete BVN or NIN verification to confirm your legal account name.",
    "balance_review": "Your existing balance needs a migration review before registration. Contact support; your funds are unchanged.",
    "pending_transactions": "Your pending transactions must finish or be reviewed before registration.",
    "pending_bank_setup": "Your earlier bank account setup must finish or be reviewed before registration.",
    "migration_review": "Your existing bank account needs an approved migration review before live registration.",
    "policy_unavailable": "Registration is not available for this profile yet. You can review your verification details.",
    "account_restricted": "Your account is restricted. Please contact support.",
}


def enrollment_eligibility(user, *, account=None):
    """Explain the current-mode requirements without allocating or exposing identity."""
    values = config()
    mode = values.get("MODE", "validation")
    account = account or VirtualAccount.objects.filter(user=user, mode=mode).first()
    wallet = Wallet.objects.filter(user=user).first()
    blockers = []
    policy = _validation_policy_allows(user) if mode == VirtualAccount.VALIDATION else enrollment_available(user)
    if account and not account.active:
        blockers.append("account_restricted")
    elif account:
        status = "enrolled"
    else:
        if not policy:
            blockers.append("policy_unavailable")
        if not user.phone_verified:
            blockers.append("phone_verification")
        if not user.email_verified:
            blockers.append("email_verification")
        if not _identity_proof_ready(user):
            blockers.append("identity_verification")
        blockers.extend(_allocation_blockers(user, wallet, validation=mode == VirtualAccount.VALIDATION))
    if blockers:
        status = ("restricted" if "account_restricted" in blockers else
                  "not_available" if "policy_unavailable" in blockers else
                  "review_required" if any(code in blockers for code in (
                      "balance_review", "pending_transactions", "pending_bank_setup", "migration_review")) else
                  "verification_required")
    elif not account:
        status = "ready"
    messages = dict(_BLOCKER_MESSAGES)
    if user.bvn_verified or user.nin_verified:
        messages["identity_verification"] = (
            "Your previous verification is saved. Confirm your legal name and identity ownership "
            "with BVN or NIN verification to complete the new account setup.")
    message = " ".join(messages[code] for code in blockers)
    if not message:
        message = (VALIDATION_MESSAGE if account and mode == VirtualAccount.VALIDATION else
                   "Your funding account is already registered." if account else
                   "Confirm your verified identity and consent to continue account setup for bank integration validation." if mode == VirtualAccount.VALIDATION else
                   "Confirm your verified identity and consent to register your new funding account.")
    return {"enrollment_mode": mode, "enrollment_status": status,
            "enrollment_blockers": blockers, "enrollment_message": message,
            "re_registration_required": bool(wallet and wallet.account_number and account is None)}


@transaction.atomic
def enroll_customer(user, *, bvn="", nin="", consent=False, consent_reference="",
                    expected_mode=None, expected_consent_version=None):
    """Derive the mode from server policy and recheck eligibility under locks."""
    if not customer_enrollment_available(user):
        raise ValidationError("New funding accounts are not available yet.")
    if ((expected_mode is not None and expected_mode != config().get("MODE"))
            or (expected_consent_version is not None and expected_consent_version != consent_version())):
        raise ValidationError("Registration has changed. Review the current account type and consent before continuing.")
    # Match the allocator/notification lock order. Refresh durable contact and
    # activity flags after waiting, so a stale request cannot undo revocation.
    Wallet.objects.get_or_create(user=user)
    Wallet.objects.select_for_update().get(user=user)
    user = User.objects.select_for_update().get(pk=user.pk)
    if ((expected_mode is not None and expected_mode != config().get("MODE"))
            or (expected_consent_version is not None and expected_consent_version != consent_version())):
        raise ValidationError("Registration has changed. Review the current account type and consent before continuing.")
    if not customer_enrollment_available(user):
        raise ValidationError("New funding accounts are not available yet.")
    return enroll_verified(user, bvn=bvn, nin=nin, consent=consent,
                           validation=validation_enrollment_available(user),
                           consent_reference=consent_reference)


def _verified_proofs(user, bvn, nin):
    if not user.is_active or not user.phone_verified:
        raise ValidationError("Verify your phone before activating your new funding account.")
    if not (bvn or nin):
        raise ValidationError("Enter your already verified BVN or NIN.")
    proofs = []
    for kind, raw in (("bvn", bvn), ("nin", nin)):
        if not raw:
            continue
        if not isinstance(raw, str) or not re.fullmatch(r"[0-9]{11}", raw):
            raise ValidationError("Enter an 11-digit BVN or NIN.")
        digest = hash_identifier(raw)
        if not getattr(user, f"{kind}_verified") or not secrets.compare_digest(digest, getattr(user, f"{kind}_hash") or ""):
            raise ValidationError("The identifier must match your verified identity.")
        proof = named_identity_proof(user, kind, digest)
        if proof is None:
            raise ValidationError("Verify your identity again to confirm your legal account name before activation.")
        proofs.append(proof)
    return proofs


def _cutover_reference(user, wallet, *, validation):
    blockers = _allocation_blockers(user, wallet, validation=validation)
    if blockers:
        raise ValidationError(" ".join(_BLOCKER_MESSAGES[code] for code in blockers))
    if validation or not wallet.account_number:
        # A retained legacy number is not a real cutover; never manufacture approval.
        return ""
    approval = MigrationApproval.objects.filter(user=user, legacy_account_number=wallet.account_number).first()
    if approval is None:
        raise ValidationError("Your existing bank account needs a reviewed migration before activation. Contact support.")
    return approval.reference


@transaction.atomic
def enroll_verified(user, *, bvn="", nin="", consent=False, validation=False, consent_reference=""):
    """Allocate once for an operator or the gated customer enrollment wrapper."""
    values = validate_configuration()
    if not values.get("ENABLED"):
        raise ValidationError("New funding accounts are not available yet.")
    if validation:
        if values.get("MODE") != "validation":
            raise ValidationError("Validation provisioning requires validation mode.")
    elif not enrollment_available(user):
        raise ValidationError("New funding accounts are not available yet.")
    if consent is not True:
        raise ValidationError("Consent to encrypted storage and sharing with Wema is required.")
    # Consistent lock order with notification/block/debit: wallet first, then user/account.
    Wallet.objects.get_or_create(user=user)
    wallet = Wallet.objects.select_for_update().get(user=user)
    user = User.objects.select_for_update().get(pk=user.pk)
    if not validation and not enrollment_available(user):
        raise ValidationError("New funding accounts are not available yet.")
    proofs = _verified_proofs(user, bvn, nin)
    existing = VirtualAccount.objects.select_for_update().filter(user=user, mode=values["MODE"]).first()
    if existing:
        if existing.mode != values["MODE"] or existing.prefix != values["PREFIX"]:
            raise ValidationError("This account needs a reviewed migration. Contact support.")
        return existing
    cutover = _cutover_reference(user, wallet, validation=validation)
    phone = (user.phone or "").lstrip("+")
    try:
        encrypted = encrypt_identity(bvn=bvn, nin=nin, phone=phone)
    except ValueError:
        raise ValidationError("A verified mobile number is required.") from None
    # The legal name is provider evidence, never editable profile content.
    name = " ".join(proofs[0].verified_name.split())
    if any(" ".join(proof.verified_name.split()).casefold() != name.casefold() for proof in proofs):
        raise ValidationError("Your verified identity names need review before activation. Contact support.")
    for _ in range(20):
        number = values["PREFIX"] + f"{secrets.randbelow(10_000_000):07d}"
        # Avoid confusing old bank callbacks and user funding instructions.
        if Wallet.objects.filter(account_number=number).exists():
            continue
        try:
            with transaction.atomic():
                return VirtualAccount.objects.create(
                    user=user, number=number, display_name=("Zitch/" + name)[:160],
                    encrypted_identity=encrypted,
                    verification_reference=",".join(f"IdentityProof:{proof.pk}" for proof in proofs),
                    consent_reference=consent_reference or f"{VALIDATION_CONSENT_VERSION if validation else CONSENT_VERSION}:{user.pk}:{timezone.now().isoformat()}",
                    verified_at=max(proof.created for proof in proofs),
                    mode=values["MODE"], prefix=values["PREFIX"], cutover_reference=cutover,
                )
        except IntegrityError:
            if not VirtualAccount.objects.filter(number=number).exists():
                raise
    raise ImproperlyConfigured("VAS number allocation temporarily unavailable")
