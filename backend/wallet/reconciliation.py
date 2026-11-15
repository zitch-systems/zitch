"""Durable per-account partner-bank statement recovery."""
import logging
from datetime import timedelta
from time import monotonic

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from utility import wema
from wallet.models import BankHistoryCheckpoint, ReversalEvidence, Transaction, Wallet
from wallet.services import (
    _reversal_ledger_reference, _reversal_provider_hash,
    apply_wema_credit, self_payout_references, wema_provisioned_wallets,
)

log = logging.getLogger("wallet")


def _credit_accounted_for(wallet, row, norm):
    """A successful observation must survive outside the polling date window."""
    ledger = Transaction.objects.filter(
        reference=_reversal_ledger_reference(norm["reference"])).first()
    if (ledger is not None and ledger.user_id == wallet.user_id
            and ledger.direction == Transaction.IN and ledger.currency == "NGN"
            and ledger.amount == norm["amount_naira"]
            and ledger.transaction_status == Transaction.SUCCESS):
        return True
    # Zitch's own settlement transfers back an existing balance; they are
    # matched, never credited, and must not hold history coverage open.
    from .models import SettlementFunding
    if SettlementFunding.objects.filter(
            wallet=wallet, provider_reference=norm["reference"],
            status=SettlementFunding.MATCHED).exists():
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


def history_credit_opening_review_required(wallet, row, norm=None, *, baseline=None):
    """A new historical receipt is not proof of this wallet's opening balance.

    Shared by notification ingestion and the scheduled history sweep. Previously
    verified credits/reversal evidence keep their idempotent replay path; dates
    missing from a repeat observation must not erase that durable evidence.
    """
    norm = norm if norm is not None else wema.normalize_transaction(row)
    amount = norm["amount_naira"]
    if (not norm["is_credit"] or not norm["settled"]
            or amount is None or amount <= 0 or _credit_accounted_for(wallet, row, norm)):
        return False
    bank_date = _credit_date(row)
    baseline = baseline if baseline is not None else timezone.localdate(wallet.created)
    return bank_date is None or bank_date < baseline


def reconcile_recent_credits(wallet, start, end):
    """Post confirmed recent credits without changing statement coverage."""
    account = wallet.account_number
    result = wema.get_transactions(account, start.isoformat(), end.isoformat())
    if not isinstance(result, dict):
        return 0, "history_unavailable"
    rows = result.get("transactions")
    if (not result.get("success") or result.get("mock") or result.get("complete") is False
            or not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows)):
        status = (result.get("diagnostic") or {}).get("http_status")
        return 0, "history_rate_limited" if status == 429 else "history_unavailable"

    applied = 0
    opening_review = False
    if not rows:
        return applied, ""
    if not Wallet.objects.filter(pk=wallet.pk, account_number=account).exists():
        return 0, "account_changed"
    refs = self_payout_references(wallet.user)
    for row in rows:
        if history_credit_opening_review_required(wallet, row):
            opening_review = True
            continue
        if apply_wema_credit(wallet, row, self_refs=refs) is not None:
            applied += 1
    return applied, "history_opening_review" if opening_review else ""


#: Set while every statement reader (the worker poll, balance requests) should
#: leave the bank alone. deposit_sync reads the same key.
STATEMENT_BACKOFF_KEY = "wema:recent-credit:backoff"
_STATEMENT_FAILURES_KEY = "wema:recent-credit:failures"
#: Errors that mean the bank refused the read or gave no readable answer.
_BANK_READ_FAILURES = frozenset({"history_unavailable", "history_rate_limited"})
_BACKOFF_FIRST_SECONDS = 60
_BACKOFF_MAX_SECONDS = 900


def note_statement_read(error):
    """Back every statement reader off after a failed read, longer each time.

    Only a 429 used to back off, for 60 seconds. On 14 November 2026 the
    gateway answered every call with something unreadable from 15:05 WAT, and
    the worker kept asking for the same statement every five seconds for
    hours. Calling a failing partner at that rate can only keep it failing.

    Each failed read doubles the pause, from a minute up to fifteen. A read
    that succeeds clears it. Errors about our own data (an account that
    changed, a history review) are not the bank's and change nothing here.
    """
    try:
        if error in _BANK_READ_FAILURES:
            failures = int(cache.get(_STATEMENT_FAILURES_KEY) or 0) + 1
            cache.set(_STATEMENT_FAILURES_KEY, failures, timeout=3600)
            pause = min(_BACKOFF_MAX_SECONDS, _BACKOFF_FIRST_SECONDS * 2 ** min(failures - 1, 8))
            cache.set(STATEMENT_BACKOFF_KEY, True, timeout=pause)
        elif not error:
            cache.delete(_STATEMENT_FAILURES_KEY)
    except Exception:  # noqa: BLE001 - the next read simply asks again
        pass


#: The poll's throttle and cursor when the shared cache cannot be reached (one
#: worker instance runs the poll), so a cache outage slows nothing down.
_poll_local = {"next_at": 0.0, "cursor": 0}


def _poll_due(interval_seconds) -> bool:
    try:
        return not cache.get(STATEMENT_BACKOFF_KEY) and bool(
            cache.add("wema:recent-credit:poll", True, timeout=interval_seconds))
    except Exception:  # noqa: BLE001 - a cache outage must not stop deposits
        now = monotonic()
        if now < _poll_local["next_at"]:
            return False
        _poll_local["next_at"] = now + max(1, int(interval_seconds or 1))
        log.warning("wema_recent_credit_poll_cache_unavailable")
        return True


def _poll_cursor(value=None):
    if value is not None:
        _poll_local["cursor"] = value
    try:
        if value is None:
            return cache.get("wema:recent-credit:cursor", 0)
        cache.set("wema:recent-credit:cursor", value, timeout=None)
    except Exception:  # noqa: BLE001 - fall back to this process's own cursor
        pass
    return _poll_local["cursor"]


def _cache_add(key, timeout) -> bool:
    try:
        return bool(cache.add(key, True, timeout=timeout))
    except Exception:  # noqa: BLE001 - without the cache there is no dedupe; stay quiet
        return False


def poll_recent_wema_credits(interval_seconds):
    """Read one account per worker interval across instances, in wallet order.

    The throttle and the cursor live in the shared cache. When it is down this
    keeps polling on its own clock instead of stopping: with every cache call
    raising, no account was read at all, and a deposit waited for the cron.
    """
    if not _poll_due(interval_seconds):
        return
    wallets = wema_provisioned_wallets().select_related("user").order_by("pk")
    cursor = _poll_cursor() or 0
    wallet = wallets.filter(pk__gt=cursor).first() or wallets.first()
    if wallet is None:
        return
    _poll_cursor(wallet.pk)
    today = timezone.localdate()
    started = monotonic()
    applied, error = reconcile_recent_credits(wallet, today - timedelta(days=1), today)
    note_statement_read(error)
    if error and _cache_add(f"wema:recent-credit:error:{wallet.pk}", 300):
        from utility.alerts import alert

        alert("Wema recent credit check could not confirm account history", level="warning",
              wallet_id=wallet.pk, reason=error)
    if applied:
        log.info("wema_recent_credit_posted wallet=%s credits=%s elapsed_ms=%d",
                 wallet.pk, applied, (monotonic() - started) * 1000)
        from utility.alerts import alert
        from whatsapp.models import WebhookEvent

        if (not WebhookEvent.objects.filter(source="wema.notify", verified=True,
                outcome=WebhookEvent.ACCEPTED, created__gte=timezone.now() - timedelta(minutes=10)).exists()
                and _cache_add("wema:recent-credit:notification-missing", 3600)):
            alert("Wema statement recovered a deposit with no recent accepted notification",
                  level="warning", wallet_id=wallet.pk)


def history_window_digest(result, rows):
    """What one statement read covered, with no customer data: how `to` was
    spelled, how many rows and credits came back and the newest row's date.
    "newest" a day behind the calendar means today's rows are not being returned.
    """
    dates = [d for d in (_credit_date(row) for row in rows) if d is not None]
    return {
        "to_form": result.get("to_form") or "as-given",
        "rows": len(rows),
        "credits": sum(1 for row in rows
                       if str(row.get("creditType") or "").strip().casefold() == "credit"),
        "newest": max(dates).isoformat() if dates else "none",
    }


def reconcile_account_history(wallet, *, today=None, overlap_days=2, on_shape=None,
                              on_window=None):
    """Recover missing days in bounded windows, advancing only durable coverage.

    No database lock spans a bank request. Repeated windows and crash retries use
    the ledger's existing provider-reference guard, so applied rows stay once-only.
    """
    from wallet.partnership import retired_funding_destination
    if retired_funding_destination(wallet.account_number):
        return {"credited": 0, "windows": 0, "error_code": "retired_funding_account",
                "fetch_failed": False, "backlog": False, "diagnostic": {}}
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
    if (today - start).days >= window_days:
        # A long backlog - a new account number on an older wallet, or a long
        # outage - is read oldest first, a window at a time, up to max_windows a
        # run: a deposit made today waited for the fourth run or later. Read the
        # last two days first. The ledger's reference guard makes the later
        # backfill of the same rows a no-op, and coverage is not touched here.
        recent, _recent_error = reconcile_recent_credits(
            wallet, today - timedelta(days=1), today)
        credited += recent
    # The first window holding a row that could not be settled yet (a Pending
    # deposit, a row without full evidence). Coverage never moves past it, so the
    # row is re-read every run until it settles. Later windows are still read and
    # their confirmed deposits applied: stopping at the stuck window, as this
    # did, left the cron blind to every newer deposit on the account for as long
    # as the one row stayed unsettled.
    stuck = False
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
        if on_window:
            on_window(history_window_digest(result, rows))
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
            if history_credit_opening_review_required(wallet, row, norm, baseline=baseline):
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
                log.exception("wema_history_apply_failed wallet=%s", wallet.pk)
                window_error = "history_apply_failed"
        if window_error:
            error_code = error_code or window_error
            if window_error == "history_account_changed":
                break
            stuck = True
            start = end + timedelta(days=1)
            continue
        if stuck:
            start = end + timedelta(days=1)
            continue
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
