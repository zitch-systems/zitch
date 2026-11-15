"""Every periodic job, run by one cron: ``python manage.py run_schedule``.

Seven cron services each started a container, loaded Django and ran one
command. They now share one cron that runs every five or ten minutes:

- ``reconcile_wema`` (deposits, payouts and purchases left pending) on every run;
- the daily and six-hourly jobs once each per slot, at their old times.

A slot is claimed in the database (``ScheduledJobRun``), so a slot the cron
missed is run on its next run, a job that crashed is tried again on the next
run, and two overlapping runs never start the same slot twice. Each job runs
in its own process with a time limit, so a crash, a hang or a memory spike in
one job cannot stop the others.

A finding is not a failure. The checks (integrity, balances, settlement, AML)
used to exit 1 when they found a discrepancy, so their cron showed as failed
until a person fixed the books, and a real crash looked the same as a finding.
Their findings already go through ``utility.alerts.alert``, which emails the
operator (``OPS_ALERT_EMAILS``) and Sentry. So they now run without their
``--fail-*`` flags and the cron run stays green. Only when no operator channel
is configured do they run as before, with the flags, and a finding fails the
run: a red cron is then the only alarm anyone would see.
"""
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path

from django.conf import settings
from django.db import transaction

#: The exit code recorded for a job that ran out of time and was killed.
TIMED_OUT = 124
#: The exit code recorded when the job's process could not be started.
NOT_STARTED = 127
#: Run records older than this are deleted.
KEEP_DAYS = 35


@dataclass(frozen=True)
class Job:
    name: str
    #: The manage.py command and its arguments.
    command: tuple
    #: Run in every slot of this many minutes (the job runs on every cron run).
    every_minutes: int = 0
    #: Or once a day at each of these UTC (hour, minute) times.
    at: tuple = ()
    #: Seconds before the job is killed and recorded as timed out.
    timeout: int = 900
    #: Flags that make the command exit 1 on a finding. Passed only when no
    #: operator channel is configured, so the cron run's failure is the alarm.
    alarm_flags: tuple = ()
    #: An environment switch with the same effect, for a command whose flag
    #: defaults from settings: "true" with the alarm, "false" without it.
    alarm_env: str = ""
    #: Runs per slot before the job waits for its next slot.
    attempts: int = 6


#: In the order they run. reconcile_wema is first so a slow daily report can
#: never hold up deposits. The daily times are the old crons' times.
JOBS = (
    Job("reconcile_wema", ("reconcile_wema",), every_minutes=5, timeout=480, attempts=1,
        alarm_env="WEMA_RECONCILE_FAIL_INCOMPLETE"),
    Job("run_maturities", ("run_maturities",), at=((1, 5),)),
    Job("integrity_check", ("integrity_check",), at=((2, 35),),
        alarm_flags=("--fail-nonzero",)),
    Job("purge_expired_sessions", ("purge_expired_sessions",), at=((3, 45),)),
    Job("aml_scan", ("aml_scan",), at=((5, 5),), alarm_flags=("--fail-on-overdue",)),
    Job("reconcile_balances", ("reconcile_balances",),
        at=((0, 5), (6, 5), (12, 5), (18, 5)), alarm_flags=("--fail-nonzero",)),
    Job("settlement_report", ("settlement_report",), at=((6, 15),),
        alarm_flags=("--fail-on-breach",)),
)


def job_named(name):
    for job in JOBS:
        if job.name == name:
            return job
    return None


def latest_slot(job, now):
    """The most recent time ``job`` was due, at or before ``now`` (UTC)."""
    now = now.astimezone(dt_timezone.utc)
    if job.every_minutes:
        minutes = (now.hour * 60 + now.minute) // job.every_minutes * job.every_minutes
        return now.replace(hour=minutes // 60, minute=minutes % 60, second=0, microsecond=0)
    slots = [
        datetime(day.year, day.month, day.day, hour, minute, tzinfo=dt_timezone.utc)
        for day in (now.date(), now.date() - timedelta(days=1))
        for hour, minute in job.at
    ]
    return max(slot for slot in slots if slot <= now)


def max_attempts(job, alarm):
    # Rerunning a check that failed because it found something finds it again.
    # With the alarm flags on, a failed check is reported once per slot.
    return 1 if alarm and job.alarm_flags else job.attempts


def claim(job, slot, now, *, alarm):
    """Start ``job``'s run for ``slot``, or return None when it is not due.

    Not due: the slot already succeeded, a run of it is still within its time
    limit, or it has used its attempts. A run whose process died without
    finishing (the container was replaced) counts as an attempt once its time
    limit has passed.
    """
    from utility.models import ScheduledJobRun as Run

    stale_before = now - timedelta(seconds=job.timeout + 120)
    with transaction.atomic():
        run, created = Run.objects.select_for_update().get_or_create(
            job=job.name, slot=slot,
            defaults={"status": Run.RUNNING, "attempts": 1, "started": now})
        if created:
            return run
        if run.status == Run.SUCCEEDED:
            return None
        if run.status == Run.RUNNING and run.started and run.started > stale_before:
            return None
        if run.attempts >= max_attempts(job, alarm):
            return None
        run.status = Run.RUNNING
        run.attempts += 1
        run.started = now
        run.finished = None
        run.exit_code = None
        run.elapsed_ms = None
        run.save(update_fields=["status", "attempts", "started", "finished",
                                "exit_code", "elapsed_ms"])
        return run


def finish(run, exit_code, elapsed_ms, now):
    from utility.models import ScheduledJobRun as Run

    Run.objects.filter(pk=run.pk).update(
        status=Run.SUCCEEDED if exit_code == 0 else Run.FAILED,
        exit_code=exit_code, elapsed_ms=elapsed_ms, finished=now)


def run_job(job, *, alarm):
    """Run one job in its own process. Its output goes to this run's log."""
    argv = [sys.executable, str(Path(settings.BASE_DIR) / "manage.py"), *job.command]
    if alarm:
        argv += list(job.alarm_flags)
    env = None
    if job.alarm_env:
        env = {**os.environ, job.alarm_env: "true" if alarm else "false"}
    started = time.monotonic()
    try:
        code = subprocess.run(argv, cwd=str(settings.BASE_DIR), timeout=job.timeout,
                              env=env, check=False).returncode
    except subprocess.TimeoutExpired:
        code = TIMED_OUT
    except OSError:
        code = NOT_STARTED
    return code, int((time.monotonic() - started) * 1000)


def forget_old_runs(now):
    from utility.models import ScheduledJobRun as Run

    Run.objects.filter(slot__lt=now - timedelta(days=KEEP_DAYS)).delete()
