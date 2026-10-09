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


def return_inventory():
    """Aggregate diagnostics only: no identities, account numbers or secrets."""
    accounts = VirtualAccount.objects.filter(mode=VirtualAccount.LIVE).select_related("user")
    counts = {"live_accounts": 0, "eligible_accounts": 0, "review_accounts": 0,
              "vas_balance": 0, "held_receipts": 0, "unresolved_bills": 0, "restricted_account": 0}
    for account in accounts:
        blockers = return_blockers(account.user)
        counts["live_accounts"] += 1
        counts["review_accounts" if blockers else "eligible_accounts"] += 1
        for blocker in blockers:
            counts[blocker] += 1
    return counts
