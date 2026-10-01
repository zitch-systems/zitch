"""Durable per-account partner-bank statement recovery."""
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from utility import wema
from wallet.models import BankHistoryCheckpoint, ReversalEvidence, Transaction, Wallet
from wallet.services import (
    _reversal_ledger_reference, _reversal_provider_hash,
    apply_wema_credit, self_payout_references,
)


def _credit_accounted_for(wallet, row, norm):
    """A successful observation must survive outside the polling date window."""
    ledger = Transaction.objects.filter(
        reference=_reversal_ledger_reference(norm["reference"])).first()
    if (ledger is not None and ledger.user_id == wallet.user_id
            and ledger.direction == Transaction.IN and ledger.currency == "NGN"
            and ledger.amount == norm["amount_naira"]
            and ledger.transaction_status == Transaction.SUCCESS):
        return True
    # Returned payments can be refunded or quarantined rather than credited.
    # Their independent evidence queue remains live after history coverage moves.
    return ReversalEvidence.objects.filter(
        provider=ReversalEvidence.WEMA,
        provider_reference_hash=_reversal_provider_hash(norm["reference"]),
        user_id=wallet.user_id,
    ).exists()


def _credit_date(row):
    """ALAT's date field has calendar precision in its documented examples."""
    value = str(row.get("date") or "").strip()
    if not value:
        return None
    try:
        dt = parse_datetime(value)
        if dt is not None:
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt)
            return timezone.localdate(dt)
        return parse_date(value)
    except (TypeError, ValueError):
        return None


def reconcile_account_history(wallet, *, today=None, overlap_days=2, on_shape=None):
    """Recover missing days in bounded windows, advancing only durable coverage.

    No database lock spans a bank request. Repeated windows and crash retries use
    the ledger's existing provider-reference guard, so applied rows stay once-only.
    """
    today = today or timezone.localdate()
    overlap_days = max(1, int(overlap_days))
    window_days = max(1, min(31, int(getattr(settings, "WEMA_HISTORY_WINDOW_DAYS", 7) or 7)))
    max_windows = max(1, min(100, int(getattr(settings, "WEMA_HISTORY_MAX_WINDOWS", 8) or 8)))
    account_number = wallet.account_number
    checkpoint, _ = BankHistoryCheckpoint.objects.get_or_create(
        wallet=wallet, account_number=account_number)
    baseline = timezone.localdate(wallet.created)
    start = (max(baseline, checkpoint.covered_through - timedelta(days=overlap_days))
             if checkpoint.covered_through else baseline)
    refs = self_payout_references(wallet.user)
    credited = 0
    windows = 0
    error_code = ""
    diagnostic = {}
    fetch_failed = False
    while start <= today and windows < max_windows:
        end = min(today, start + timedelta(days=window_days - 1))
        result = wema.get_transactions(account_number, start.isoformat(), end.isoformat())
        windows += 1
        if not isinstance(result, dict):
            error_code = "malformed_history"
            fetch_failed = True
            break
        rows = result.get("transactions")
        if (not result.get("success") or result.get("mock")
                or result.get("complete") is False
                or not isinstance(rows, list)
                or any(not isinstance(row, dict) for row in rows)):
            error_code = result.get("error_code") or "history_fetch_failed"
            fetch_failed = True
            diagnostic = result.get("diagnostic") or {}
            break
        if on_shape and rows:
            on_shape(rows[0])
        window_error = ""
        # Apply every confirmed row, even when another row needs retry. Coverage
        # stays pinned so old Pending/incomplete deposits are still re-observed.
        for row in rows:
            direction = str(row.get("creditType") or "").strip().casefold()
            if direction == "debit":
                continue
            if direction != "credit":
                window_error = "history_row_unrecognized"
                continue
            norm = wema.normalize_transaction(row)
            if norm["status"] in {"failed", "reversed"}:
                continue
            if not norm["reference"] or norm["amount_naira"] is None:
                window_error = "history_credit_invalid"
                continue
            if norm["amount_naira"] == 0:
                continue
            if norm["amount_naira"] < 0 or not norm["settled"]:
                window_error = "history_credit_unsettled"
                continue
            if not _credit_accounted_for(wallet, row, norm):
                bank_date = _credit_date(row)
                if bank_date is None or bank_date < baseline:
                    # A gross historical receipt is not an opening balance; it
                    # may already have been spent before the platform existed.
                    window_error = "history_credit_opening_review"
                    continue
            if not Wallet.objects.filter(pk=wallet.pk, account_number=account_number).exists():
                window_error = "history_account_changed"
                break
            try:
                if apply_wema_credit(wallet, row, self_refs=refs) is not None:
                    credited += 1
                if not _credit_accounted_for(wallet, row, norm):
                    window_error = "history_credit_not_accounted"
            except Exception:  # A single account must not stop other settlements.
                import logging

                logging.getLogger("wallet").exception(
                    "wema_history_apply_failed wallet=%s", wallet.pk)
                window_error = "history_apply_failed"
        if window_error:
            error_code = window_error
            break
        with transaction.atomic():
            locked_wallet = Wallet.objects.select_for_update().get(pk=wallet.pk)
            locked = BankHistoryCheckpoint.objects.select_for_update().get(pk=checkpoint.pk)
            if locked_wallet.account_number != account_number:
                error_code = "history_account_changed"
                break
            if locked.covered_through is None or end > locked.covered_through:
                locked.covered_through = end
            locked.last_completed_at = timezone.now()
            locked.last_error_code = (
                "history_opening_review" if locked.opening_review_required else "")
            locked.save(update_fields=["covered_through", "last_completed_at", "last_error_code", "updated"])
            checkpoint.covered_through = locked.covered_through
        start = end + timedelta(days=1)
    backlog = not error_code and start <= today
    if not error_code and checkpoint.opening_review_required:
        error_code = "history_opening_review"
    if error_code or backlog:
        BankHistoryCheckpoint.objects.filter(pk=checkpoint.pk).update(
            last_error_code=error_code or "history_backlog", updated=timezone.now())
    return {"credited": credited, "windows": windows, "error_code": error_code,
            "fetch_failed": fetch_failed,
            "backlog": backlog, "diagnostic": diagnostic}
