"""Run every periodic job that is due. See utility/schedule.py.

Start command for the one scheduler cron (every 5 or 10 minutes):

    python manage.py migrate --check && python manage.py run_schedule

``--list`` shows each job's latest slot and how its run went, without running
anything. ``--only NAME`` runs just the named jobs (still once per slot).
"""
import logging
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import OperationalError, connection
from django.utils import timezone

log = logging.getLogger("zitch")


class Command(BaseCommand):
    help = "Run every periodic job that is due: reconcile_wema each run, the daily checks once per slot."

    def add_arguments(self, parser):
        parser.add_argument("--list", action="store_true",
                            help="Show each job's latest slot and its run; run nothing.")
        parser.add_argument("--only", action="append", default=[], metavar="JOB",
                            help="Run only this job (repeatable).")

    def handle(self, *args, **options):
        from utility import schedule
        from utility.alerts import alert, operator_channel_configured

        names = options["only"]
        unknown = sorted(set(names) - {job.name for job in schedule.JOBS})
        if unknown:
            raise CommandError(f"Unknown job(s): {', '.join(unknown)}. Jobs: "
                               + ", ".join(job.name for job in schedule.JOBS))
        jobs = [job for job in schedule.JOBS if not names or job.name in names]
        self._wait_for_database()
        now = timezone.now()
        alarm = not operator_channel_configured()

        if options["list"]:
            self._list(jobs, now, alarm)
            return

        if alarm:
            self.stdout.write(
                "schedule: no operator alert channel is configured (OPS_ALERT_EMAILS with "
                "RESEND_API_KEY, or SENTRY_DSN). The checks run with their --fail flags and "
                "a finding fails this run, so the cron's failure notice is the alarm.")
        problems = []
        for job in jobs:
            slot = schedule.latest_slot(job, now)
            run = schedule.claim(job, slot, now, alarm=alarm)
            if run is None:
                continue
            self.stdout.write(f"schedule: {job.name} slot={slot:%Y-%m-%dT%H:%MZ} "
                              f"attempt={run.attempts} start")
            self.stdout.flush()
            code, elapsed_ms = schedule.run_job(job, alarm=alarm)
            schedule.finish(run, code, elapsed_ms, timezone.now())
            self.stdout.write(f"schedule: {job.name} exit={code} elapsed_ms={elapsed_ms}")
            if code != 0:
                problems.append((job, code, run.attempts))

        # The other services: is the worker alive, is the alert outbox moving,
        # are Wema's deposit notifications arriving?
        from utility import watchdog

        watchdog.check_worker()
        watchdog.check_alert_outbox()
        watchdog.check_wema_notifications()

        try:
            schedule.forget_old_runs(now)
        except Exception:  # noqa: BLE001 - housekeeping retries on the next run
            log.warning("schedule_cleanup_failed")

        for job, code, attempt in problems:
            last = attempt >= schedule.max_attempts(job, alarm)
            alert(f"Scheduled job {job.name} did not finish cleanly",
                  level="error" if last else "warning",
                  job=job.name, exit_code=code, attempt=attempt,
                  timed_out=code == schedule.TIMED_OUT,
                  next="its next slot" if last else "the next run")
        if problems and alarm:
            self.stderr.write("schedule: a job failed and no operator alert channel is "
                              "configured; failing this run so its failure notice reaches someone.")
            raise SystemExit(1)

    def _wait_for_database(self, tries=4, pause=5.0):
        """Ride out a database restart instead of losing the run to it."""
        for attempt in range(1, tries + 1):
            try:
                connection.ensure_connection()
                return
            except OperationalError:
                if attempt == tries:
                    raise
                connection.close()
                time.sleep(pause)

    def _list(self, jobs, now, alarm):
        from utility import schedule
        from utility.models import ScheduledJobRun

        self.stdout.write(f"now {now:%Y-%m-%dT%H:%M:%SZ}; checks run "
                          + ("WITH their --fail flags (no operator alert channel)"
                             if alarm else "without --fail flags (findings are alerted)"))
        for job in jobs:
            slot = schedule.latest_slot(job, now)
            run = ScheduledJobRun.objects.filter(job=job.name, slot=slot).first()
            if run is None:
                state = "due (not run yet)"
            else:
                state = (f"{run.status} attempt {run.attempts}"
                         + (f" exit {run.exit_code}" if run.exit_code is not None else "")
                         + (f" in {run.elapsed_ms} ms" if run.elapsed_ms is not None else ""))
            self.stdout.write(f"{job.name:24} {slot:%Y-%m-%dT%H:%MZ}  {state}")
