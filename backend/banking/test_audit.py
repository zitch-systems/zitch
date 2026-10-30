"""Bank/ledger reports must retain liabilities and never invent verified assets."""
import json
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command, CommandError
from django.core.cache import cache
from django.test import TestCase, override_settings

from wallet.models import Transaction, Wallet, BankHistoryCheckpoint
from .audit import snapshot
from .client import Reply, ProviderError
from .models import BankAccount, PreviousBankArchive
from .test_client import CONFIG
from .test_fixtures import make_bank_user


@override_settings(NINEPSB=CONFIG)
class AuditTests(TestCase):
    def setUp(self):
        cache.clear()
        self.alert = self.enterContext(patch("utility.alerts.alert"))
        self.user, _ = make_bank_user("08010000901", "audit@zitch.test", balance="5000")
        self.wallet = Wallet.objects.get(user=self.user)
        self.bank = BankAccount.objects.get(wallet=self.wallet)
        self.remote = {self.bank.number: "5000"}
        self.read = self.enterContext(patch("banking.client.Client.wallet", side_effect=self.bank_read))

    def bank_read(self, number):
        amount = self.remote[number]
        if isinstance(amount, Exception):
            raise amount
        bank = BankAccount.objects.get(number=number)
        return Reply({"status": "SUCCESS", "data": {
            "nuban": number, "number": "INTERNAL-WALLET", "name": bank.name,
            "status": "Active", "tier": str(bank.tier), "pndstatus": "InActive",
            "freezeStatus": "InActive", "ledgerBalance": amount,
            "availableBalance": amount}}, 200)

    def run_report(self, name, *args):
        out = StringIO()
        call_command(name, *args, stdout=out)
        return json.loads(out.getvalue())

    def assert_money_unchanged(self, rows, balance="5000"):
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal(balance))
        self.assertEqual(list(Transaction.objects.order_by("pk").values()), rows)

    def test_balanced_assets_match_liability_on_both_reports(self):
        for name in ("settlement_report", "reconcile_balances"):
            with self.subTest(command=name):
                result = self.run_report(name)
                self.assertFalse(result["incomplete"])
                self.assertEqual(Decimal(result["ledger_liability"]), 5000)
                self.assertEqual(Decimal(result["held_total"]), 5000)
                self.assertEqual(Decimal(result["position"]), 0)
                self.assertEqual(result["checked_wallets"], 1)

    def test_either_discrepancy_is_reviewed_without_arithmetic_correction(self):
        before = list(Transaction.objects.order_by("pk").values())
        for amount, flag in (("4000", "ledger_over_bank"), ("6000", "bank_over_ledger")):
            self.remote[self.bank.number] = amount
            with self.subTest(amount=amount):
                result = snapshot()
                self.assertEqual(result[flag], 1)
                self.assertTrue(result["incomplete"])
                self.assertIsNone(result["position"])
                self.assert_money_unchanged(before)
                for command, option in (("settlement_report", "--fail-on-breach"),
                                        ("reconcile_balances", "--fail-nonzero"),
                                        ("reconcile_balances", "--fail-over")):
                    with self.assertRaises(CommandError):
                        self.run_report(command, option)

    def test_provider_outage_is_unavailable_not_zero_assets_or_a_shortfall(self):
        self.remote[self.bank.number] = ProviderError("network_unavailable")
        result = snapshot()
        self.assertEqual(result["unreachable"], 1)
        self.assertTrue(result["incomplete"])
        self.assertIsNone(result["position"])
        self.assertIsNone(result["held_total"])
        self.assertEqual(result["ledger_over_bank"], 0)
        self.assertEqual(Decimal(result["ledger_liability"]), 5000)

    def test_legacy_pool_configuration_cannot_hide_new_bank_shortfall(self):
        self.remote[self.bank.number] = "4000"
        with override_settings(WEMA={"SOURCE_ACCOUNT": "9999999999"}):
            result = snapshot()
        self.assertEqual(Decimal(result["known_9psb_assets"]), 4000)
        self.assertEqual(result["ledger_over_bank"], 1)
        self.assertIsNone(result["held_total"])

    def test_each_current_bank_account_is_read_once(self):
        other, _ = make_bank_user("08010000902", "audit2@zitch.test", balance="700")
        bank = BankAccount.objects.get(wallet__user=other)
        self.remote[bank.number] = "700"
        result = snapshot()
        self.assertEqual(result["wallets"], 2)
        self.assertEqual(Decimal(result["held_total"]), 5700)
        self.assertEqual({c.args[0] for c in self.read.call_args_list}, set(self.remote))
        self.assertEqual(self.read.call_count, 2)

    def test_historical_liability_is_retained_without_claiming_verified_old_assets(self):
        PreviousBankArchive.objects.create(wallet=self.wallet, balance=1000,
            account_number="0123456789", pending_transactions=1)
        self.remote[self.bank.number] = "4000"
        before = list(Transaction.objects.order_by("pk").values())
        result = snapshot()
        self.assertTrue(result["legacy_funding_required"])
        self.assertEqual(Decimal(result["archived_liability"]), 1000)
        self.assertEqual(Decimal(result["ledger_liability"]), 5000)
        self.assertEqual(Decimal(result["known_9psb_assets"]), 4000)
        self.assertEqual(result["ledger_over_bank"], 0)
        self.assertIsNone(result["held_total"])
        self.assert_money_unchanged(before)

    def test_pending_instruction_prevents_a_green_position(self):
        Transaction.objects.create(user=self.user, service="Bank transfer", amount=100,
            direction=Transaction.OUT, transaction_status=Transaction.PENDING,
            reference="AUDIT-PENDING", meta={"provider": "9psb"})
        Wallet.objects.filter(pk=self.wallet.pk).update(balance=4900)
        self.remote[self.bank.number] = "4900"
        result = snapshot()
        self.assertEqual(result["pending_wallets"], 1)
        self.assertIsNone(result["position"])
        self.assertTrue(result["incomplete"])

    def test_opening_review_prevents_a_green_position(self):
        self.bank.opening_review_required = True
        self.bank.save(update_fields=["opening_review_required"])
        result = snapshot()
        self.assertEqual(result["opening_reviews"], 1)
        self.assertIsNone(result["position"])

    def test_historical_opening_review_is_not_hidden_by_new_bank_balance(self):
        BankHistoryCheckpoint.objects.create(wallet=self.wallet, account_number="0123456789",
            opening_review_required=True)
        result = snapshot()
        self.assertEqual(result["historical_opening_reviews"], 1)
        self.assertTrue(result["legacy_funding_required"])
        self.assertIsNone(result["position"])

    def test_foreign_currency_ledger_is_not_summed_as_naira(self):
        Transaction.objects.create(user=self.user, amount=500, currency="USD",
            direction=Transaction.IN, transaction_status=Transaction.SUCCESS,
            service="Foreign balance", reference="AUDIT-USD")
        result = snapshot()
        self.assertEqual(Decimal(result["ledger_liability"]), 5000)
        self.assertEqual(Decimal(result["position"]), 0)

    def test_stored_ledger_divergence_prevents_a_green_position(self):
        Wallet.objects.filter(pk=self.wallet.pk).update(balance=6000)
        self.remote[self.bank.number] = "6000"
        result = snapshot()
        self.assertEqual(Decimal(result["ledger_liability"]), 5000)
        self.assertEqual(Decimal(result["stored_liability"]), 6000)
        self.assertIsNone(result["position"])

    def test_budget_exhaustion_is_reported_as_incomplete_without_calls(self):
        result = snapshot(budget_seconds=0)
        self.read.assert_not_called()
        self.assertEqual(result["unreachable"], 1)
        self.assertTrue(result["incomplete"])
        self.assertIsNone(result["held_total"])

    def test_nonzero_tolerance_cannot_hide_a_discrepancy(self):
        for value in ("0.01", "-1", "NaN", "Infinity", "bad"):
            with self.subTest(value=value), self.assertRaises(CommandError):
                self.run_report("reconcile_balances", "--tolerance", value)
        self.read.assert_not_called()

    def test_invalid_settlement_thresholds_fail_before_reading_bank(self):
        for option in ("--max-shortfall", "--max-surplus"):
            for value in ("-1", "NaN", "Infinity", "bad"):
                with self.subTest(option=option, value=value), self.assertRaises(CommandError):
                    self.run_report("settlement_report", option, value)
        self.read.assert_not_called()

    def test_reports_leave_an_operator_audit_record(self):
        from whatsapp.models import AuditLog
        for command, action in (("reconcile_balances", "recon.balance_check"),
                                ("settlement_report", "recon.settlement_report")):
            result = self.run_report(command)
            self.assertEqual(AuditLog.objects.get(action=action).after, result)

    def test_unchanged_discrepancy_alerts_once_and_changed_incident_alerts_again(self):
        self.remote[self.bank.number] = "4000"
        self.run_report("reconcile_balances")
        self.run_report("reconcile_balances")
        self.assertEqual(self.alert.call_count, 1)
        self.remote[self.bank.number] = ProviderError("network_unavailable")
        self.run_report("reconcile_balances")
        self.assertEqual(self.alert.call_count, 2)
        self.assertNotIn(self.bank.number, str(self.alert.call_args_list))

    def test_balanced_report_does_not_alert(self):
        self.run_report("settlement_report", "--fail-on-breach")
        self.run_report("reconcile_balances", "--fail-nonzero")
        self.alert.assert_not_called()
