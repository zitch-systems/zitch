from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from django.test import TestCase
from .maintenance import run_job, run_due, period_for
from .models import MaintenanceRun


class MaintenanceTests(TestCase):
    def test_completed_period_is_not_repeated(self):
        with patch("banking.maintenance.call_command") as call:
            self.assertEqual(run_job("integrity_check", "test-day", {}), "complete")
            self.assertEqual(run_job("integrity_check", "test-day", {}), "complete")
            self.assertEqual(call.call_count, 1)

    def test_failed_job_does_not_skip_other_controls(self):
        def run(name, **kwargs):
            if name == "integrity_check":
                raise RuntimeError("private details must not enter scheduler evidence")
        with patch("banking.maintenance.call_command", side_effect=run) as call:
            results = run_due(datetime(2026, 10, 28, 12, 5, tzinfo=timezone.utc))
        self.assertEqual(results["integrity_check"], "failed")
        self.assertEqual(results["settlement_report"], "complete")
        self.assertEqual(call.call_count, 7)
        self.assertEqual(MaintenanceRun.objects.get(name="integrity_check").last_error, "RuntimeError")

    def test_active_lease_cannot_duplicate_execution(self):
        now = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
        MaintenanceRun.objects.create(name="job", period="today", lease_until=now+timedelta(minutes=5), state="running")
        with patch("banking.maintenance.call_command") as call:
            self.assertEqual(run_job("job", "today", {}, now=now), "busy")
        call.assert_not_called()

    def test_schedule_is_utc_and_does_not_run_before_daily_time(self):
        self.assertIsNone(period_for((6,15), datetime(2026,10,28,6,14,tzinfo=timezone.utc)))
        self.assertEqual(period_for((6,15), datetime(2026,10,28,6,15,tzinfo=timezone.utc)), "2026-10-28")
