"""Customer-controlled enrollment using existing, durable identity proof.

This never upgrades KYC or converts a Partnership account/balance. The original
bank number remains available for historical settlement and reconciliation.
"""
import re
import secrets

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import IdentityProof, User, hash_identifier
from wallet.models import Transaction, Wallet, WemaFaceSession, WemaProvisioningAttempt

from .config import config, validate_configuration
from .identity import encrypt_identity
from .models import MigrationApproval, VirtualAccount

CONSENT_VERSION = "vas-identity-v1"
PENDING_MESSAGE = "Your new funding account is being prepared. Please wait before sending money."
SPENDING_MESSAGE = "Payments and transfers are unavailable while the new bank connection is completed."


def enrollment_available():
    values = config()
    return bool(values.get("ENABLED") and values.get("ENABLE_ENROLLMENT")
                and values.get("MODE") == "live"
                and values.get("LIVE_APPROVAL_REFERENCE")
                and re.fullmatch(r"[0-9]{10}", values.get("COLLECTION_ACCOUNT", ""))
                and getattr(settings, "BANK_ACCOUNT_PROVIDER", "partnership") == "wema_vas")


def customer_account_payload(user):
    account = VirtualAccount.objects.filter(user=user).first()
    selected = getattr(settings, "BANK_ACCOUNT_PROVIDER", "partnership") == "wema_vas"
    if not selected and (account is None or account.mode != VirtualAccount.LIVE):
        return None
    ready = bool(account and account.active and account.mode == VirtualAccount.LIVE
                 and config().get("ENABLED") and config().get("ENABLE_ENROLLMENT")
                 and config().get("MODE") == "live" and account.prefix == config().get("PREFIX"))
    state = "ready" if ready else "vas_enrollment_required"
    if account and not account.active:
        state = "restricted"
    elif account and account.mode == VirtualAccount.VALIDATION:
        state = "vas_validation"
    return {
        "provider": "wema_vas", "has_account": ready, "available": ready,
        "account_number": account.number if ready else "",
        "account_name": account.display_name if ready else "",
        "bank_name": "Wema Bank" if ready else "", "bank_accounts": [], "bank_tier": 0,
        "account_setup_state": state, "spending_available": False,
        "enrollment_available": bool(enrollment_available() and account is None),
        "migration_message": SPENDING_MESSAGE if ready else (
            "Your account is restricted. Please contact support." if state == "restricted" else PENDING_MESSAGE),
        "enrollment_endpoint": "/api/wallet/vas/enroll/", "consent_version": CONSENT_VERSION,
    }


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
        proof = IdentityProof.objects.filter(
            user=user, identity_type=kind, identity_hash=digest,
            source__in=[source for source, _ in IdentityProof.SOURCE_CHOICES],
        ).exclude(verified_name="").order_by("pk").first()
        if proof is None:
            raise ValidationError("Verify your identity again to confirm your legal account name before activation.")
        proofs.append(proof)
    return proofs


def _cutover_reference(user, wallet, *, validation):
    if wallet.balance != 0 or Transaction.objects.filter(user=user, transaction_status=Transaction.PENDING).exists():
        raise ValidationError("Your existing balance or pending transactions need reconciliation before activation. Contact support.")
    if (WemaProvisioningAttempt.objects.filter(user=user, status=WemaProvisioningAttempt.PENDING).exists()
            or WemaFaceSession.objects.filter(user=user, account_state="awaiting_callback").exists()
            or WemaFaceSession.objects.filter(user=user, status=WemaFaceSession.PENDING, expires_at__gt=timezone.now()).exists()):
        raise ValidationError("Your earlier bank account setup needs to finish or be reviewed before activation. Contact support.")
    if not wallet.account_number:
        return ""
    if validation:
        raise ValidationError("Validation accounts require dedicated users without a Partnership account.")
    approval = MigrationApproval.objects.filter(user=user, legacy_account_number=wallet.account_number).first()
    if approval is None:
        raise ValidationError("Your existing bank account needs a reviewed migration before activation. Contact support.")
    return approval.reference


@transaction.atomic
def enroll_verified(user, *, bvn="", nin="", consent=False, validation=False, consent_reference=""):
    """Allocate once. Validation is internal-only for dedicated consented users."""
    values = validate_configuration()
    if not values.get("ENABLED"):
        raise ValidationError("New funding accounts are not available yet.")
    if validation:
        if values.get("MODE") != "validation":
            raise ValidationError("Validation provisioning requires validation mode.")
    elif not enrollment_available():
        raise ValidationError("New funding accounts are not available yet.")
    if consent is not True:
        raise ValidationError("Consent to encrypted storage and sharing with Wema is required.")
    # Consistent lock order with notification/block/debit: wallet first, then user/account.
    Wallet.objects.get_or_create(user=user)
    wallet = Wallet.objects.select_for_update().get(user=user)
    user = User.objects.select_for_update().get(pk=user.pk)
    proofs = _verified_proofs(user, bvn, nin)
    existing = VirtualAccount.objects.select_for_update().filter(user=user).first()
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
                    consent_reference=consent_reference or f"{CONSENT_VERSION}:{user.pk}:{timezone.now().isoformat()}",
                    verified_at=max(proof.created for proof in proofs),
                    mode=values["MODE"], prefix=values["PREFIX"], cutover_reference=cutover,
                )
        except IntegrityError:
            if not VirtualAccount.objects.filter(number=number).exists():
                raise
    raise ImproperlyConfigured("VAS number allocation temporarily unavailable")
