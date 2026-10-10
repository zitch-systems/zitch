"""Durable bank address-verification completion; acceptance is not Tier 3."""
import logging
import re

from django.contrib.auth import get_user_model
from django.db import transaction

from utility import wema
from wallet.models import Wallet

log = logging.getLogger("wallet")


def bank_tier_number(value):
    """Accept one explicit tier, never concatenate digits from an unknown label."""
    match = re.fullmatch(r"(?:tier[\s_-]*)?([123])", str(value or "").strip(), re.I)
    return int(match.group(1)) if match else 0


def address_status(value):
    if not isinstance(value, str):
        return "unknown"
    normalized = " ".join(re.sub(r"[_-]", " ", value).strip().casefold().split())
    if normalized in {"verified", "completed", "complete", "approved", "successful", "success",
                      "address verified", "verification completed"}:
        return "completed"
    if normalized in {"failed", "rejected", "declined", "unsuccessful", "not verified"}:
        return "rejected"
    return "unknown"


def address_request_rejected(result):
    """Only an explicit address rejection can release an ambiguous submission."""
    if not isinstance(result, dict) or result.get("mock") or result.get("pending"):
        return False
    if result.get("rejected") is True:
        return True
    containers = [result]
    for item in containers:
        if not isinstance(item, dict):
            continue
        for key, value in item.items():
            name = str(key).replace("_", "").casefold()
            if name in {"addressverification", "addressverificationstatus", "verificationstatus"}:
                if address_status(value) == "rejected":
                    return True
            if name in {"data", "result", "response", "raw"} and isinstance(value, dict):
                containers.append(value)
    return False


def _copy_address_state(source, target):
    for field in ("address", "address_verified", "address_verification_pending",
                  "address_verification_requested_at", "address_verification_account_number", "tier"):
        setattr(target, field, getattr(source, field))


def refresh_address_verification(user, wallet=None, result=None) -> bool:
    """Confirm only a pending request with explicit authenticated bank completion.

    Bank reads run outside locks. Before promotion, recheck the exact pending
    request and attached account so an old read cannot approve a replacement.
    Supplying the existing status result avoids a second call from tier sync.
    """
    user._address_verification_refreshed = True
    User = get_user_model()
    current = User.objects.get(pk=user.pk)
    _copy_address_state(current, user)
    if current.address_verified or not current.address_verification_pending:
        return bool(current.address_verified)
    wallet = wallet or Wallet.objects.filter(user_id=user.pk).first()
    if wallet is None or wallet.user_id != user.pk or not wallet.account_number:
        return False
    account_number = wallet.account_number
    if current.address_verification_account_number != account_number:
        return False
    requested_at, address = current.address_verification_requested_at, current.address
    if requested_at is None or not address:
        return False
    try:
        response = result if result is not None else wema.get_kyc_status(account_number)
    except Exception:  # A failed read must retain the durable, non-repeatable request.
        log.exception("wema_address_status_unavailable user=%s", user.pk)
        return False
    if not isinstance(response, dict) or response.get("success") is not True or response.get("mock"):
        return False
    status = address_status(response.get("address_verification"))
    completed = bank_tier_number(response.get("tier")) == 3 and status == "completed"
    if not completed and status != "rejected":
        return False
    with transaction.atomic():
        locked_wallet = Wallet.objects.select_for_update().get(pk=wallet.pk)
        locked_user = User.objects.select_for_update().get(pk=user.pk)
        if (locked_wallet.user_id != user.pk or locked_wallet.account_number != account_number
                or not locked_user.address_verification_pending
                or locked_user.address_verification_account_number != account_number
                or locked_user.address_verification_requested_at != requested_at
                or locked_user.address != address):
            _copy_address_state(locked_user, user)
            return bool(locked_user.address_verified)
        locked_user.address_verification_pending = False
        fields = ["address_verification_pending"]
        if completed:
            locked_user.address_verified = True
            locked_user.recompute_tier()
            fields.extend(["address_verified", "tier"])
            locked_wallet.bank_tier = 3
            locked_wallet.save(update_fields=["bank_tier", "updated"])
            wallet.bank_tier = 3
        locked_user.save(update_fields=fields)
        _copy_address_state(locked_user, user)
    return completed
