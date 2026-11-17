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


def refresh_address_verification(user, wallet=None, result=None) -> bool:
    """Keep existing verification and pending records without inventing evidence.

    Wema's available account-status response carries neither the submitted
    address nor a verification request reference. Completed/Rejected, even after
    Pending, can belong to an older bank job. Therefore neither a fresh fetch nor
    a supplied account-status result can approve this address or release it for
    another submission. Bank-tier synchronization remains independent.
    """
    user._address_verification_refreshed = True
    current = get_user_model().objects.get(pk=user.pk)
    for field in ("address", "address_verified", "address_verification_pending",
                  "address_verification_requested_at", "address_verification_account_number", "tier"):
        setattr(user, field, getattr(current, field))
    return bool(current.address_verified)
