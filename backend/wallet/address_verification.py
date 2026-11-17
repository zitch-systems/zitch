"""Tier 3 requires bank evidence bound to the submitted address and request."""
import re

from django.contrib.auth import get_user_model


ADDRESS_VERIFICATION_UNAVAILABLE_MESSAGE = (
    "Address verification is temporarily unavailable. "
    "Please contact Zitch Support for help with Tier 3 verification.")


def tier3_address_capability():
    """Account-level bank status does not identify a submitted address or request.

    This is a contract requirement, not a configurable bypass. Until an
    authenticated, correlated terminal-result adapter exists, collecting a new
    address would create a request whose completion we cannot safely establish.
    """
    return {
        "tier3_address_available": False,
        "tier3_address_unavailable_reason": ADDRESS_VERIFICATION_UNAVAILABLE_MESSAGE,
    }


_TIER_WORDS = {"one": 1, "two": 2, "three": 3, "i": 1, "ii": 2, "iii": 3}


def bank_tier_number(value):
    """The tier in the bank's label, in whatever form it comes: "2", "Tier 2",
    "TIER_2", "tier-2", "Level 2", "Partnership Accounts – Tier 2", "Tier Two".

    The bank's documentation does not list the values ``accountTier`` takes, so
    any label that names exactly one tier (a lone 1, 2 or 3, not part of a
    longer number such as a year) is read as that tier. A label naming none, or
    two different ones, is 0: unknown, never a guess.
    """
    text = str(value or "").strip().lower()
    found = {int(d) for d in re.findall(r"(?<![0-9])([123])(?![0-9])", text)}
    if not found:
        # A label that spells it out ("Tier Two", "Tier II") but carries no digit.
        found = {_TIER_WORDS[w] for w in re.findall(
            r"(?:tier|level|kyc)[\s_:-]*(one|two|three|iii|ii|i)\b", text)}
    return found.pop() if len(found) == 1 else 0


#: Read the bank back for a webhook-confirmed address at most this often per
#: customer (the app polls the status; the reconcile job runs every five minutes).
_TIER3_READBACK_SECONDS = 120


def refresh_address_verification(user, wallet=None, result=None) -> bool:
    """Reload the address records, and finish Tier 3 when Wema's address webhook
    already said this address passed and the bank's own record now shows Tier 3.

    Wema's account-status response carries neither the submitted address nor a
    verification request reference, so on its own it never approves an address
    or releases it for another submission. Only after the webhook has tied the
    result to this account and request (``tier3_review`` is TIER3_BANK_OK) is the
    status read back to finish it, because Wema sends that webhook once and does
    not retry. ``result`` is a status read-back the caller already has.
    """
    from django.core.cache import cache

    from wallet.models import Wallet
    from wallet.wema_callbacks import TIER3_BANK_OK, grant_tier3_once_bank_shows_it

    user._address_verification_refreshed = True
    current = get_user_model().objects.get(pk=user.pk)
    if (current.tier3_review == TIER3_BANK_OK and current.address_verification_pending
            and not current.address_verified):
        wallet = wallet or Wallet.objects.filter(user=current).first()
        if wallet is not None and wallet.account_number and (
                result is not None
                or cache.add(f"wema_tier3_readback:{current.pk}", 1, _TIER3_READBACK_SECONDS)):
            grant_tier3_once_bank_shows_it(current, wallet, status=result)
            current = get_user_model().objects.get(pk=user.pk)
    for field in ("address", "address_verified", "address_verification_pending",
                  "address_verification_requested_at", "address_verification_account_number",
                  "tier3_review", "tier3_review_at", "tier_cap", "tier"):
        setattr(user, field, getattr(current, field))
    return bool(current.address_verified)
