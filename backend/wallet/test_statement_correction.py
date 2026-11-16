"""post_statement_correction: one audited debit per statement difference, never twice."""
import json
from decimal import Decimal
from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase

from wallet.models import Transaction
from wallet.services import credit, get_or_create_wallet, wallet_expected_balance
from wallet.tests import make_user
from whatsapp.models import AuditLog


class StatementCorrectionTests(TestCase):
    def setUp(self):
        self.user, _ = make_user("08071240031", "stmtfix@zitch.test")
        credit(self.user, Decimal("500.00"), "Wallet funding", reference="WEMA-CR-266630915742")
        credit(self.user, Decimal("500.00"), "Wallet funding", reference="WEMA-CR-ACXFRSOL")

    def _run(self, *args):
        out = StringIO()
        call_command("post_statement_correction", "--user-id", str(self.user.pk), *args, stdout=out)
        return json.loads(out.getvalue())

    def _balance(self):
        return get_or_create_wallet(self.user).balance

    def test_a_dry_run_changes_nothing(self):
        result = self._run("--kind", "reversed-deposit", "--amount", "500.00",
                           "--original-reference", "WEMA-CR-ACXFRSOL",
                           "--operator-reference", "credit reversed by Wema on 13 Oct")
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["balance_after"], "500.00")
        self.assertEqual(self._balance(), Decimal("1000.00"))

    def test_a_reversed_deposit_is_debited_once_however_it_is_worded(self):
        args = ("--kind", "reversed-deposit", "--amount", "500.00",
                "--original-reference", "WEMA-CR-ACXFRSOL")
        result = self._run(*args, "--operator-reference", "reversed per Wema", "--apply")
        self.assertTrue(result["posted"])
        self.assertTrue(result["ledger_agrees"])
        self.assertEqual(self._balance(), Decimal("500.00"))
        row = Transaction.objects.get(reference=result["reference"])
        self.assertEqual((row.direction, row.transaction_status, row.service),
                         (Transaction.OUT, Transaction.SUCCESS, "Deposit reversed by bank"))
        self.assertEqual(row.meta["corrects"], "WEMA-CR-ACXFRSOL")
        self.assertTrue(AuditLog.objects.filter(action="wallet.statement_correction").exists())

        again = self._run(*args, "--operator-reference", "said differently", "--apply")
        self.assertTrue(again["already_posted"])
        self.assertEqual(self._balance(), Decimal("500.00"))

    def test_a_reversal_must_match_the_credit_it_reverses(self):
        with self.assertRaises(CommandError):
            self._run("--kind", "reversed-deposit", "--amount", "400.00",
                      "--original-reference", "WEMA-CR-ACXFRSOL", "--operator-reference", "x", "--apply")
        with self.assertRaises(CommandError):
            self._run("--kind", "reversed-deposit", "--amount", "500.00",
                      "--original-reference", "WEMA-CR-NOT-HERE", "--operator-reference", "x", "--apply")
        with self.assertRaises(CommandError):
            self._run("--kind", "reversed-deposit", "--amount", "500.00",
                      "--operator-reference", "no original", "--apply")
        self.assertEqual(self._balance(), Decimal("1000.00"))

    def test_another_customers_credit_cannot_be_reversed_here(self):
        other, _ = make_user("08071240032", "other@zitch.test")
        credit(other, Decimal("500.00"), "Wallet funding", reference="WEMA-CR-OTHER1")
        with self.assertRaises(CommandError):
            self._run("--kind", "reversed-deposit", "--amount", "500.00",
                      "--original-reference", "WEMA-CR-OTHER1", "--operator-reference", "x", "--apply")
        self.assertEqual(get_or_create_wallet(other).balance, Decimal("500.00"))

    def test_bank_charges_post_once_per_reference(self):
        args = ("--kind", "bank-charges", "--amount", "6.00",
                "--operator-reference", "SMS alerts Sep 25 - Oct 24 per Wema statement")
        result = self._run(*args, "--apply")
        self.assertEqual(Transaction.objects.get(reference=result["reference"]).service, "Bank charges")
        self.assertTrue(self._run(*args, "--apply")["already_posted"])
        self._run("--kind", "bank-charges", "--amount", "6.00",
                  "--operator-reference", "SMS alerts Oct 25 - Nov 24 per Wema statement", "--apply")
        self.assertEqual(self._balance(), Decimal("988.00"))
        self.assertEqual(wallet_expected_balance(self.user.pk), self._balance())
        with self.assertRaises(CommandError):
            self._run("--kind", "bank-charges", "--amount", "6.00", "--original-reference",
                      "WEMA-CR-ACXFRSOL", "--operator-reference", "not linked", "--apply")

    def test_only_a_failed_purchase_can_be_recorded_as_charged(self):
        Transaction.objects.create(user=self.user, service="Airtime - MTN", amount=Decimal("55.00"),
                                   currency="NGN", direction=Transaction.OUT,
                                   transaction_status=Transaction.FAILED, reference="ZTCHBFE49EB78C0A")
        result = self._run("--kind", "charged-failed-purchase", "--amount", "55.00",
                           "--original-reference", "ZTCHBFE49EB78C0A",
                           "--operator-reference", "airtime delivered; bank debited 15 Sep", "--apply")
        self.assertEqual(Transaction.objects.get(reference=result["reference"]).service,
                         "Airtime - MTN (charged by bank)")
        self.assertEqual(self._balance(), Decimal("945.00"))
        with self.assertRaises(CommandError):
            self._run("--kind", "charged-failed-purchase", "--amount", "500.00",
                      "--original-reference", "WEMA-CR-ACXFRSOL", "--operator-reference", "x", "--apply")

    def test_a_correction_larger_than_the_balance_changes_nothing(self):
        with self.assertRaises(CommandError):
            self._run("--kind", "bank-charges", "--amount", "5000.00",
                      "--operator-reference", "too much", "--apply")
        self.assertEqual(self._balance(), Decimal("1000.00"))
