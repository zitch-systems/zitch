"""The services watch each other, so a stopped one is reported, not discovered.

- The WhatsApp worker writes a heartbeat every 30 seconds. The scheduler cron
  checks it on every run: no heartbeat for 5 minutes means chat replies and
  transaction alerts have stopped.
- The worker checks the scheduler: no completed reconcile_wema run for 20
  minutes means the deposit and payout backstop has stopped.
- The scheduler checks the alert outbox: an alert that has waited 5 minutes for
  its first send (the worker picks them up within a second) is stuck.

Each check alerts through utility.alerts (log, email, Sentry) and never raises.
"""
import logging
import time
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

log = logging.getLogger("zitch")

WORKER_HEARTBEAT_KEY = "ops:heartbeat:whatsapp-worker"
WORKER_SILENT_AFTER = 300          # seconds
SCHEDULER_SILENT_AFTER = 20 * 60   # seconds
OUTBOX_STUCK_AFTER = 300           # seconds


def beat_worker() -> None:
    try:
        cache.set(WORKER_HEARTBEAT_KEY, time.time(), timeout=7 * 24 * 3600)
    except Exception:  # noqa: BLE001 - the heartbeat must never stop the worker
        pass


def check_worker(now=None) -> bool:
    """Alert when the worker's heartbeat is stale. True when it is healthy."""
    from utility.alerts import alert

    try:
        last = cache.get(WORKER_HEARTBEAT_KEY)
    except Exception:  # noqa: BLE001 - cache down: nothing to judge by
        return True
    if last is None:
        return True    # never seen since the cache was last emptied; not proof of an outage
    silent = (now or time.time()) - float(last)
    if silent < WORKER_SILENT_AFTER:
        return True
    alert("The WhatsApp worker has stopped reporting: chat replies and transaction "
          "alerts are not being sent", level="error", minutes_silent=int(silent // 60))
    return False


def check_scheduler(now=None) -> bool:
    """Alert when the scheduler cron has stopped completing runs."""
    from utility.alerts import alert
    from utility.models import ScheduledJobRun

    try:
        latest = (ScheduledJobRun.objects
                  .filter(job="reconcile_wema", status=ScheduledJobRun.SUCCEEDED)
                  .order_by("-finished").values_list("finished", flat=True).first())
    except Exception:  # noqa: BLE001 - the database check runs again next time
        log.warning("watchdog_scheduler_check_failed")
        return True
    if latest is None:
        return True    # the scheduler cron is not in use yet
    silent = ((now or timezone.now()) - latest).total_seconds()
    if silent < SCHEDULER_SILENT_AFTER:
        return True
    alert("The scheduler cron has not completed a run: deposits, pending payouts and "
          "the daily checks rely on the worker alone", level="error",
          minutes_silent=int(silent // 60))
    return False


def check_alert_outbox(now=None) -> bool:
    """Alert when transaction alerts are waiting for their first send."""
    from utility.alerts import alert
    from wallet.models import TransactionAlertDelivery as Delivery

    cutoff = (now or timezone.now()) - timedelta(seconds=OUTBOX_STUCK_AFTER)
    try:
        stuck = Delivery.objects.filter(state=Delivery.READY, created__lt=cutoff)
        count = stuck.count()
        oldest = stuck.order_by("created").values_list("created", flat=True).first()
    except Exception:  # noqa: BLE001
        log.warning("watchdog_outbox_check_failed")
        return True
    if not count:
        return True
    alert("Transaction alerts are waiting to be sent: the worker is not dispatching them",
          level="error", waiting=count,
          oldest_minutes=int(((now or timezone.now()) - oldest).total_seconds() // 60))
    return False
