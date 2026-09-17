from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from utility.reconciliation import alert_due, claim_status_lookup, recorded_vas_outcome
from wallet.models import Transaction, Wallet
from wallet.services import debit, settle_or_refund
from wallet.tests import make_user


class ReconciliationSchedulingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user, _ = make_user("08033331234", "retry@zitch.app", balance="5000")
        self.txn = debit(self.user, Decimal("55"), "airtime",
                         meta={"vas_type": "airtime", "reconcile": True})

    def callback(self, status, ip="135.236.18.76"):
        from whatsapp.models import WebhookEvent
        return WebhookEvent.objects.create(
            source="wema.txn", verified=True, outcome=WebhookEvent.ACCEPTED,
            http_status=200, reference=self.txn.reference, remote_ip=ip,
            payload={"data": {"transactionReference": self.txn.reference, "status": status}})

    @override_settings(WEMA={"CALLBACK_IPS": ["135.236.18.76"]})
    def test_recovers_confirmed_callback_but_never_guesses_from_conflicting_events(self):
        self.callback("Pending")
        self.assertIsNone(recorded_vas_outcome(self.txn))
        self.callback("Successful")
        self.assertTrue(recorded_vas_outcome(self.txn)["success"])
        self.callback("Failed")
        self.assertIsNone(recorded_vas_outcome(self.txn))

    @override_settings(WEMA={"CALLBACK_IPS": ["135.236.18.76"]})
    def test_does_not_replay_untrusted_callback(self):
        self.callback("Successful", ip="8.8.8.8")
        self.assertIsNone(recorded_vas_outcome(self.txn))

    @override_settings(WEMA={"CALLBACK_IPS": ["135.236.18.76"]}, PAYMENT_PROVIDER="wema")
    @patch("utility.management.commands.reconcile_wema.wema_provisioned_wallets", return_value=[])
    @patch("utility.management.commands.reconcile_wema.vas_requery")
    def test_sweep_replays_success_even_during_lookup_backoff(self, query, wallets):
        Transaction.objects.filter(pk=self.txn.pk).update(created=timezone.now() - timedelta(minutes=10))
        claim_status_lookup(self.txn)
        self.callback("Successful")
        call_command("reconcile_wema", account_recovery_limit=0, stdout=StringIO())
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.transaction_status, Transaction.SUCCESS)
        query.assert_not_called()

    def test_duplicate_workers_share_durable_claim(self):
        self.assertTrue(claim_status_lookup(self.txn))
        cache.clear()  # deployment/cache restart must not reset the claim
        self.assertFalse(claim_status_lookup(Transaction.objects.get(pk=self.txn.pk)))

    @patch("utility.management.commands.reconcile_wema.Command._run_unlocked")
    def test_reconciliation_cache_lock_is_released_after_a_run(self, run):
        from utility.management.commands.reconcile_wema import Command

        call_command("reconcile_wema", account_recovery_limit=0, stdout=StringIO())
        self.assertIsNone(cache.get("zitch:money-reconcile:lock"))
        run.assert_called_once()

    @patch("utility.management.commands.reconcile_wema.Command._run_unlocked",
           side_effect=RuntimeError("provider failure"))
    def test_reconciliation_cache_lock_is_released_after_failure(self, run):
        from utility.management.commands.reconcile_wema import Command

        with self.assertRaises(RuntimeError):
            Command()._run(lookback_days=2, payout_older_than_minutes=2,
                           account_recovery_limit=0)
        self.assertIsNone(cache.get("zitch:money-reconcile:lock"))
        run.assert_called_once()

    @patch("utility.management.commands.reconcile_wema.Command._run_unlocked")
    @patch("utility.management.commands.reconcile_wema.cache.add",
           side_effect=RuntimeError("cache unavailable"))
    @patch("utility.alerts.alert")
    def test_lock_backend_failure_fails_command_without_reconciling(self, alert, add, run):

        with self.assertRaises(CommandError) as raised:
            call_command("reconcile_wema", account_recovery_limit=0,
                         stderr=StringIO())

        self.assertEqual(str(raised.exception),
                         "reconcile_wema: distributed lock unavailable")
        add.assert_called_once()
        run.assert_not_called()
        alert.assert_called_once_with("reconcile_wema: run crashed", level="fatal", exc=True)

    def test_retry_resumes_after_expiry_and_increases_delay(self):
        now = timezone.now()
        with patch("utility.reconciliation.timezone.now", return_value=now):
            self.assertTrue(claim_status_lookup(self.txn))
        later = now + timedelta(seconds=31)
        with patch("utility.reconciliation.timezone.now", return_value=later):
            self.assertTrue(claim_status_lookup(self.txn))
        retry = self.txn.meta["wema_requery"]
        self.assertEqual(retry["attempts"], 2)
        self.assertEqual(parse_datetime(retry["next_at"]), later + timedelta(seconds=60))

    def test_terminal_callback_can_settle_during_retry_delay(self):
        claim_status_lookup(self.txn)
        settle_or_refund(self.txn, {"success": True})
        self.assertFalse(claim_status_lookup(self.txn))
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.transaction_status, Transaction.SUCCESS)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("4945"))

    def test_alert_repeats_only_for_new_reference_or_after_expiry(self):
        self.assertTrue(alert_due("vas", ["A"]))
        self.assertFalse(alert_due("vas", ["A"]))
        self.assertTrue(alert_due("vas", ["A", "B"]))
        cache.clear()
        self.assertTrue(alert_due("vas", ["A"]))

    @override_settings(PAYMENT_PROVIDER="wema")
    @patch("utility.management.commands.reconcile_wema.wema_provisioned_wallets", return_value=[])
    @patch("utility.management.commands.reconcile_wema.vas_requery",
           return_value={"pending": True, "lookup_refused": True, "status": "LOOKUP_REFUSED_401"})
    @patch("utility.alerts.alert")
    def test_repeated_sweeps_query_and_alert_once_without_refunding(self, alert, query, wallets):
        Transaction.objects.filter(pk=self.txn.pk).update(
            created=timezone.now() - timedelta(hours=3))
        for _ in range(3):
            call_command("reconcile_wema", account_recovery_limit=0, stdout=StringIO())
        query.assert_called_once()
        self.assertEqual(alert.call_count, 1)
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.transaction_status, Transaction.PENDING)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("4945"))
