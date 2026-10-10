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


def bank_tier_number(value):
    """Accept one explicit tier, never concatenate digits from an unknown label."""
    match = re.fullmatch(r"(?:tier[\s_-]*)?([123])", str(value or "").strip(), re.I)
    return int(match.group(1)) if match else 0


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
