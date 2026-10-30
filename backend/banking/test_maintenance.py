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


class WorkerSchedulingTests(TestCase):
    def test_worker_does_not_duplicate_cron_money_or_account_sweeps(self):
        from django.core.management import call_command
        from django.test import override_settings
        with override_settings(DEBUG=True, WHATSAPP_WORKER_RECONCILE=False), \
             patch("whatsapp.management.commands.whatsapp_worker.threading.Thread") as thread, \
             patch("whatsapp.management.commands.whatsapp_worker.signal.signal"), \
             patch("whatsapp.management.commands.whatsapp_worker.process_once", side_effect=KeyboardInterrupt), \
             self.assertRaises(KeyboardInterrupt):
            call_command("whatsapp_worker", poll_seconds=0.5)
        self.assertEqual([call.kwargs["name"] for call in thread.call_args_list], ["whatsapp-transaction-alert-retry"])

    def test_idle_worker_backs_off_polling_and_keeps_alert_retry_enabled(self):
        from django.core.management import call_command
        from django.test import override_settings
        with override_settings(DEBUG=True, WHATSAPP_WORKER_RECONCILE=False), \
             patch("whatsapp.management.commands.whatsapp_worker.threading.Thread"), \
             patch("whatsapp.management.commands.whatsapp_worker.signal.signal"), \
             patch("whatsapp.management.commands.whatsapp_worker.time.monotonic", return_value=100), \
             patch("whatsapp.management.commands.whatsapp_worker.time.sleep") as sleep, \
             patch("whatsapp.management.commands.whatsapp_worker.process_once", side_effect=[(0, 0), (0, 0), (0, 0), KeyboardInterrupt]), \
             self.assertRaises(KeyboardInterrupt):
            call_command("whatsapp_worker", poll_seconds=0.5)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.5, 0.75, 1.125])
