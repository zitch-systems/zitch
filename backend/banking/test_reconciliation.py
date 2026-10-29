"""Recovery keeps uncertain money queued and isolates per-record failures."""
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command, CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from . import reconciliation
from .client import ProviderError, Reply
from .models import BankAccount, Instruction
from .payments import dispatch, requery
from .test_client import CONFIG
from .test_money import MoneyTests


@override_settings(NINEPSB=CONFIG)
class RecoveryTests(TestCase):
    def setUp(self):
        cache.clear()
        MoneyTests.setUp(self)
        self.instruction = MoneyTests.instruction(self)

    def test_one_unexpected_failure_does_not_stop_later_queues_or_leak_error_text(self):
        BankAccount.objects.filter(pk=self.bank.pk).update(checked_at=None)
        with patch.object(reconciliation, "requery", side_effect=RuntimeError("private-account-and-secret")), \
                patch.object(reconciliation, "update_account") as account, self.assertLogs("banking.reconciliation") as logged:
            result = reconciliation.sweep()
        self.assertEqual(result["errors"], 1)
        self.assertEqual(result["accounts"], 1)
        account.assert_called_once()
        self.assertNotIn("private-account-and-secret", " ".join(logged.output))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 400)
        self.assertEqual(Instruction.objects.get().state, "prepared")

    def test_failed_recovery_command_reports_nonzero_after_processing(self):
        with patch.object(reconciliation, "requery", side_effect=ProviderError("status_unavailable")), \
                self.assertRaises(CommandError):
            call_command("reconcile_9psb", stdout=StringIO())

    def test_uncertain_instruction_is_requeried_without_second_submission(self):
        with patch("banking.payments._invoke", side_effect=ProviderError("network_unavailable", uncertain=True)) as send:
            dispatch(self.instruction)
            Instruction.objects.filter(pk=self.instruction.pk).update(next_check=timezone.now())
            with patch("banking.client.Client.transaction_status", return_value=Reply({"status": "SUCCESS", "data": {"status": "PENDING"}}, 200)) as lookup:
                result = reconciliation.sweep()
            self.assertEqual(send.call_count, 1)
            lookup.assert_called_once()
        self.assertEqual(result["instructions"], 1)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 400)
        self.assertEqual(Instruction.objects.get().state, "pending")

    def test_reentrant_dispatch_claim_sends_one_instruction(self):
        def send(row, body):
            again = dispatch(Instruction.objects.get(pk=row.pk))
            self.assertTrue(again["pending"])
            return Reply({"status": "SUCCESS", "responseCode": "00"}, 200)
        with patch("banking.payments._invoke", side_effect=send) as sent:
            result = dispatch(self.instruction)
        self.assertTrue(result["success"])
        sent.assert_called_once()
        self.assertEqual(Instruction.objects.get().attempts, 1)

    def test_stuck_instruction_pages_once_without_refund_and_fresh_one_does_not(self):
        Instruction.objects.filter(pk=self.instruction.pk).update(state="pending")
        with patch.object(reconciliation, "requery"), patch("utility.alerts.alert") as alert:
            reconciliation.sweep()
            alert.assert_not_called()
            Instruction.objects.filter(pk=self.instruction.pk).update(created=timezone.now()-timedelta(hours=3))
            reconciliation.sweep()
            reconciliation.sweep()
        alert.assert_called_once()
        self.assertNotIn(self.bank.number, str(alert.call_args))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 400)
        self.assertEqual(Instruction.objects.get().state, "pending")

    def test_execution_budget_stops_before_provider_io(self):
        with patch.object(reconciliation.time, "monotonic", side_effect=[0, 121]), \
                patch.object(reconciliation, "requery") as query:
            result = reconciliation.sweep(budget_seconds=1)
        query.assert_not_called()
        self.assertEqual(result["instructions"], 0)
