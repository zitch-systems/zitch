"""Return to Partnership without moving or reclassifying collection funds."""
from django.conf import settings
from django.db.models import Q

from wallet.models import Transaction
from .models import Receipt, VirtualAccount

REVIEW_MESSAGE = (
    "Your return to Wema Partnership needs a balance review. Your money and "
    "transaction history are preserved. Contact support before adding or spending money."
)


def return_enabled():
    return (getattr(settings, "WEMA_PARTNERSHIP_RESTORE_VAS", False) is True
            and getattr(settings, "BANK_ACCOUNT_PROVIDER", "") == "partnership"
            and getattr(settings, "WEMA_PARTNERSHIP_MODE", "") == "active")


def return_blockers(user):
    """Read-only; spending callers hold Wallet's lock, as VAS credits do.

    A zero balance alone is insufficient: a pending bill may still refund, and a
    held receipt may represent money at the collection bank. Recheck on every
    instruction so a late VAS credit immediately closes Partnership spending.
    """
    from .services import account_balance

    account = VirtualAccount.objects.filter(user_id=user.pk, mode=VirtualAccount.LIVE).first()
    if account is None:
        return []
    blockers = []
    if not account.active or not user.is_active:
        blockers.append("restricted_account")
    if account_balance(account) != 0:
        blockers.append("vas_balance")
    if account.receipts.filter(state=Receipt.HELD).exists():
        blockers.append("held_receipts")
    if account.bill_fundings.filter(
            Q(transaction__transaction_status=Transaction.PENDING)
            | Q(transaction__transaction_status=Transaction.FAILED, refund__isnull=True)).exists():
        blockers.append("unresolved_bills")
    return blockers


def partnership_allowed(user):
    if not VirtualAccount.objects.filter(user_id=user.pk, mode=VirtualAccount.LIVE).exists():
        return True
    return return_enabled() and not return_blockers(user)


def first_partnership_setup(user):
    """Independently verified VAS customers who have never started bank issuance.

    Verified identity is retained, but it is not evidence that Wema has opened a
    Partnership account. Any prior bank attempt/proof/cutover keeps its existing
    recovery path; this exception must never restart uncertain bank issuance.
    """
    from accounts.models import IdentityProof
    from wallet.models import Wallet, WemaFaceSession, WemaProvisioningAttempt
    from .models import MigrationApproval

    if not return_enabled() or not partnership_allowed(user):
        return False
    accounts = VirtualAccount.objects.filter(user=user)
    if not accounts.exists() or accounts.exclude(cutover_reference="").exists():
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


def return_inventory():
    """Aggregate diagnostics only: no identities, account numbers or secrets."""
    from wallet.models import Wallet
    accounts = VirtualAccount.objects.filter(mode=VirtualAccount.LIVE).select_related("user")
    counts = {"live_accounts": 0, "eligible_accounts": 0, "review_accounts": 0,
              "vas_balance": 0, "held_receipts": 0, "unresolved_bills": 0, "restricted_account": 0,
              "retained_partnership_accounts": 0, "missing_partnership_accounts": 0}
    for account in accounts:
        blockers = return_blockers(account.user)
        counts["live_accounts"] += 1
        counts["review_accounts" if blockers else "eligible_accounts"] += 1
        retained = Wallet.objects.filter(user=account.user).exclude(account_number="").exists()
        counts["retained_partnership_accounts" if retained else "missing_partnership_accounts"] += 1
        for blocker in blockers:
            counts[blocker] += 1
    return counts
