"""Customer balances that no bank account holds, and where each came from.

A live customer's money sits in their own partner-bank account; the settlement
report compares the total of those accounts with the ledger. A wallet with a
balance but no account is money no bank holds: the whole of it is a settlement
shortfall, and no reconciliation run can close it.

New ones are refused at the source (``wallet.services.assert_backed_credit``).
This module explains the ones that already exist, so the alert says what each
is and which reviewed command resolves it, instead of a bare total.

Each balance is split by the ledger credits that built it:

  simulated      simulation-deploy deposits (``WEMA-CR-SIM-``): fake money;
                 ``purge_simulation_data --confirm`` reverses them.
  retired        deposits to a retired provider, held as ``UnsettledFunding``;
                 ``archive_retired_balances`` isolates them.
  manual         operator manual credits.
  local_transfer wallet-to-wallet moves made while the bank rail was mocked.
  product        loan, savings, airtime-to-cash or fee-refund credits.
  other          anything else (reviewed by hand).

Real customer money (anything but ``simulated``) is resolved the same way: the
customer opens their Wema account (WhatsApp reply 6 or the app), then
``prepare_settlement_funding --user-id N --apply`` issues the token for the
exact transfer that puts the money in that account without crediting it twice.
Output carries user ids and amounts only.
"""
from decimal import Decimal

from django.db.models import Q, Sum

from .models import Transaction, UnsettledFunding, Wallet

SIM_CREDIT_PREFIX = "WEMA-CR-SIM-"
SIM_REVERSAL_PREFIX = "SIMREV-"

_PRODUCT_SERVICES = ("Loan disbursed", "Fixed Save matured", "Airtime → Cash", "Transfer fee refund")


def _origin(row, retired_ids) -> str:
    service = str(row.service or "")
    reference = str(row.reference or "")
    if reference.startswith(SIM_CREDIT_PREFIX):
        return "simulated"
    if row.pk in retired_ids:
        return "retired"
    if service.startswith("Manual credit"):
        return "manual"
    if service.startswith("Transfer from") and reference.startswith("ZTRF"):
        return "local_transfer"
    if service.startswith(_PRODUCT_SERVICES):
        return "product"
    return "other"


def _retired_credit_ids(user_id) -> set:
    ids = set()
    for evidence in UnsettledFunding.objects.filter(user_id=user_id).values_list("evidence", flat=True):
        for row in (evidence or {}).get("credits", []) if isinstance(evidence, dict) else []:
            if isinstance(row, dict) and row.get("transaction_id"):
                ids.add(row["transaction_id"])
    return ids


def next_step(origins: dict) -> str:
    """The reviewed command that resolves this wallet."""
    real = {k: v for k, v in origins.items() if k != "simulated" and v > 0}
    if not real:
        return "simulation only: python manage.py purge_simulation_data --confirm"
    if set(real) == {"retired"}:
        return "retired provider: python manage.py archive_retired_balances (see docs/retired-balance-archive.md)"
    return ("customer money: customer opens their Wema account (reply 6), then "
            "python manage.py prepare_settlement_funding --user-id <id> --apply")


def unbacked_wallets() -> list[dict]:
    """Every wallet holding money with no partner-bank account behind it.

    The balance is attributed to its most recent credits first (money spent is
    the oldest money), so ``origins`` sums to the balance.
    """
    wallets = Wallet.objects.filter(balance__gt=0).filter(
        Q(account_number="") | Q(account_number__isnull=True)).order_by("user_id")
    out = []
    for wallet in wallets:
        retired_ids = _retired_credit_ids(wallet.user_id)
        reversed_refs = set(Transaction.objects.filter(
            user_id=wallet.user_id, reference__startswith=SIM_REVERSAL_PREFIX,
        ).values_list("reference", flat=True))
        credits = (Transaction.objects.filter(
            user_id=wallet.user_id, direction=Transaction.IN,
            transaction_status=Transaction.SUCCESS, currency="NGN")
            .only("pk", "amount", "service", "reference").order_by("-created", "-pk"))
        left = wallet.balance
        origins: dict[str, Decimal] = {}
        for row in credits:
            if left <= 0:
                break
            if SIM_REVERSAL_PREFIX + str(row.reference or "") in reversed_refs:
                continue
            take = min(left, row.amount)
            kind = _origin(row, retired_ids)
            origins[kind] = origins.get(kind, Decimal("0")) + take
            left -= take
        if left > 0:
            origins["other"] = origins.get("other", Decimal("0")) + left
        out.append({"user_id": wallet.user_id, "balance": wallet.balance,
                    "origins": origins, "next_step": next_step(origins)})
    return out


def unbacked_total() -> tuple[int, Decimal]:
    row = Wallet.objects.filter(balance__gt=0).filter(
        Q(account_number="") | Q(account_number__isnull=True)).aggregate(total=Sum("balance"))
    count = Wallet.objects.filter(balance__gt=0).filter(
        Q(account_number="") | Q(account_number__isnull=True)).count()
    return count, row["total"] or Decimal("0")


def summary_line(entry: dict) -> str:
    parts = ", ".join(f"{k} {v:.2f}" for k, v in sorted(entry["origins"].items()))
    return f"user {entry['user_id']}: {entry['balance']:.2f} ({parts}) -> {entry['next_step']}"
