"""Partnership eligibility with immutable historical funding holds."""
import re
import hashlib

from wallet.models import Wallet, UnsettledFunding

REVIEW_MESSAGE = (
    "Your account needs a balance review. Your money and transaction history are "
    "preserved. Contact support before adding or spending money."
)


def legacy_funding_blockers(user):
    """Read-only; spend callers hold the wallet lock before evaluating exposure."""
    if UnsettledFunding.objects.filter(user_id=user.pk, archive__isnull=True).exists():
        return ["legacy_balance"]
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
    return any(getattr(user, f"{kind}_verified", False) and getattr(user, f"{kind}_hash", "")
               and IdentityProof.objects.filter(user=user, identity_type=kind,
                   identity_hash=getattr(user, f"{kind}_hash"),
                   source=IdentityProof.IDENTITY_PROVIDER_OTP).exists()
               for kind in ("bvn", "nin"))
