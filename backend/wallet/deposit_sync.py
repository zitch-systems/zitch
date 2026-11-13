"""Find partner-bank deposits between scheduled reconciliations.

Wema sends Zitch no deposit notification. A transfer into a customer's account
reaches the ledger only when something reads that account's statement. The
``reconcile_wema`` cron does that for every account every 10 minutes. A Render
cron cannot run more often than once a minute, and each run boots a fresh
machine before it does any work. So, until now, a customer could wait up to ten
minutes to see a deposit, and longer when a run was slow.

This module adds two faster readers. Both use the same statement reader as the
cron (``reconcile_account_history``) and the same once-only ledger guard (the
bank's per-transaction reference). Running either alongside the cron cannot
credit a deposit twice:

- ``poll_deposits`` runs inside the always-on WhatsApp worker every
  ``WEMA_DEPOSIT_POLL_SECONDS`` (30 by default). It reads deposits only: no
  payouts, bill settlement or account repair, which stay with the cron.
- ``refresh_deposits_briefly`` reads one customer's statement when they ask for
  their balance, so a transfer they have just made is there when they look.

A per-account throttle keeps the two from asking the bank about the same
account more than once every ``WEMA_DEPOSIT_SYNC_MIN_INTERVAL_SECONDS``.
"""
import logging
import secrets
import threading
import time

from django.conf import settings
from django.core.cache import cache
from django.db import connections
from django.db.models import F, OuterRef, Subquery

from utility import wema

log = logging.getLogger("wallet")

POLL_LOCK_KEY = "zitch:wema-deposit-poll:lock"
_NOT_LIVE_LOG_EVERY = 3600.0
_FAILURE_LOG_EVERY = 600.0
_logged_at = {}
_log_guard = threading.Lock()


def _setting(name, default):
    try:
        return float(getattr(settings, name, default))
    except (TypeError, ValueError):
        return float(default)


def _min_interval() -> float:
    return max(5.0, _setting("WEMA_DEPOSIT_SYNC_MIN_INTERVAL_SECONDS", 15))


def statement_live() -> bool:
    """Whether this process can read account statements (the gate get_transactions uses)."""
    return wema._product_live("acct_mgt")


def _due(kind: str, every: float) -> bool:
    """Rate-limit a repeating log line: a 30-second loop must not flood the logs.

    Wall clock, deliberately: worker tests patch time.monotonic process-wide
    with a fixed sequence for the loop, and a log throttle needs no more.
    """
    with _log_guard:
        now = time.time()
        last = _logged_at.get(kind)
        if last is not None and now - last < every:
            return False
        _logged_at[kind] = now
        return True


def _note_not_live() -> None:
    """Say once an hour, not every pass, why this process cannot see deposits."""
    if _due("not_live", _NOT_LIVE_LOG_EVERY):
        log.warning("wema_deposit_poll_not_live reasons=%s",
                    ",".join(wema.not_live_reasons()) or "account_statement_key_missing")


def _claim_account(wallet_pk) -> bool:
    """True for the first reader of this account in the throttle window."""
    try:
        return bool(cache.add(f"zitch:wema-deposit-sync:{wallet_pk}", 1,
                              timeout=int(_min_interval())))
    except Exception:  # noqa: BLE001 - a cache outage must not stop deposits
        log.warning("wema_deposit_sync_throttle_unavailable wallet=%s", wallet_pk)
        return True


def _read_statement(wallet) -> dict:
    from wallet.reconciliation import reconcile_account_history

    return reconcile_account_history(wallet, overlap_days=2)


def _accounts():
    from wallet.models import BankHistoryCheckpoint
    from wallet.services import wema_provisioned_wallets

    last_read = (BankHistoryCheckpoint.objects
                 .filter(wallet=OuterRef("pk"), account_number=OuterRef("account_number"))
                 .values("last_completed_at")[:1])
    return (wema_provisioned_wallets()
            .annotate(deposit_last_read=Subquery(last_read))
            .order_by(F("deposit_last_read").asc(nulls_first=True), "pk"))


def poll_deposits(*, limit=None) -> dict:
    """One bounded deposits-only pass over partner-bank accounts, stalest first.

    Returns counts for the caller to log. Never raises for one account: a bank
    error is recorded on that account's checkpoint exactly as the cron records
    it, and the next pass (or the cron) retries.
    """
    summary = {"checked": 0, "credited": 0, "failed": 0, "skipped": False}
    if not statement_live():
        _note_not_live()
        summary["skipped"] = True
        return summary
    interval = max(10.0, _setting("WEMA_DEPOSIT_POLL_SECONDS", 30))
    token = secrets.token_urlsafe(16)
    try:
        if not cache.add(POLL_LOCK_KEY, token, timeout=int(interval * 4)):
            summary["skipped"] = True
            return summary
    except Exception:  # noqa: BLE001 - without the lock, still read statements
        token = None
    try:
        cap = int(limit if limit is not None
                  else _setting("WEMA_DEPOSIT_POLL_MAX_WALLETS", 50))
        for wallet in _accounts()[:max(1, min(cap, 500))]:
            if not _claim_account(wallet.pk):
                continue
            summary["checked"] += 1
            try:
                result = _read_statement(wallet)
            except Exception:  # noqa: BLE001 - one account must not stop the others
                summary["failed"] += 1
                log.exception("wema_deposit_poll_failed wallet=%s", wallet.pk)
                continue
            summary["credited"] += int(result.get("credited") or 0)
            # Opening review and backlog are states the cron already reports;
            # only a statement the bank would not give us is a failure here.
            if result.get("fetch_failed"):
                summary["failed"] += 1
    finally:
        if token is not None:
            try:
                if cache.get(POLL_LOCK_KEY) == token:
                    cache.delete(POLL_LOCK_KEY)
            except Exception:  # noqa: BLE001 - the lock expires on its own
                pass
    if summary["credited"] or (summary["failed"] and _due("failed", _FAILURE_LOG_EVERY)):
        log.info("wema_deposit_poll checked=%s credited=%s failed=%s",
                 summary["checked"], summary["credited"], summary["failed"])
    return summary


#: Reads started by refresh_deposits_briefly in this process, by wallet pk. The
#: app asks for the balance and the transaction list at the same moment; the
#: second request waits on the first one's read instead of answering before it,
#: so the list never lags a balance that already includes a new deposit.
_inflight = {}
_inflight_guard = threading.Lock()


def _customer_account(user):
    from wallet.services import wema_provisioned_wallets

    return wema_provisioned_wallets().filter(user_id=user.pk).first()


def _read_customer_statement(wallet, user_pk) -> int:
    try:
        result = _read_statement(wallet)
    except Exception:  # noqa: BLE001 - a balance check must not fail on the bank
        log.exception("wema_deposit_sync_failed user=%s", user_pk)
        return 0
    credited = int(result.get("credited") or 0)
    if credited:
        log.info("wema_deposit_sync user=%s credited=%s", user_pk, credited)
    return credited


def sync_wallet_deposits(user) -> int:
    """Read one customer's statement now, inline. Returns deposits credited; never raises."""
    try:
        if not statement_live():
            return 0
        wallet = _customer_account(user)
        if wallet is None or not _claim_account(wallet.pk):
            return 0
    except Exception:  # noqa: BLE001 - a balance check must not fail on the bank
        log.exception("wema_deposit_sync_failed user=%s", getattr(user, "pk", None))
        return 0
    return _read_customer_statement(wallet, user.pk)


def refresh_deposits_briefly(user, wait_seconds=None):
    """Check the customer's statement, but hold their reply for a few seconds at most.

    The bank's timeout is 30 seconds, and an empty statement costs a second call.
    Waiting for that inline would freeze a balance reply for up to a minute while
    the bank is slow. The read runs on its own thread instead: if it finishes
    within ``WEMA_DEPOSIT_SYNC_WAIT_SECONDS`` (5 by default) the reply includes
    whatever it credited; if not, the reply shows the balance as it stands and
    the read carries on. A deposit it finds later still gets its alert.

    Returns the number credited, or None while the read is still running (or
    when ``WEMA_DEPOSIT_SYNC_WAIT_SECONDS`` is 0, which turns this off).
    """
    wait = _setting("WEMA_DEPOSIT_SYNC_WAIT_SECONDS", 5) if wait_seconds is None else float(wait_seconds)
    if getattr(settings, "TESTING", False) and wait_seconds is None:
        # Deterministic in tests: the thread below would read through its own
        # database connection, outside the test's transaction.
        return sync_wallet_deposits(user)
    if wait <= 0:
        return None
    try:
        if not statement_live():
            return 0
        wallet = _customer_account(user)
    except Exception:  # noqa: BLE001 - a balance check must not fail on the bank
        log.exception("wema_deposit_sync_failed user=%s", getattr(user, "pk", None))
        return 0
    if wallet is None:
        return 0
    start = False
    with _inflight_guard:
        pending = _inflight.get(wallet.pk)
        if pending is None:
            if not _claim_account(wallet.pk):
                return 0  # read moments ago, or being read by another process
            pending = {"done": threading.Event(), "credited": None}
            _inflight[wallet.pk] = pending
            start = True
    if start:
        def run():
            try:
                pending["credited"] = _read_customer_statement(wallet, user.pk)
            finally:
                with _inflight_guard:
                    _inflight.pop(wallet.pk, None)
                pending["done"].set()
                connections.close_all()

        threading.Thread(target=run, name="wema-deposit-refresh", daemon=True).start()
    pending["done"].wait(wait)
    return pending["credited"]
