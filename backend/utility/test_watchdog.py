"""The services watch each other (utility/watchdog.py)."""
import time
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from utility import watchdog
from utility.models import ScheduledJobRun


class WorkerHeartbeatTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_a_beating_worker_is_healthy(self):
        watchdog.beat_worker()
        with mock.patch("utility.alerts.alert") as alert:
            self.assertTrue(watchdog.check_worker())
        alert.assert_not_called()

    def test_a_silent_worker_is_reported(self):
        watchdog.beat_worker()
        with mock.patch("utility.alerts.alert") as alert:
            self.assertFalse(watchdog.check_worker(now=time.time() + 6 * 60))
        self.assertEqual(alert.call_args.kwargs["level"], "error")
        self.assertEqual(alert.call_args.kwargs["minutes_silent"], 6)

    def test_no_heartbeat_yet_is_not_an_outage(self):
        with mock.patch("utility.alerts.alert") as alert:
            self.assertTrue(watchdog.check_worker())
        alert.assert_not_called()


class SchedulerWatchTests(TestCase):
    def run_at(self, finished):
        ScheduledJobRun.objects.create(job="reconcile_wema", slot=finished, status=ScheduledJobRun.SUCCEEDED,
                                       attempts=1, started=finished, finished=finished)

    def test_a_recent_run_is_healthy(self):
        self.run_at(timezone.now() - timedelta(minutes=6))
        with mock.patch("utility.alerts.alert") as alert:
            self.assertTrue(watchdog.check_scheduler())
        alert.assert_not_called()

    def test_a_stopped_scheduler_is_reported(self):
        self.run_at(timezone.now() - timedelta(minutes=25))
        with mock.patch("utility.alerts.alert") as alert:
            self.assertFalse(watchdog.check_scheduler())
        self.assertEqual(alert.call_args.kwargs["minutes_silent"], 25)

    def test_before_the_scheduler_is_in_use_nothing_is_reported(self):
        with mock.patch("utility.alerts.alert") as alert:
            self.assertTrue(watchdog.check_scheduler())
        alert.assert_not_called()


class AlertOutboxWatchTests(TestCase):
    def setUp(self):
        from wallet.services import credit
        from wallet.tests import make_user

        self.user, _token = make_user("08071250001", "outbox@zitch.test")
        self.txn = credit(self.user, "100", "Deposit")

    def delivery(self, minutes_ago, state="ready"):
        from wallet.models import TransactionAlertDelivery as Delivery

        row, _ = Delivery.objects.get_or_create(transaction=self.txn, channel="email", reversal=False)
        Delivery.objects.filter(pk=row.pk).update(
            state=state, created=timezone.now() - timedelta(minutes=minutes_ago))

    def test_an_alert_waiting_five_minutes_is_reported(self):
        self.delivery(7)
        with mock.patch("utility.alerts.alert") as alert:
            self.assertFalse(watchdog.check_alert_outbox())
        self.assertEqual(alert.call_args.kwargs["waiting"], 1)

    def test_a_fresh_alert_or_a_scheduled_retry_is_not(self):
        self.delivery(1)
        with mock.patch("utility.alerts.alert") as alert:
            self.assertTrue(watchdog.check_alert_outbox())
            self.delivery(30, state="retry")
            self.assertTrue(watchdog.check_alert_outbox())
        alert.assert_not_called()
