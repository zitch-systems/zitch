"""One scheduler with durable periods and PostgreSQL process locks."""
import hashlib
import io
from contextlib import contextmanager
from datetime import timedelta, timezone as utc

from django.core.management import call_command
from django.db import connection, transaction
from django.db.models import F
from django.utils import timezone

from .models import MaintenanceRun

# Existing daily checks keep their UTC schedules; each failure is isolated.
JOBS = [
    ("reconcile_9psb", None, {}),
    ("run_maturities", (1, 5), {}),
    ("integrity_check", (2, 35), {"fail_nonzero": True}),
    ("purge_expired_sessions", (3, 45), {}),
    ("aml_scan", (5, 5), {"fail_on_overdue": True}),
    ("settlement_report", (6, 15), {"fail_on_breach": True}),
    ("reconcile_balances", "six_hours", {"fail_nonzero": True}),
]


@contextmanager
def process_lock(name):
    if connection.vendor != "postgresql":
        yield True  # Unit tests use the durable CAS below; production uses PG.
        return
    key = int.from_bytes(hashlib.sha256(("zitch-maintenance:" + name).encode()).digest()[:8], "big", signed=True)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [key])
        acquired = cursor.fetchone()[0]
    try:
        yield acquired
    finally:
        if acquired:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [key])


def period_for(schedule, now):
    now = now.astimezone(utc.utc)
    if schedule is None:
        return now.strftime("%Y-%m-%dT%H:") + str(now.minute // 5)
    if schedule == "six_hours":
        if now.hour % 6 == 0 and now.minute < 5:
            return None
        return now.strftime("%Y-%m-%d:") + str(now.hour // 6)
    if (now.hour, now.minute) < schedule:
        return None
    return now.date().isoformat()


def run_job(name, period, options, *, now=None):
    now = now or timezone.now()
    with process_lock(name) as acquired:
        if not acquired:
            return "busy"
        with transaction.atomic():
            row, created = MaintenanceRun.objects.get_or_create(name=name, period=period,
                defaults={"lease_until": now, "state": "ready"})
            # A failed financial check remains failed and can be inspected. It is
            # retried at most three times per period; it cannot suppress other jobs.
            if row.state == "complete" or row.attempts >= 3:
                return row.state
            claimed = MaintenanceRun.objects.filter(pk=row.pk, lease_until__lte=now).exclude(state="complete").update(
                state="running", lease_until=now+timedelta(minutes=30), attempts=F("attempts")+1)
            if not claimed:
                return "busy"
        try:
            call_command(name, stdout=io.StringIO(), **options)
        except (Exception, SystemExit) as exc:
            # Store only error class; provider messages can include private data.
            MaintenanceRun.objects.filter(pk=row.pk).update(state="failed", last_error=type(exc).__name__,
                lease_until=timezone.now()+timedelta(minutes=10), finished=timezone.now())
            return "failed"
        MaintenanceRun.objects.filter(pk=row.pk).update(state="complete", last_error="", finished=timezone.now())
        return "complete"


def run_due(now=None):
    now = now or timezone.now()
    results = {}
    for name, schedule, options in JOBS:
        period = period_for(schedule, now)
        if period is not None:
            results[name] = run_job(name, period, options, now=now)
    # Operational run markers are not financial evidence. Keep 90 days.
    MaintenanceRun.objects.filter(started__lt=now-timedelta(days=90), state="complete").delete()
    return results
