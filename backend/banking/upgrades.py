"""9PSB tier upgrades with independent, correlated address evidence."""
import hmac
import secrets

from django.db import transaction
from django.utils import timezone

from accounts.models import IdentityProof, User, hash_identifier
from wallet.models import Wallet

from .accounts import account_for, require_enabled, update_account
from .client import Client, ProviderError
from .models import AccountUpgrade, AddressVerification
from .payments import digest
from .secrets import unseal
from .validation import upgrade_profile


def submit_upgrade(user, tier, payload, *, address_reference=""):
    require_enabled(user)
    bank = account_for(user)
    if not bank:
        raise ValueError("Complete bank account setup first")
    tier = int(tier)
    if tier not in {2, 3}:
        raise ValueError("Choose Tier 2 or Tier 3")
    update_account(bank)
    if bank.tier >= tier:
        approved = AccountUpgrade.objects.filter(account=bank, tier__gte=tier, state="approved").exists()
        if approved and user.tier >= tier:
            return {"success": True, "bank_tier": bank.tier, "message": "Your bank tier is already active."}
        raise ValueError("Bank tier evidence needs review before this upgrade can be enabled")
    for kind in ("bvn", "nin"):
        proof = IdentityProof.objects.filter(user=user, identity_type=kind,
            identity_hash=hash_identifier(str(payload.get(kind, ""))), source__in=["9psb_otp", "9psb_face"]).exists()
        if not proof:
            raise ValueError("Verify both BVN and NIN with 9PSB before upgrading")
    address = None
    # Client-uploaded address proof is not provider verification.
    payload = {k: v for k, v in payload.items() if k != "proofOfAddressVerification"}
    if tier == 3:
        address = AddressVerification.objects.filter(user=user, reference=address_reference, state="verified").first()
        if not address or not address.encrypted_report:
            raise ValueError("Complete Prembly address verification before Tier 3")
        stored_address = unseal(address.encrypted_address)
        expected = {"house_number": payload.get("houseNumber"), "street": payload.get("streetName"),
            "city": payload.get("city"), "state": payload.get("state"), "lga": payload.get("localGovernment")}
        if any(str(stored_address.get(k, "")).strip().casefold() != str(v or "").strip().casefold() for k, v in expected.items()):
            raise ValueError("The upgrade address must match the verified address")
        payload["proofOfAddressVerification"] = unseal(address.encrypted_report)["document"]
    body = upgrade_profile(tier, payload)
    if body["accountName"].strip().casefold() != bank.name.strip().casefold():
        raise ValueError("Use the name confirmed on your bank account")
    with transaction.atomic():
        Wallet.objects.select_for_update().get(pk=bank.wallet_id)
        existing = AccountUpgrade.objects.filter(account=bank, state__in=["pending", "submitted", "review"]).first()
        if existing:
            return {"success": False, "pending": True, "reference": existing.reference,
                    "message": "Your bank upgrade is being processed."}
        request = AccountUpgrade.objects.create(account=bank, reference="ZU" + secrets.token_hex(10).upper(),
            tier=tier, bvn_hash=hash_identifier(body["bvn"]), nin_hash=hash_identifier(body["nin"]),
            address_verification=address, request_digest=digest(body))
    try:
        response = Client().upgrade(bank.number, tier, body)
    except ProviderError:
        AccountUpgrade.objects.filter(pk=request.pk).update(last_error="submission_unconfirmed")
        return {"success": False, "pending": True, "reference": request.reference,
                "message": "Your upgrade is awaiting bank confirmation. Do not submit it again."}
    # HTTP success means submission. Approval requires a separate bank read-back.
    AccountUpgrade.objects.filter(pk=request.pk).update(state="submitted" if response.ok else "review",
        last_error="" if response.ok else "bank_submission_needs_review")
    refresh_upgrade(bank)
    request.refresh_from_db()
    return {"success": request.state == "approved", "pending": request.state != "approved",
        "reference": request.reference, "bank_tier": bank.tier,
        "message": "Upgrade approved." if request.state == "approved" else "Your upgrade is being reviewed by 9PSB."}


def refresh_upgrade(bank):
    request = AccountUpgrade.objects.filter(account=bank, state__in=["pending", "submitted", "review"]).order_by("created").first()
    if not request:
        return False
    response = Client().upgrade_status(bank.number)
    data = response.data if isinstance(response.data, dict) else {}
    # The documented account-level status must be read for our account, and any
    # echoed account must agree. The callback's status alone is never accepted.
    if not response.ok or (data.get("accountNumber") and data["accountNumber"] != bank.number):
        raise ProviderError("upgrade_status_unconfirmed")
    status = str(data.get("status", "")).upper()
    if status == "DECLINED":
        AccountUpgrade.objects.filter(pk=request.pk).update(state="declined", checked_at=timezone.now())
        return False
    update_account(bank)
    if status != "APPROVED" or bank.tier < request.tier:
        AccountUpgrade.objects.filter(pk=request.pk).update(checked_at=timezone.now())
        return False
    with transaction.atomic():
        wallet = Wallet.objects.select_for_update().get(pk=bank.wallet_id)
        user = User.objects.select_for_update().get(pk=wallet.user_id)
        locked = AccountUpgrade.objects.select_for_update().get(pk=request.pk)
        if not hmac.compare_digest(user.bvn_hash, locked.bvn_hash) or not hmac.compare_digest(user.nin_hash, locked.nin_hash):
            locked.state, locked.last_error = "review", "identity_changed"
        elif locked.tier == 3 and (not locked.address_verification or locked.address_verification.state != "verified"):
            locked.state, locked.last_error = "review", "address_evidence_unconfirmed"
        else:
            locked.state = "approved"
            # Tier recomputation reads the durable approval in this transaction.
            locked.save(update_fields=["state"])
            if locked.tier == 3:
                user.address_verified = True
                user.address_verification_pending = False
            user.recompute_tier()
            user.save(update_fields=["address_verified", "address_verification_pending", "tier"])
        locked.checked_at = timezone.now()
        locked.save(update_fields=["state", "last_error", "checked_at"])
    return locked.state == "approved"
