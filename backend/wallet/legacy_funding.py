"""Read-only attribution of retained funding evidence. No bank operations."""
from decimal import Decimal

from django.db.models import Sum

from legacy_funding.models import Receipt, VirtualAccount
from wallet.models import BillFundingRefund


def account_balance(account):
    """Original receipt credits less immutable reservations plus their releases."""
    if account.mode == VirtualAccount.VALIDATION:
        return account.validation_balance
    credits = account.receipts.filter(state=Receipt.CREDITED).aggregate(
        value=Sum("amount"))["value"] or Decimal("0.00")
    debits = account.bill_fundings.aggregate(
        value=Sum("transaction__amount"))["value"] or Decimal("0.00")
    releases = BillFundingRefund.objects.filter(binding__vas_account=account).aggregate(
        value=Sum("binding__transaction__amount"))["value"] or Decimal("0.00")
    return credits - debits + releases


def reconciliation_scope():
    """Retired bank assets remain unverified until independently reconciled.

    Internal receipts establish liabilities, never the bank's held balance.
    Keeping the historical account in scope prevents a product removal from
    turning missing bank evidence into a false complete settlement position.
    """
    count = VirtualAccount.objects.filter(mode=VirtualAccount.LIVE).count()
    return {"legacy_funding_accounts": count, "legacy_funding_required": bool(count),
            "legacy_funding_balance": None, "legacy_funding_verified": False if count else None}
