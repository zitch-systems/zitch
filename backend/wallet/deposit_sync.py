"""Show a customer's new deposit when they ask for their balance.

Wema sends Zitch no deposit notification. A transfer into a customer's account
reaches the ledger only when something reads that account's statement: the
``reconcile_wema`` cron every 10 minutes, and the WhatsApp worker's recent-credit
poll (``reconciliation.poll_recent_wema_credits``), which reads one account at a
time in turn. With many accounts, that poll is rarely reading the account of the
customer who has just sent money and is now asking for their balance.

So a balance request reads that customer's recent statement first, through the
same reader and the same once-only ledger guard as the poll (the bank's
per-transaction reference, credited under a wallet row lock). Reading it here,
there and in the cron at the same moment cannot credit a deposit twice. It also
shares the poll's backoff after the bank answers 429.
"""
import logging
import threading
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import connections
from django.utils import timezone

from utility import wema

log = logging.getLogger("wallet")

#: Set by the worker poll (and here) for 60 seconds after the bank answers 429.
BACKOFF_KEY = "wema:recent-credit:backoff"


def _setting(name, default):
    try:
        return float(getattr(settings, name, default))
    except (TypeError, ValueError):
        return float(default)


def statement_live() -> bool:
    """Whether this process can read account statements (the gate get_transactions uses)."""
    return wema._product_live("acct_mgt")


def _backing_off() -> bool:
    try:
        return bool(cache.get(BACKOFF_KEY))
    except Exception:  # noqa: BLE001 - a cache outage must not stop deposits
        return False


def _claim_account(wallet_pk) -> bool:
    """True for the first balance-request read of this account in the throttle window."""
    try:
        return bool(cache.add(f"zitch:wema-deposit-sync:{wallet_pk}", 1,
                              timeout=int(max(5.0, _setting("WEMA_DEPOSIT_SYNC_MIN_INTERVAL_SECONDS", 15)))))
    except Exception:  # noqa: BLE001 - a cache outage must not stop deposits
        log.warning("wema_deposit_sync_throttle_unavailable wallet=%s", wallet_pk)
        return True


def _read_statement(wallet) -> int:
    """Credit confirmed deposits from yesterday and today. Returns how many."""
    from wallet.reconciliation import reconcile_recent_credits

    today = timezone.localdate()
    credited, error = reconcile_recent_credits(wallet, today - timedelta(days=1), today)
    if error == "history_rate_limited":
        try:
            cache.set(BACKOFF_KEY, True, timeout=60)
        except Exception:  # noqa: BLE001 - the next read simply tries again
            pass
    return credited


#: Reads started by refresh_deposits_briefly in this process, by wallet pk. The
#: app asks for the balance and the transaction list at the same moment; the
#: second request waits on the first one's read instead of answering before it,
#: so the list never lags a balance that already includes a new deposit.
_inflight = {}
_inflight_guard = threading.Lock()


def _customer_account(user):
    from wallet.services import wema_provisioned_wallets

    return wema_provisioned_wallets().select_related("user").filter(user_id=user.pk).first()


def _read_customer_statement(wallet, user_pk) -> int:
    try:
        credited = _read_statement(wallet)
    except Exception:  # noqa: BLE001 - a balance check must not fail on the bank
        log.exception("wema_deposit_sync_failed user=%s", user_pk)
        return 0
    if credited:
        log.info("wema_deposit_sync user=%s credited=%s", user_pk, credited)
    return credited


def sync_wallet_deposits(user) -> int:
    """Read one customer's recent statement now, inline. Returns deposits credited; never raises."""
    try:
        if not statement_live() or _backing_off():
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

    The bank's timeout is 30 seconds. Waiting for that inline would freeze a
    balance reply while the bank is slow. The read runs on its own thread
    instead: if it finishes within ``WEMA_DEPOSIT_SYNC_WAIT_SECONDS`` (5 by
    default) the reply includes whatever it credited; if not, the reply shows the
    balance as it stands and the read carries on. A deposit it finds later still
    gets its alert.

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
        if not statement_live() or _backing_off():
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
