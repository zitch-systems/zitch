import json
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from wallet.models import BankHistoryCheckpoint, Transaction, Wallet
from wallet.services import debit, wema_account_reference
from wallet.tests import make_user


class ReconciliationEvidenceTests(TestCase):
    def setUp(self):
        self.user, self.token = make_user("08044440121", "evidence@example.test", balance="5000", tier=3)
        Wallet.objects.filter(user=self.user).update(
            account_number="0450000123", account_reference=wema_account_reference(self.user))
        self.today = timezone.localdate()

    def run_report(self, rows=None, *, balance=None, history=None, **options):
        out = StringIO()
        bank = balance or {"success": True, "balance_naira": Decimal("4889.25"), "raw": {
            "result": {"availableBalance": "4889.25", "ledgerBalance": "5000.00",
                       "accountName": "PRIVATE NAME", "accountNumber": "0450000123"}}}
        with patch("utility.wema.wema_live", return_value=True), \
                patch("utility.wema.get_balance", return_value=bank) as get_balance, \
                patch("utility.wema.get_transactions", return_value=history or {
                    "success": True, "complete": True, "transactions": rows or []}) as get_history:
            call_command("reconciliation_evidence", user_ids=str(self.user.pk), stdout=out, **options)
        reports = [json.loads(line) for line in out.getvalue().splitlines()]
        return next(row for row in reports if row.get("user_id") == self.user.pk), out.getvalue(), get_balance, get_history

    def row(self, **updates):
        return {"date": self.today.isoformat(), "referenceId": "BANK-SECRET-REFERENCE",
                "tranId": "BANK-SECRET-ID", "rrn": "BANK-SECRET-RRN", "amount": "10.75",
                "creditType": "Debit", "status": "Default", "balance": "4889.25",
                "narration": "NIP fee PRIVATE NAME 08044440121 0450000123", **updates}

    def test_report_redacts_bank_identity_and_is_strictly_read_only(self):
        before_txns = list(Transaction.objects.values("id", "transaction_status", "amount", "meta"))
        before_balance = Wallet.objects.get(user=self.user).balance
        report, text, _, _ = self.run_report([self.row()])
        self.assertTrue(report["read_only"])
        self.assertEqual(report["ledger_minus_bank_available"], "110.75")
        self.assertEqual(report["balance_field_names"], ["availableBalance", "ledgerBalance"])
        self.assertEqual(report["reported_bank_balances"], {
            "availableBalance": "4889.25", "ledgerBalance": "5000.00"})
        self.assertEqual(report["reported_bank_balance_semantics"], "provider_reported_unverified")
        self.assertEqual(report["bank_debit_observations"][0]["category_hint"], "fee")
        self.assertEqual(report["bank_totals"], [
            {"direction": "debit", "status": "default", "count": 1, "amount": "10.75"}])
        for secret in ("PRIVATE NAME", "BANK-SECRET", "08044440121", "0450000123", "evidence@example.test"):
            self.assertNotIn(secret, text)
        self.assertEqual(list(Transaction.objects.values("id", "transaction_status", "amount", "meta")), before_txns)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, before_balance)
        self.assertFalse(BankHistoryCheckpoint.objects.exists())

    def test_reported_balances_exclude_malformed_nonfinite_and_unapproved_fields(self):
        report, text, _, _ = self.run_report(balance={
            "success": True, "balance_naira": Decimal("25.00"), "raw": {"result": {
                "availableBalance": "₦25.00", "ledgerBalance": "PRIVATE SECRET",
                "bookBalance": "NaN", "currentBalance": "Infinity", "holdBalance": {"private": "SECRET"},
                "lienAmount": "1,250.10", "accountNumber": "0450000123", "unknownAmount": "9000"}}})
        self.assertEqual(report["reported_bank_balances"], {"availableBalance": "25.00", "lienAmount": "1250.10"})
        for secret in ("PRIVATE", "SECRET", "0450000123", "unknownAmount", "9000", "Infinity", "NaN"):
            self.assertNotIn(secret, text)

    def test_failed_or_simulated_balance_response_exposes_no_reported_amounts(self):
        for flags in ({"success": False}, {"success": True, "mock": True}):
            with self.subTest(flags=flags):
                report, _, _, _ = self.run_report(balance={**flags, "balance_naira": Decimal("999"),
                    "raw": {"result": {"availableBalance": "999", "ledgerBalance": "1000"}}})
                self.assertEqual(report["reported_bank_balances"], {})
                self.assertIsNone(report["bank_available_balance"])

    def test_reference_match_does_not_assume_fee_is_in_principal(self):
        txn = debit(self.user, Decimal("100"), "Transfer to PRIVATE NAME", reference="LEDGER-REFERENCE",
                    meta={"bank": "Example", "wema_transfer": {"platform_reference": "BANK-PLATFORM"}})
        report, text, _, _ = self.run_report([
            self.row(referenceId="BANK-PLATFORM", amount="100"),
            self.row(referenceId="BANK-PLATFORM", tranId="FEE-REFERENCE", amount="10.75"),
        ])
        self.assertEqual(report["bank_debit_observations"][0]["same_amount_matches"], 1)
        self.assertEqual(report["bank_debit_observations"][1]["exact_reference_matches"], 1)
        self.assertEqual(report["bank_debit_observations"][1]["same_amount_matches"], 0)
        self.assertNotIn("LEDGER-REFERENCE", text)
        txn.refresh_from_db()
        self.assertEqual(txn.transaction_status, Transaction.PENDING)

    def test_incomplete_history_is_not_counted_as_bank_evidence(self):
        report, _, _, _ = self.run_report(history={
            "success": True, "complete": False, "transactions": [self.row()],
            "diagnostic": {"http_status": 400, "message": "PRIVATE NAME"}})
        self.assertFalse(report["bank_history_range_complete"])
        self.assertEqual(report["bank_totals"], [])
        self.assertEqual(report["history_http_status"], 400)

    def test_invalid_or_out_of_window_history_is_flagged(self):
        report, _, _, _ = self.run_report([
            self.row(date=(self.today + timedelta(days=1)).isoformat()),
            self.row(amount="NaN"), self.row(referenceId="", tranId=""),
        ])
        self.assertFalse(report["bank_history_range_complete"])
        self.assertEqual(report["invalid_bank_rows"], 3)
        self.assertEqual(report["bank_totals"], [])

    def test_duplicate_rows_are_flagged_and_not_added_twice(self):
        report, _, _, _ = self.run_report([self.row(), self.row()])
        self.assertFalse(report["bank_history_range_complete"])
        self.assertEqual(report["duplicate_bank_rows"], 1)
        self.assertEqual(report["bank_totals"][0]["amount"], "10.75")

    def test_observation_limit_preserves_full_aggregate(self):
        report, _, _, _ = self.run_report([
            self.row(referenceId="FIRST"), self.row(referenceId="SECOND")], max_rows=1)
        self.assertTrue(report["debit_observations_truncated"])
        self.assertEqual(len(report["bank_debit_observations"]), 1)
        self.assertEqual(report["bank_totals"][0]["amount"], "21.50")

    def test_history_windows_are_bounded_without_checkpoint_changes(self):
        Wallet.objects.filter(user=self.user).update(created=timezone.now() - timedelta(days=100))
        report, _, _, history = self.run_report(days=90)
        self.assertEqual(history.call_count, 13)
        for call in history.call_args_list:
            date_from, date_to = map(date.fromisoformat, call.args[1:])
            self.assertLessEqual((date_to - date_from).days, 6)
        self.assertFalse(BankHistoryCheckpoint.objects.exists())

    def test_missing_or_unrecognized_account_never_calls_provider(self):
        for number, expected in (("", "no_bank_account"), ("1234567890", "unrecognized_account_provenance")):
            Wallet.objects.filter(user=self.user).update(account_number=number, account_reference="")
            report, _, balance, history = self.run_report()
            self.assertEqual(report["status"], expected)
            balance.assert_not_called()
            history.assert_not_called()

    def test_arguments_reject_unbounded_or_untrusted_inputs(self):
        for options in ({"user_ids": "1,2,0"}, {"user_ids": "1,unexpected"},
                        {"user_ids": "9" * 5000}, {"user_ids": "9223372036854775808"},
                        {"user_ids": "1", "days": 91}, {"user_ids": "1", "max_rows": 201}):
            with self.subTest(options=options), self.assertRaises(CommandError):
                call_command("reconciliation_evidence", **options, stdout=StringIO())

    def test_unexpected_provider_error_is_redacted_and_next_user_is_reported(self):
        other, _ = make_user("08044440122", "another@example.test", balance="0")
        out = StringIO()
        with patch("utility.wema.wema_live", return_value=True), \
                patch("utility.wema.get_balance", side_effect=ValueError("https://bank/PRIVATE/0450000123")):
            call_command("reconciliation_evidence", user_ids=f"{self.user.pk},{other.pk}", stdout=out)
        reports = [json.loads(line) for line in out.getvalue().splitlines() if "user_id" in json.loads(line)]
        self.assertEqual(reports[0]["status"], "evidence_collection_failed")
        self.assertEqual(reports[1]["status"], "no_bank_account")
        self.assertNotIn("PRIVATE", out.getvalue())
        self.assertNotIn("0450000123", out.getvalue())

    @override_settings(WEMA={"SOURCE_ACCOUNT": "0450000123"})
    def test_pool_probe_outputs_only_safe_classification_and_account_relationships(self):
        from wallet.management.commands.reconciliation_evidence import pool_evidence

        with patch("utility.wema.wema_live", return_value=True), \
                patch("utility.wema.get_balance", return_value={"success": False,
                      "message": "Invalid account PRIVATE NAME 0450000123",
                      "diagnostic": {"http_status": 400}}) as balance:
            report = pool_evidence()
        balance.assert_called_once_with("0450000123")
        self.assertEqual(report["failure_hint"], "invalid_account")
        self.assertEqual(report["http_status"], 400)
        self.assertTrue(report["matches_wallet_account"])
        self.assertNotIn("PRIVATE", json.dumps(report))
        self.assertNotIn("0450000123", json.dumps(report))

    @override_settings(WEMA={"SOURCE_ACCOUNT": "not-a-nuban"})
    def test_invalid_pool_config_never_reaches_provider(self):
        from wallet.management.commands.reconciliation_evidence import pool_evidence

        with patch("utility.wema.get_balance") as balance:
            report = pool_evidence()
        self.assertEqual(report["status"], "invalid_source_format")
        balance.assert_not_called()
