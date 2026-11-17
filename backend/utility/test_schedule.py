"""The single scheduler cron (utility/schedule.py, run_schedule).

What matters: every job runs once per slot, in order, with reconcile_wema
first; a slot the cron missed or a job that crashed is run on the next run; a
finding never fails the cron when an operator channel exists; and when none
does, the checks fail the run exactly as their own crons used to.
"""
import io
import subprocess
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from utility import schedule
from utility.models import ScheduledJobRun


def utc(*args):
    return datetime(*args, tzinfo=dt_timezone.utc)


class SlotTests(TestCase):

    def test_every_five_minutes_floors_to_the_slot(self):
        job = schedule.job_named("reconcile_wema")
        self.assertEqual(schedule.latest_slot(job, utc(2026, 11, 15, 3, 44, 59)),
                         utc(2026, 11, 15, 3, 40))
        self.assertEqual(schedule.latest_slot(job, utc(2026, 11, 15, 3, 45)),
                         utc(2026, 11, 15, 3, 45))

    def test_a_daily_job_before_its_time_is_still_yesterdays_slot(self):
        job = schedule.job_named("run_maturities")   # 01:05 UTC
        self.assertEqual(schedule.latest_slot(job, utc(2026, 11, 15, 1, 4)),
                         utc(2026, 11, 14, 1, 5))
        self.assertEqual(schedule.latest_slot(job, utc(2026, 11, 15, 1, 5)),
                         utc(2026, 11, 15, 1, 5))

    def test_the_six_hourly_check_has_four_slots_a_day(self):
        job = schedule.job_named("reconcile_balances")
        self.assertEqual(schedule.latest_slot(job, utc(2026, 11, 15, 0, 4)),
                         utc(2026, 11, 14, 18, 5))
        self.assertEqual(schedule.latest_slot(job, utc(2026, 11, 15, 13, 0)),
                         utc(2026, 11, 15, 12, 5))

    def test_lagos_time_does_not_shift_the_slots(self):
        lagos = dt_timezone(timedelta(hours=1))
        job = schedule.job_named("settlement_report")   # 06:15 UTC = 07:15 Lagos
        self.assertEqual(schedule.latest_slot(job, datetime(2026, 11, 15, 7, 20, tzinfo=lagos)),
                         utc(2026, 11, 15, 6, 15))

    def test_every_job_has_a_schedule_and_a_unique_name(self):
        names = [job.name for job in schedule.JOBS]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(names[0], "reconcile_wema")
        for job in schedule.JOBS:
            self.assertTrue(bool(job.every_minutes) != bool(job.at), job.name)


class ClaimTests(TestCase):
    NOW = utc(2026, 11, 15, 6, 20)

    def setUp(self):
        self.job = schedule.job_named("settlement_report")
        self.slot = schedule.latest_slot(self.job, self.NOW)

    def claim(self, now=None, alarm=False):
        return schedule.claim(self.job, self.slot, now or self.NOW, alarm=alarm)

    def test_a_slot_is_claimed_once(self):
        run = self.claim()
        self.assertEqual((run.status, run.attempts), (ScheduledJobRun.RUNNING, 1))
        self.assertIsNone(self.claim())      # still running, within its time limit

    def test_a_succeeded_slot_is_never_run_again(self):
        schedule.finish(self.claim(), 0, 1200, self.NOW)
        self.assertIsNone(self.claim(self.NOW + timedelta(hours=1)))

    def test_a_failed_slot_is_retried_until_its_attempts_run_out(self):
        for attempt in range(1, self.job.attempts + 1):
            run = self.claim()
            self.assertEqual(run.attempts, attempt)
            schedule.finish(run, 1, 900, self.NOW)
        self.assertIsNone(self.claim())

    def test_a_run_whose_process_died_is_retried_after_its_time_limit(self):
        self.claim()
        self.assertIsNone(self.claim(self.NOW + timedelta(seconds=self.job.timeout)))
        later = self.NOW + timedelta(seconds=self.job.timeout + 121)
        run = self.claim(later)
        self.assertEqual(run.attempts, 2)

    def test_with_the_alarm_flags_a_failed_check_is_reported_once_per_slot(self):
        schedule.finish(self.claim(alarm=True), 1, 900, self.NOW)
        self.assertIsNone(self.claim(alarm=True))

    def test_old_runs_are_forgotten(self):
        schedule.finish(self.claim(), 0, 10, self.NOW)
        schedule.forget_old_runs(self.NOW + timedelta(days=schedule.KEEP_DAYS + 1))
        self.assertFalse(ScheduledJobRun.objects.exists())


class RunJobTests(TestCase):

    def test_the_job_runs_as_its_own_manage_py_process(self):
        job = schedule.Job("version", ("--version",), every_minutes=5, timeout=60)
        code, elapsed_ms = schedule.run_job(job, alarm=False)
        self.assertEqual(code, 0)
        self.assertGreaterEqual(elapsed_ms, 0)

    def test_the_alarm_flags_are_passed_only_when_asked(self):
        job = schedule.job_named("integrity_check")
        with mock.patch("utility.schedule.subprocess.run") as run:
            run.return_value.returncode = 0
            schedule.run_job(job, alarm=False)
            self.assertNotIn("--fail-nonzero", run.call_args.args[0])
            schedule.run_job(job, alarm=True)
            self.assertIn("--fail-nonzero", run.call_args.args[0])

    def test_the_reconcile_findings_switch_follows_the_alarm(self):
        job = schedule.job_named("reconcile_wema")
        with mock.patch("utility.schedule.subprocess.run") as run:
            run.return_value.returncode = 0
            schedule.run_job(job, alarm=False)
            self.assertEqual(run.call_args.kwargs["env"]["WEMA_RECONCILE_FAIL_INCOMPLETE"], "false")
            schedule.run_job(job, alarm=True)
            self.assertEqual(run.call_args.kwargs["env"]["WEMA_RECONCILE_FAIL_INCOMPLETE"], "true")

    def test_a_job_that_runs_out_of_time_is_recorded_as_timed_out(self):
        job = schedule.job_named("aml_scan")
        with mock.patch("utility.schedule.subprocess.run",
                        side_effect=subprocess.TimeoutExpired("aml_scan", job.timeout)):
            self.assertEqual(schedule.run_job(job, alarm=False)[0], schedule.TIMED_OUT)


class CommandTests(TestCase):
    """run_schedule end to end, with each job's process replaced by its exit code."""

    def run_schedule(self, now, codes=None, channel=True, *args):
        codes = codes or {}
        ran = []

        def fake_run(job, *, alarm):
            ran.append((job.name, alarm))
            return codes.get(job.name, 0), 5

        out = io.StringIO()
        with mock.patch("django.utils.timezone.now", return_value=now), \
             mock.patch("utility.schedule.run_job", side_effect=fake_run), \
             mock.patch("utility.alerts.operator_channel_configured", return_value=channel), \
             mock.patch("utility.alerts.alert") as alert:
            call_command("run_schedule", *args, stdout=out, stderr=io.StringIO())
        return ran, alert, out.getvalue()

    def test_the_first_run_does_every_job_once_reconcile_first(self):
        ran, _alert, _out = self.run_schedule(utc(2026, 11, 15, 7, 0))
        self.assertEqual([name for name, _ in ran], [job.name for job in schedule.JOBS])

    def test_the_next_run_does_only_the_deposits_sweep(self):
        self.run_schedule(utc(2026, 11, 15, 7, 0))
        ran, _alert, _out = self.run_schedule(utc(2026, 11, 15, 7, 10))
        self.assertEqual([name for name, _ in ran], ["reconcile_wema"])

    def test_a_daily_job_runs_at_its_time(self):
        self.run_schedule(utc(2026, 11, 15, 6, 10))   # after the 06:05 balance check
        ran, _alert, _out = self.run_schedule(utc(2026, 11, 15, 6, 15))
        self.assertEqual([name for name, _ in ran], ["reconcile_wema", "settlement_report"])

    def test_a_slot_missed_by_an_outage_runs_on_the_next_run(self):
        self.run_schedule(utc(2026, 11, 15, 6, 0))
        # The cron did not run between 06:00 and 09:00 (an outage, a deploy).
        ran, _alert, _out = self.run_schedule(utc(2026, 11, 15, 9, 0))
        self.assertEqual([name for name, _ in ran],
                         ["reconcile_wema", "reconcile_balances", "settlement_report",
                          "address_webhook_watch"])

    def test_a_crashed_daily_job_is_retried_and_the_cron_stays_green(self):
        self.run_schedule(utc(2026, 11, 15, 6, 0))
        ran, alert, _out = self.run_schedule(utc(2026, 11, 15, 6, 15),
                                             codes={"settlement_report": 1})
        self.assertEqual(alert.call_args.kwargs["level"], "warning")   # will retry
        ran, alert, _out = self.run_schedule(utc(2026, 11, 15, 6, 20))
        self.assertEqual([name for name, _ in ran], ["reconcile_wema", "settlement_report"])
        alert.assert_not_called()

    def test_the_last_attempt_is_an_error_alert(self):
        job = schedule.job_named("settlement_report")
        now = utc(2026, 11, 15, 6, 15)
        self.run_schedule(now - timedelta(minutes=15))
        for minute in range(job.attempts):
            _ran, alert, _out = self.run_schedule(now + timedelta(minutes=5 * minute),
                                                  codes={"settlement_report": 1})
        self.assertEqual(alert.call_args.kwargs["level"], "error")
        ran, _alert, _out = self.run_schedule(now + timedelta(minutes=5 * job.attempts))
        self.assertEqual([name for name, _ in ran], ["reconcile_wema"])

    def test_with_an_operator_channel_checks_run_without_their_fail_flags(self):
        ran, _alert, _out = self.run_schedule(utc(2026, 11, 15, 7, 0), channel=True)
        self.assertTrue(all(alarm is False for _name, alarm in ran))

    def test_without_a_channel_a_finding_fails_the_run_as_the_old_cron_did(self):
        with self.assertRaises(SystemExit) as stop:
            self.run_schedule(utc(2026, 11, 15, 7, 0), codes={"reconcile_balances": 1},
                              channel=False)
        self.assertEqual(stop.exception.code, 1)

    def test_without_a_channel_a_clean_run_still_passes(self):
        ran, _alert, _out = self.run_schedule(utc(2026, 11, 15, 7, 0), channel=False)
        self.assertTrue(all(alarm is True for _name, alarm in ran))

    def test_only_runs_the_named_job(self):
        ran, _alert, _out = self.run_schedule(utc(2026, 11, 15, 7, 0), None, True,
                                              "--only", "integrity_check")
        self.assertEqual([name for name, _ in ran], ["integrity_check"])

    def test_list_runs_nothing(self):
        ran, _alert, out = self.run_schedule(utc(2026, 11, 15, 7, 0), None, True, "--list")
        self.assertEqual(ran, [])
        self.assertIn("reconcile_wema", out)
        self.assertIn("due (not run yet)", out)

    def test_an_unknown_job_is_refused(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            self.run_schedule(utc(2026, 11, 15, 7, 0), None, True, "--only", "nope")
