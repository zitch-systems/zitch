"""Partnership eligibility with immutable historical funding holds."""
import re
import hashlib

from wallet.models import Wallet, UnsettledFunding
from django.db.models import Q



REVIEW_MESSAGE = (
    "Your account needs a balance review. Your money and transaction history are "
    "preserved. Contact support before adding or spending money."
)


_RETIRED_BANK_LABEL = (r"^\s*(9[ -]*psb|9[ -]*payment[ -]*service[ -]*bank"
                      r"([ -]*(ltd|limited|plc))?|(wema([ -]*bank)?|alat)[ -]+"
                      r"(vas|virtual[ -]*accounts?))\s*$")
_RETIRED_ACCOUNT_REFERENCE = r"^\s*(9psb|wema[-_ ]vas)([-_ ]|$)"


def retired_wallet_marker(bank_name, account_reference):
    """A retained legacy label or reference is sufficient to block reuse."""
    return bool(re.search(_RETIRED_BANK_LABEL, bank_name or "", re.I)
                or re.search(_RETIRED_ACCOUNT_REFERENCE, account_reference or "", re.I))


def retired_wallet_markers():
    return Q(bank_name__iregex=_RETIRED_BANK_LABEL) | Q(account_reference__iregex=_RETIRED_ACCOUNT_REFERENCE)


def legacy_funding_blockers(user):
    """Read-only; spend callers hold the wallet lock before evaluating exposure."""
    if UnsettledFunding.objects.filter(user_id=user.pk, archive__isnull=True).exists():
        return ["legacy_balance"]
    bank = Wallet.objects.filter(user_id=user.pk).values_list(
        "bank_name", "account_number", "account_reference").first()
    if bank and bank[1] and retired_wallet_marker(bank[0], bank[2]):
        return ["retired_provider_wallet_review"]
    return []


def retired_funding_destination(number):
    """Reject retired destinations even after their account records are deleted."""
    return bool(isinstance(number, str) and number and (
        number.startswith("711")
        or UnsettledFunding.objects.filter(
            destination_digest=hashlib.sha256(number.encode()).hexdigest()).exists()))


def partnership_account_number(number):
    """Only a valid, non-retired bank number is usable for Partnership."""
    return (isinstance(number, str) and bool(re.fullmatch(r"[0-9]{10}", number))
            and not retired_funding_destination(number))


def partnership_allowed(user):
    if legacy_funding_blockers(user):
        return False
    number = Wallet.objects.filter(user_id=user.pk).values_list("account_number", flat=True).first()
    # Blank is eligible for first setup; a stored invalid/retired number needs
    # review before any channel can start a bank upgrade or verification flow.
    return not number or partnership_account_number(number)


#: Identity proofs recorded by the retired 9PSB rail. Historical rows only:
#: no code creates them, and they are proof of identity, not of a Wema account.
PRE_WEMA_IDENTITY_SOURCES = ("9psb_otp", "9psb_face")


def first_partnership_setup(user):
    """Preserve independent identity proof without replaying uncertain issuance."""
    from accounts.models import IdentityProof
    from wallet.models import WemaFaceSession, WemaProvisioningAttempt

    if not partnership_allowed(user):
        return False
    if (Wallet.objects.filter(user=user).exclude(account_number="").exists()
            or WemaFaceSession.objects.filter(user=user).exists()
            or WemaProvisioningAttempt.objects.filter(user=user).exists()
            or IdentityProof.objects.filter(user=user, source__in=[IdentityProof.WEMA_WALLET_OTP,
                IdentityProof.WEMA_FACE, IdentityProof.WEMA_TIER2]).exists()):
        return False
    # A BVN/NIN verified by OTP before Wema (our identity provider, or the
    # retired 9PSB rail) never opened a Wema account. Wema has nothing to
    # reconnect, so these customers take the standard OTP opening.
    sources = [IdentityProof.IDENTITY_PROVIDER_OTP, *PRE_WEMA_IDENTITY_SOURCES]
    return any(getattr(user, f"{kind}_verified", False) and getattr(user, f"{kind}_hash", "")
               and IdentityProof.objects.filter(user=user, identity_type=kind,
                   identity_hash=getattr(user, f"{kind}_hash"),
                   source__in=sources).exists()
               for kind in ("bvn", "nin"))
