"""write_off_unbacked_balance: test/staff balances with no Wema account, off the ledger once."""
import json
from decimal import Decimal
from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase

from wallet.models import Transaction, Wallet
from wallet.services import credit, get_or_create_wallet
from wallet.tests import make_user
from wallet.unbacked import unbacked_wallets
from whatsapp.models import AuditLog


class WriteOffUnbackedTests(TestCase):
    def setUp(self):
        self.user, _ = make_user("08071240077", "writeoff@zitch.test")
        credit(self.user, Decimal("459.00"), "Wallet funding", reference="OLD-9PSB-CR-1")
        # Detached from its old provider: a balance with no Wema account behind it.
        Wallet.objects.filter(user=self.user).update(account_number="")

    def _run(self, *args):
        out = StringIO()
        call_command("write_off_unbacked_balance", *args, stdout=out)
        return [json.loads(line) for line in out.getvalue().splitlines() if line.startswith("{")]

    def _apply(self, amount="459.00", note="test account, approved by ops", *extra):
        return self._run("--user-id", str(self.user.pk), "--amount", amount,
                         "--operator-reference", note, *extra)[0]

    def _balance(self):
        return get_or_create_wallet(self.user).balance

    def test_without_a_user_it_lists_each_wallet_and_its_command(self):
        rows = self._run()
        mine = [r for r in rows if r["user_id"] == self.user.pk]
        self.assertEqual(mine[0]["balance"], "459.00")
        self.assertIn(f"--user-id {self.user.pk} --amount 459.00", mine[0]["command"])

    def test_a_dry_run_changes_nothing(self):
        result = self._apply()
        self.assertTrue(result["dry_run"])
        self.assertEqual(self._balance(), Decimal("459.00"))

    def test_apply_zeroes_it_once_and_the_report_stops_naming_it(self):
        result = self._apply("459.00", "test account, approved by ops", "--apply")
        self.assertTrue(result["posted"])
        self.assertTrue(result["ledger_agrees"])
        self.assertEqual(self._balance(), Decimal("0.00"))
        row = Transaction.objects.get(reference=result["reference"])
        self.assertEqual((row.direction, row.transaction_status, row.service),
                         (Transaction.OUT, Transaction.SUCCESS, "Test balance written off"))
        self.assertEqual(row.meta["operator_reference"], "test account, approved by ops")
        self.assertTrue(AuditLog.objects.filter(action="wallet.unbacked_balance_written_off").exists())
        self.assertNotIn(self.user.pk, [r["user_id"] for r in unbacked_wallets()])

        again = self._apply("459.00", "test account, approved by ops", "--apply")
        self.assertTrue(again["already_posted"])
        self.assertEqual(Transaction.objects.filter(reference=result["reference"]).count(), 1)

    def test_the_amount_must_be_the_balance_now(self):
        with self.assertRaises(CommandError):
            self._apply("400.00", "x", "--apply")
        self.assertEqual(self._balance(), Decimal("459.00"))

    def test_a_reason_is_required(self):
        with self.assertRaises(CommandError):
            self._run("--user-id", str(self.user.pk), "--amount", "459.00", "--apply")

    def test_a_wallet_with_a_wema_account_is_refused(self):
        Wallet.objects.filter(user=self.user).update(account_number="0123456789")
        with self.assertRaises(CommandError):
            self._apply("459.00", "x", "--apply")
        self.assertEqual(self._balance(), Decimal("459.00"))

    def test_a_pending_transaction_blocks_it(self):
        Transaction.objects.create(user=self.user, amount=Decimal("10"), direction=Transaction.OUT,
                                   service="Airtime", reference="PEND-1",
                                   transaction_status=Transaction.PENDING)
        with self.assertRaises(CommandError):
            self._apply("459.00", "x", "--apply")
