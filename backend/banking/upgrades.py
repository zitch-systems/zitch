"""9PSB tier upgrades with independent, correlated address evidence."""
import hmac
import logging
import re
import secrets

from django.db import transaction
from django.utils import timezone

from accounts.models import IdentityProof, User, hash_identifier, record_identity_proof
from wallet.models import Wallet

from .accounts import account_for, display_name, require_enabled, same_name, update_account
from .client import Client, ProviderError, affirmative_code, require_environment
from .models import AccountUpgrade, AddressVerification
from .payments import digest
from .secrets import unseal
from .validation import upgrade_profile

# The root logger is WARNING. Use the configured application audit stream so
# these bounded outcome records reach operators without exposing payloads.
log = logging.getLogger("zitch.banking.upgrades")

ACTIVE_UPGRADE_STATES = {"pending", "submitted", "review"}


def _reported_state(value):
    value = str(value or "").strip().upper()
    return value if value in {"APPROVED", "DECLINED", "PENDING", "PROCESSING",
                              "REVIEW", "SUBMITTED", "FAILED"} else "unrecognized"


def _log_upgrade_status(request, response, data, bank, decision):
    """Bounded evidence for what the bank reports about one upgrade.

    A request can sit in `submitted` for hours while the bank neither approves
    nor declines it. Without this the only trace is `checked_at` moving, and an
    operator cannot tell a silent provider from an unrecognised status: the
    reference, tier and bounded codes are the whole diagnosis. Customer data,
    account numbers, holder names and raw messages stay out.
    """
    def code(value):
        value = str(value or "")
        return value if re.fullmatch(r"[0-9]{2,4}", value) else "absent"
    echoed = data.get("accountNumber")
    keys = [key for key in ("status", "responseCode", "isSuccessful", "accountNumber",
                            "tier", "message") if key in data]
    log.info("bank_upgrade_status suffix=%s tier=%s stored=%s decision=%s http=%s code=%s "
             "nested_code=%s envelope=%s reported=%s account_echo=%s fields=%s",
             request.reference[-6:], request.tier, request.state, decision, response.http_status,
             code(response.body.get("responseCode")), code(data.get("responseCode")),
             _reported_state(response.body.get("status")), _reported_state(data.get("status")),
             "absent" if not echoed else ("match" if echoed == bank.number else "mismatch"),
             ",".join(keys))


def upgrade_payload(user):
    bank = account_for(user)
    row = (AccountUpgrade.objects.filter(account=bank).order_by("-created", "-pk").first()
           if bank else None)
    if not row:
        return None
    return {"reference": row.reference, "tier": row.tier, "state": row.state,
            "pending": row.state in ACTIVE_UPGRADE_STATES}


def refresh_customer_upgrade(user):
    """Check an owner's existing request with a shared, bounded read budget."""
    from django.core.cache import cache
    bank = account_for(user)
    if not bank or not AccountUpgrade.objects.filter(account=bank, state__in=ACTIVE_UPGRADE_STATES).exists():
        return False
    require_enabled(user)
    try:
        claimed = cache.add(f"bank-upgrade-refresh:{bank.environment}:{bank.pk}", True, timeout=20)
    except Exception:
        return True
    if not claimed:
        return False
    try:
        refresh_upgrade(bank, timeout=(2, 4))
    except ProviderError:
        return True
    return False


def _pending_result(request):
    return {"success": False, "pending": True, "reference": request.reference,
            "message": "Your bank upgrade is being processed."}


def submit_upgrade(user, tier, payload, *, address_reference=""):
    require_enabled(user)
    bank = account_for(user)
    if not bank:
        raise ValueError("Complete bank account setup first")
    if str(tier) not in {"2", "3"}:
        raise ValueError("Choose Tier 2 or Tier 3")
    tier = int(tier)
    existing = AccountUpgrade.objects.filter(account=bank, state__in=ACTIVE_UPGRADE_STATES).first()
    if existing:
        return _pending_result(existing)
    if not isinstance(payload, dict):
        raise ValueError("Complete your upgrade details")
    update_account(bank)
    if bank.tier >= tier:
        approved = AccountUpgrade.objects.filter(account=bank, tier__gte=tier, state="approved").exists()
        if approved and user.tier >= tier:
            return {"success": True, "bank_tier": bank.tier, "message": "Your bank tier is already active."}
        raise ValueError("Bank tier evidence needs review before this upgrade can be enabled")
    payload = dict(payload)
    # Documents uploaded beforehand fill in whatever the request doesn't carry.
    for kind, document in stored_documents(user).items():
        if not payload.get(kind):
            payload[kind] = document
    # The BVN is the one the account was opened with; the customer doesn't type it again.
    if bank.encrypted_bvn:
        payload["bvn"] = unseal(bank.encrypted_bvn)
    if not IdentityProof.objects.filter(user=user, identity_type="bvn", identity_hash=hash_identifier(str(payload.get("bvn", ""))),
            source__in=["9psb_otp", "9psb_face"]).exists():
        raise ValueError("Enter the BVN your account was opened with")
    # The NIN is checked by the bank with the upgrade; it must not belong to anyone else here.
    nin_hash = hash_identifier(str(payload.get("nin", "")))
    if User.objects.exclude(pk=user.pk).filter(nin_hash=nin_hash).exists() or \
            IdentityProof.objects.exclude(user=user).filter(identity_type="nin", identity_hash=nin_hash).exists():
        raise ValueError("This NIN is already linked to another account")
    if user.nin_hash and not hmac.compare_digest(user.nin_hash, nin_hash):
        raise ValueError("Use the NIN already on your account")
    address = None
    # Client-uploaded address proof is not provider verification.
    payload = {k: v for k, v in payload.items() if k != "proofOfAddressVerification"}
    if tier == 3:
        verified = AddressVerification.objects.filter(user=user, state="verified")
        address = (verified.filter(reference=address_reference) if address_reference else verified.order_by("-created", "-pk")).first()
        if not address or not address.encrypted_report:
            raise ValueError("Verify your home address before Tier 3")
        stored_address = unseal(address.encrypted_address)
        # The verified address is the Tier 3 address: the customer isn't asked to type it again.
        for field, key in (("houseNumber", "house_number"), ("streetName", "street"), ("city", "city"),
                           ("state", "state"), ("localGovernment", "lga"), ("nearestLandmark", "landmark")):
            if not str(payload.get(field) or "").strip():
                payload[field] = stored_address.get(key, "")
        expected = {"house_number": payload.get("houseNumber"), "street": payload.get("streetName"),
            "city": payload.get("city"), "state": payload.get("state"), "lga": payload.get("localGovernment")}
        if any(str(stored_address.get(k, "")).strip().casefold() != str(v or "").strip().casefold() for k, v in expected.items()):
            raise ValueError("The upgrade address must match the verified address")
        payload["proofOfAddressVerification"] = unseal(address.encrypted_report)["document"]
    body = upgrade_profile(tier, payload)
    # The bank names a wallet "MERCHANT/CUSTOMER NAME", and the customer is only
    # ever shown, and only ever submits, the customer part of it. Compare on that
    # part, the way `account_payload` already presents it, and compare it the way
    # every other name check here does: the same person may be written in a
    # different order or spacing, and the merchant prefix is never theirs to send.
    confirmed = display_name(bank.name)
    if not same_name(body["accountName"], confirmed):
        raise ValueError("Use the name confirmed on your bank account")
    # Submit the bank's own spelling, whatever order the client sent.
    body["accountName"] = confirmed
    with transaction.atomic():
        Wallet.objects.select_for_update().get(pk=bank.wallet_id)
        existing = AccountUpgrade.objects.filter(account=bank, state__in=["pending", "submitted", "review"]).first()
        if existing:
            return _pending_result(existing)
        request = AccountUpgrade.objects.create(account=bank, reference="ZU" + secrets.token_hex(10).upper(),
            tier=tier, bvn_hash=hash_identifier(body["bvn"]), nin_hash=hash_identifier(body["nin"]),
            address_verification=address, request_digest=digest(body))
    clear_documents(user)  # Sent to the bank now; Zitch keeps no copy.
    try:
        response = Client().upgrade(bank.number, tier, body)
    except ProviderError:
        AccountUpgrade.objects.filter(pk=request.pk).update(last_error="submission_unconfirmed")
        return {"success": False, "pending": True, "reference": request.reference,
                "message": "Your upgrade is awaiting bank confirmation. Do not submit it again."}
    # HTTP success means submission. Approval requires a separate bank read-back.
    AccountUpgrade.objects.filter(pk=request.pk).update(state="submitted" if response.ok else "review",
        last_error="" if response.ok else "bank_submission_needs_review")
    try:
        refresh_upgrade(bank)
    except ProviderError:
        pass  # Submitted; the status read-back is retried later. Not a failed upgrade.
    request.refresh_from_db()
    return {"success": request.state == "approved", "pending": request.state != "approved",
        "reference": request.reference, "bank_tier": bank.tier,
        "message": "Upgrade approved." if request.state == "approved" else "Your upgrade is being reviewed."}


def refresh_upgrade(bank, *, timeout=None):
    require_environment(bank.environment)
    request = AccountUpgrade.objects.filter(account=bank, state__in=["pending", "submitted", "review"]).order_by("created").first()
    if not request:
        return False
    response = Client(**({"timeout": timeout} if timeout else {})).upgrade_status(bank.number)
    data = response.data if isinstance(response.data, dict) else {}
    # The documented account-level status must be read for our account, and any
    # echoed account must agree. The callback's status alone is never accepted.
    status = str(data.get("status", "")).upper()
    successful = data.get("isSuccessful", True)
    if (not response.ok or not affirmative_code(response.body.get("responseCode"))
            or (status == "APPROVED" and (not affirmative_code(data.get("responseCode"))
                or (successful is not True and successful not in ("true", "True"))))
            or (data.get("accountNumber") and data["accountNumber"] != bank.number)):
        _log_upgrade_status(request, response, data, bank, "unconfirmed")
        raise ProviderError("upgrade_status_unconfirmed")
    if status == "DECLINED":
        _log_upgrade_status(request, response, data, bank, "declined")
        AccountUpgrade.objects.filter(pk=request.pk).update(state="declined", checked_at=timezone.now())
        return False
    if status != "APPROVED":
        # Record what the bank last reported, not just that it was asked: an
        # upgrade can wait here for hours, and an empty last_error cannot tell
        # a bank still deciding from a status this build does not recognise.
        _log_upgrade_status(request, response, data, bank, "undecided")
        AccountUpgrade.objects.filter(pk=request.pk).update(
            checked_at=timezone.now(), last_error="status_" + _reported_state(status).lower())
        return False
    _log_upgrade_status(request, response, data, bank, "approved")
    update_account(bank, **({"timeout": timeout} if timeout else {}))
    if bank.tier < request.tier:
        AccountUpgrade.objects.filter(pk=request.pk).update(checked_at=timezone.now())
        return False
    with transaction.atomic():
        wallet = Wallet.objects.select_for_update().get(pk=bank.wallet_id)
        user = User.objects.select_for_update().get(pk=wallet.user_id)
        locked = AccountUpgrade.objects.select_for_update().get(pk=request.pk)
        if not hmac.compare_digest(user.bvn_hash, locked.bvn_hash) or (
                user.nin_hash and not hmac.compare_digest(user.nin_hash, locked.nin_hash)):
            locked.state, locked.last_error = "review", "identity_changed"
        elif locked.tier == 3 and (not locked.address_verification or locked.address_verification.state != "verified"):
            locked.state, locked.last_error = "review", "address_evidence_unconfirmed"
        else:
            locked.state = "approved"
            # Tier recomputation reads the durable approval in this transaction.
            locked.save(update_fields=["state"])
            # Approval verifies this NIN even when an earlier integration
            # already stored its hash. A pre-existing hash is not a substitute
            # for the new bank's durable proof, and must not strand the app on
            # Tier 1 after the bank has approved Tier 2 or Tier 3.
            record_identity_proof(user, "nin", locked.nin_hash, prehashed=True, source="9psb_upgrade",
                                  provider_reference=locked.reference, verified_name=bank.name)
            user.nin_hash, user.nin_verified = locked.nin_hash, True
            user.save(update_fields=["nin_hash", "nin_verified"])
            if locked.tier == 3:
                user.address_verified = True
                user.address_verification_pending = False
            user.recompute_tier()
            user.save(update_fields=["address_verified", "address_verification_pending", "tier"])
        locked.checked_at = timezone.now()
        locked.save(update_fields=["state", "last_error", "checked_at"])
    return locked.state == "approved"


def restore_approved_state(user, bank):
    """Repair flags from this account's durable approval and fresh bank tier."""
    require_environment(bank.environment)
    approval = AccountUpgrade.objects.filter(account=bank, state="approved", tier__lte=bank.tier)
    approval = approval.select_related("address_verification").order_by("-tier", "-created").first()
    if not approval or not hmac.compare_digest(user.bvn_hash, approval.bvn_hash) or (
            user.nin_hash and not hmac.compare_digest(user.nin_hash, approval.nin_hash)):
        return
    if user.tier >= approval.tier and user.nin_verified and IdentityProof.objects.filter(user=user,
            identity_type="nin", identity_hash=approval.nin_hash, source="9psb_upgrade").exists():
        return
    with transaction.atomic():
        Wallet.objects.select_for_update().get(pk=bank.wallet_id)
        current = User.objects.select_for_update().get(pk=user.pk)
        if not hmac.compare_digest(current.bvn_hash, approval.bvn_hash) or (
                current.nin_hash and not hmac.compare_digest(current.nin_hash, approval.nin_hash)):
            return
        record_identity_proof(current, "nin", approval.nin_hash, prehashed=True, source="9psb_upgrade",
            provider_reference=approval.reference, verified_name=bank.name)
        current.nin_hash, current.nin_verified = approval.nin_hash, True
        if approval.tier == 3 and approval.address_verification and approval.address_verification.state == "verified":
            current.address_verified, current.address_verification_pending = True, False
        current.recompute_tier()
        fields = ["nin_hash", "nin_verified", "address_verified", "address_verification_pending", "tier"]
        current.save(update_fields=fields)
        for field in fields:
            setattr(user, field, getattr(current, field))


# Documents are uploaded one at a time, converted to the bank's size limits at
# once, and held sealed for an hour until the upgrade is submitted.
DOCUMENT_KINDS = {"idCardFront": ("Upload your NIN slip", 100000, False),
                  "utilityBill": ("Upload a utility bill", 50000, True),
                  "customerSignature": ("Upload your signature", (500 * 1024 + 2) // 3 * 4, False),
                  "userPhoto": ("Verify your face to continue", 100000, False)}
DOCUMENT_SECONDS = 60 * 60


def _document_key(user, kind):
    return f"bank-upgrade-doc:{user.pk}:{kind}"


def store_document(user, kind, value):
    from django.core.cache import cache
    from .secrets import seal
    from .validation import fit_document
    if kind not in DOCUMENT_KINDS:
        raise ValueError("Choose a document to upload")
    missing, limit, allow_pdf = DOCUMENT_KINDS[kind]
    fitted = fit_document(value, maximum_chars=limit, missing=missing, allow_pdf=allow_pdf)
    cache.set(_document_key(user, kind), seal({"document": fitted}), DOCUMENT_SECONDS)
    return {"success": True, "kind": kind}


def stored_documents(user):
    """The documents this customer uploaded for the upgrade, still within their hour."""
    from django.core.cache import cache
    from cryptography.fernet import InvalidToken
    found = {}
    for kind in DOCUMENT_KINDS:
        sealed = cache.get(_document_key(user, kind))
        if not sealed:
            continue
        try:
            found[kind] = unseal(sealed)["document"]
        except (InvalidToken, KeyError, TypeError, ValueError):
            continue
    return found


def clear_documents(user):
    from django.core.cache import cache
    cache.delete_many([_document_key(user, kind) for kind in DOCUMENT_KINDS])
