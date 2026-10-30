"""Launch gates require recent successful controls, not merely configured keys."""
from datetime import timedelta
import json
from io import StringIO
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from utility.launch_readiness import financial_controls_report
from wallet.models import BankHistoryCheckpoint, Wallet
from whatsapp.models import AuditLog


class FinancialControlsReadinessTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="readiness-customer")
        self.wallet = Wallet.objects.create(user=self.user)

    def record(self, action, data):
        return AuditLog.objects.create(actor_type="system", action=action, after=data)

    def passing(self):
        self.record("recon.integrity_check", {"wallets": 1, "mismatches": 0})
        self.record("recon.balance_check", {"wallets": 0, "unreachable": 0,
                    "ledger_over_bank": 0, "bank_over_ledger": 0,
                    "legacy_funding_required": False, "tolerance": "0.00"})
        self.record("recon.settlement_report", {"incomplete": False, "nuban_wallets": 0, "held_total": "0.00",
                    "position": "0.00", "ledger_liability": "0.00", "stored_liability": "0.00"})

    def test_existing_wallets_without_evidence_are_blocked(self):
        report = financial_controls_report()
        self.assertFalse(report["financial_controls_ready"])
        self.assertEqual(set(report["checks"].values()), {"missing"})

    def test_recent_complete_consistent_results_pass_without_network_or_writes(self):
        from django.db import connection
        self.passing()
        def reads_only(execute, sql, params, many, context):
            self.assertTrue(sql.lstrip().upper().startswith("SELECT"), sql)
            return execute(sql, params, many, context)
        with connection.execute_wrapper(reads_only), patch("requests.sessions.Session.request") as network:
            report = financial_controls_report()
        network.assert_not_called()
        self.assertTrue(report["financial_controls_ready"])

    def test_new_failure_overrides_older_success_and_does_not_leak_details(self):
        self.passing()
        self.record("recon.balance_check", {"wallets": 1, "unreachable": 0,
                    "ledger_over_bank": 1, "bank_over_ledger": 0,
                    "legacy_funding_required": False, "tolerance": "0.00", "over_sample": [{"account": "PRIVATE_ACCOUNT"}]})
        report = financial_controls_report()
        self.assertEqual(report["checks"]["bank_balances"], "failed")
        self.assertFalse(report["financial_controls_ready"])
        self.assertNotIn("PRIVATE_ACCOUNT", str(report))

    def test_stale_and_future_evidence_cannot_pass(self):
        self.passing()
        with patch("utility.launch_readiness.timezone.now", return_value=timezone.now() + timedelta(hours=8)):
            self.assertEqual(financial_controls_report()["checks"]["bank_balances"], "stale")
        with patch("utility.launch_readiness.timezone.now", return_value=timezone.now() + timedelta(hours=27)):
            self.assertEqual(set(financial_controls_report()["checks"].values()), {"stale"})
        with patch("utility.launch_readiness.timezone.now", return_value=timezone.now() - timedelta(minutes=1)):
            self.assertFalse(financial_controls_report()["financial_controls_ready"])

    def test_new_wallet_requires_fresh_integrity_inventory(self):
        self.passing()
        Wallet.objects.create(user=User.objects.create(username="new-customer"))
        self.assertEqual(financial_controls_report()["checks"]["integrity"], "inventory_changed")

    def test_unverified_opening_blocks_even_when_latest_controls_pass(self):
        from banking.models import BankAccount
        from banking.client import config
        self.passing()
        bank = BankAccount.objects.create(wallet=self.wallet, number="1234567890", name="Test Customer",
            environment=config().get("ENVIRONMENT", "sandbox"), opening_review_required=True)
        report = financial_controls_report()
        self.assertEqual(report["opening_reviews"], 1)
        self.assertFalse(report["financial_controls_ready"])
        BankAccount.objects.filter(pk=bank.pk).update(opening_review_required=False)
        report = financial_controls_report()
        self.assertEqual(report["opening_reviews"], 0)
        self.assertEqual(report["checks"]["bank_balances"], "inventory_changed")
        self.assertEqual(report["checks"]["settlement"], "inventory_changed")

    def test_nonzero_or_missing_tolerance_cannot_certify_reconciliation(self):
        self.passing()
        for tolerance in (None, "1.00", "NaN", "-1", "Infinity"):
            with self.subTest(tolerance=tolerance):
                self.record("recon.balance_check", {"wallets": 0, "unreachable": 0,
                    "ledger_over_bank": 0, "bank_over_ledger": 0,
                    "legacy_funding_required": False, "tolerance": tolerance})
                self.assertEqual(financial_controls_report()["checks"]["bank_balances"], "failed")

    def test_unresolved_legacy_exposure_blocks_older_passing_controls(self):
        self.passing()
        with patch("wallet.funding_holds.reconciliation_scope", return_value={"legacy_funding_required": True}):
            self.assertFalse(financial_controls_report()["financial_controls_ready"])

    def test_invalid_or_incomplete_settlement_never_passes(self):
        self.passing()
        for changes in ({"incomplete": True}, {"position": None}, {"position": "NaN"},
                        {"position": "-1"}, {"held_total": "5"}, {"stored_liability": "5"}):
            with self.subTest(changes=changes):
                self.record("recon.settlement_report", {"incomplete": False, "held_total": "0.00",
                    "position": "0.00", "ledger_liability": "0.00", "stored_liability": "0.00", **changes})
                self.assertEqual(financial_controls_report()["checks"]["settlement"], "failed")

    def test_database_failure_is_redacted_and_blocking(self):
        with patch("wallet.models.Wallet.objects.count", side_effect=RuntimeError("secret-connection")):
            report = financial_controls_report()
        self.assertEqual(report["status"], "inspection_unavailable")
        self.assertFalse(report["financial_controls_ready"])
        self.assertNotIn("secret-connection", str(report))

    def test_core_preflight_cannot_ignore_failed_financial_evidence(self):
        out = StringIO()
        with self.assertRaises(CommandError):
            call_command("bank_preflight", "--launch-scope", "core", stdout=out)
        report = json.loads(out.getvalue())
        self.assertFalse(report["financial_controls"]["financial_controls_ready"])
        self.assertFalse(report["production_ready"])


class EmptyFinancialControlsTests(TestCase):
    def test_empty_installation_is_explicitly_reported(self):
        report = financial_controls_report()
        self.assertEqual(report["status"], "no_wallets")
        self.assertEqual(report["wallets"], 0)

    def test_ledger_without_wallet_rows_is_not_an_empty_installation(self):
        from wallet.models import Transaction
        user = User.objects.create(username="ledger-without-wallet")
        Transaction.objects.create(user=user, reference="orphan-ledger-test", amount="1.00",
            direction=Transaction.IN, transaction_status=Transaction.SUCCESS)
        report = financial_controls_report()
        self.assertEqual(report["status"], "blocked")
        self.assertFalse(report["financial_controls_ready"])
