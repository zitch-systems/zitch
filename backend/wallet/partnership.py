"""Partnership eligibility with immutable historical funding holds."""
import re
import hashlib

from django.db.models import Q

from legacy_funding.models import MigrationApproval, Receipt, VirtualAccount
from wallet.models import Transaction, Wallet, UnsettledFunding

REVIEW_MESSAGE = (
    "Your account needs a balance review. Your money and transaction history are "
    "preserved. Contact support before adding or spending money."
)


def legacy_funding_blockers(user):
    """Read-only; spend callers hold the wallet lock before evaluating exposure."""
    from wallet.legacy_funding import account_balance

    if UnsettledFunding.objects.filter(user_id=user.pk).exists():
        return ["legacy_balance"]
    account = VirtualAccount.objects.filter(user_id=user.pk, mode=VirtualAccount.LIVE).first()
    if account is None:
        return []
    blockers = []
    if not account.active or not user.is_active:
        blockers.append("restricted_account")
    if account_balance(account) != 0:
        blockers.append("legacy_balance")
    if account.receipts.filter(state=Receipt.HELD).exists():
        blockers.append("held_receipts")
    if account.bill_fundings.filter(
            Q(transaction__transaction_status=Transaction.PENDING)
            | Q(transaction__transaction_status=Transaction.FAILED, refund__isnull=True)).exists():
        blockers.append("unresolved_bills")
    return blockers


def partnership_account_number(number):
    """Only a valid number with no retained retired-account identity is usable."""
    return (isinstance(number, str) and bool(re.fullmatch(r"[0-9]{10}", number))
            and not number.startswith("711")
            and not UnsettledFunding.objects.filter(
                destination_digest=hashlib.sha256(number.encode()).hexdigest()).exists()
            and not VirtualAccount.objects.filter(number=number).exists())


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
    accounts = VirtualAccount.objects.filter(user=user)
    if accounts.exclude(cutover_reference="").exists():
        return False
    if (Wallet.objects.filter(user=user).exclude(account_number="").exists()
            or MigrationApproval.objects.filter(user=user).exists()
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


def legacy_funding_inventory():
    """Aggregate diagnostics only; no identities, account numbers or secrets."""
    accounts = VirtualAccount.objects.filter(mode=VirtualAccount.LIVE).select_related("user")
    counts = {"live_accounts": 0, "eligible_accounts": 0, "review_accounts": 0,
              "legacy_balance": 0, "held_receipts": 0, "unresolved_bills": 0, "restricted_account": 0,
              "retained_partnership_accounts": 0, "missing_partnership_accounts": 0}
    for account in accounts:
        blockers = legacy_funding_blockers(account.user)
        counts["live_accounts"] += 1
        number = Wallet.objects.filter(user=account.user).values_list("account_number", flat=True).first()
        retained = partnership_account_number(number)
        review = bool(blockers or (number and not retained))
        counts["review_accounts" if review else "eligible_accounts"] += 1
        counts["retained_partnership_accounts" if retained else "missing_partnership_accounts"] += 1
        for blocker in blockers:
            counts[blocker] += 1
    return counts
