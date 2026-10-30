"""Read-only bank/ledger checks. Historical assets stay explicitly unverified."""
from decimal import Decimal
import json
import time
from django.db.models import Sum
from banking.accounts import spendable, update_account
from banking.client import ProviderError, config
from banking.models import BankAccount, PreviousBankArchive, HistoricalBalanceAdjustment
from wallet.models import Transaction, Wallet, RetiredFundingArchive, BankHistoryCheckpoint, UnsettledFunding
from wallet.services import wallet_expected_balance, quarantined_bank_payouts, unmatched_reversal_evidence


def total(qs, field):
    return qs.aggregate(value=Sum(field))["value"] or Decimal("0")


def alert_review(kind, result):
    """Preserve operational alerts without repeating one unchanged incident."""
    from utility.alerts import alert
    from utility.reconciliation import alert_due
    if alert_due("9psb-" + kind, [json.dumps(result, sort_keys=True)]):
        alert(f"9PSB {kind}: bank balances or historical liabilities require review. "
              f"Unreachable={result['unreachable']}; ledger over bank={result['ledger_over_bank']}; "
              f"bank over ledger={result['bank_over_ledger']}; pending={result['pending_wallets']}; "
              f"reversal reviews={result['reversal_reviews']}; "
              f"historical review={result['legacy_funding_required']}.", level="error")


def snapshot(*, budget_seconds=60):
    deadline = time.monotonic() + budget_seconds
    archived_funding = total(RetiredFundingArchive.objects.all(), "funding__amount")
    previous = total(PreviousBankArchive.objects.all(), "balance") + total(HistoricalBalanceAdjustment.objects.all(), "amount")
    credits = total(Transaction.objects.filter(direction=Transaction.IN, transaction_status=Transaction.SUCCESS, currency="NGN"), "amount")
    debits = total(Transaction.objects.filter(direction=Transaction.OUT, transaction_status__in=[Transaction.PENDING, Transaction.SUCCESS], currency="NGN"), "amount")
    stored = total(Wallet.objects.all(), "balance") + archived_funding
    liability = credits - debits + archived_funding
    historical_reviews = BankHistoryCheckpoint.objects.filter(opening_review_required=True).count()
    reversal_reviews = quarantined_bank_payouts().count() + unmatched_reversal_evidence().count()
    legacy_required = bool(previous or archived_funding or historical_reviews or reversal_reviews
                           or UnsettledFunding.objects.filter(archive__isnull=True).exists()
                           or PreviousBankArchive.objects.filter(pending_transactions__gt=0).exists())
    checked = unreachable = over = under = pending = opening = 0
    held = Decimal("0")
    banks = BankAccount.objects.filter(environment=config().get("ENVIRONMENT", "sandbox")).select_related("wallet")
    bank_count = banks.count()
    for bank in banks.iterator(chunk_size=100):
        if time.monotonic() >= deadline:
            unreachable += bank_count - checked
            break
        checked += 1
        opening += int(bank.opening_review_required)
        if Transaction.objects.filter(user_id=bank.wallet.user_id, meta__provider="9psb", transaction_status=Transaction.PENDING).exists():
            pending += 1
        try:
            update_account(bank)
        except (ProviderError, ValueError):
            unreachable += 1
            continue
        if bank.ledger_balance is None:
            unreachable += 1
            continue
        held += bank.ledger_balance
        expected = spendable(bank.wallet)
        if expected > bank.ledger_balance:
            over += 1
        elif bank.ledger_balance > expected:
            under += 1
    incomplete = bool(legacy_required or unreachable or opening or pending or over or under or liability != stored)
    return {"wallets": bank_count, "nuban_wallets": bank_count, "checked_wallets": checked, "unreachable": unreachable,
        "ledger_over_bank": over, "bank_over_ledger": under, "pending_wallets": pending,
        "opening_reviews": opening, "historical_opening_reviews": historical_reviews,
        "reversal_reviews": reversal_reviews,
        "tolerance": "0.00", "legacy_funding_required": legacy_required,
        "legacy_funding_verified": False if legacy_required else None,
        "archived_liability": str(previous + archived_funding),
        "ledger_liability": str(liability), "stored_liability": str(stored),
        "known_9psb_assets": str(held), "incomplete": incomplete,
        "held_total": None if incomplete else str(held), "position": None if incomplete else str(held-liability)}
