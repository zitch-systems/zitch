"""The opening-balance hold lifts only on a live bank balance equal to the ledger."""
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from wallet.models import BankHistoryCheckpoint, Transaction, Wallet
from wallet.services import bank_history_review_required
from wallet.tests import make_user
from whatsapp.models import AuditLog

NUBAN = "0452881679"
REF = "ledger matches Wema balance, approved by ops"


class ApproveOpeningBalanceTests(TestCase):
    def setUp(self):
        self.user, _ = make_user("08011118001", "opening@example.test")
        Transaction.objects.create(
            user=self.user, service="Wallet funding", amount=Decimal("971.25"),
            direction=Transaction.IN, transaction_status=Transaction.SUCCESS,
            reference="WEMA-CR-OPENING-1")
        Wallet.objects.filter(user=self.user).update(account_number=NUBAN,
                                                     balance=Decimal("971.25"))
        self.wallet = Wallet.objects.get(user=self.user)
        self.checkpoint = BankHistoryCheckpoint.objects.create(
            wallet=self.wallet, account_number=NUBAN, opening_review_required=True)
        live = patch("utility.wema.wema_live", return_value=True)
        live.start()
        self.addCleanup(live.stop)

    def run_command(self, *, bank=None, reference=REF, apply=False):
        bank = bank if bank is not None else {"success": True, "balance_naira": Decimal("971.25")}
        args = ["approve_opening_balance", "--user-id", str(self.user.pk),
                "--operator-reference", reference]
        if apply:
            args.append("--apply")
        out = StringIO()
        with patch("utility.wema.get_balance", return_value=bank) as read:
            call_command(*args, stdout=out)
        return out.getvalue(), read

    def assert_still_held(self):
        self.checkpoint.refresh_from_db()
        self.assertTrue(self.checkpoint.opening_review_required)
        self.assertTrue(bank_history_review_required(self.wallet))
        self.assertFalse(AuditLog.objects.filter(action="wallet.opening_balance_approved").exists())

    def test_dry_run_reports_and_leaves_the_hold(self):
        out, read = self.run_command()
        read.assert_called_once_with(NUBAN)
        self.assertIn('"dry_run": true', out)
        self.assert_still_held()

    def test_matching_balances_clear_the_hold_on_the_record(self):
        out, _ = self.run_command(apply=True)
        self.assertIn('"cleared": true', out)
        self.checkpoint.refresh_from_db()
        self.assertFalse(self.checkpoint.opening_review_required)
        self.assertFalse(bank_history_review_required(self.wallet))
        audit = AuditLog.objects.get(action="wallet.opening_balance_approved")
        self.assertEqual(audit.target, f"user:{self.user.pk}")
        self.assertEqual(audit.after["bank"], "971.25")
        self.assertEqual(audit.after["ledger"], "971.25")
        self.assertEqual(audit.after["operator_reference"], REF)
        self.assertNotIn(NUBAN, str(audit.after))

    def test_a_bank_balance_that_differs_from_the_ledger_is_refused(self):
        with self.assertRaisesMessage(CommandError, "does not equal the bank balance"):
            self.run_command(bank={"success": True, "balance_naira": Decimal("1000.00")},
                             apply=True)
        self.assert_still_held()

    def test_no_live_bank_balance_is_refused(self):
        for bank in ({"success": False, "balance_naira": None},
                     {"success": True, "mock": True, "balance_naira": Decimal("971.25")}):
            with self.subTest(bank=bank), \
                    self.assertRaisesMessage(CommandError, "did not return a live balance"):
                self.run_command(bank=bank, apply=True)
        self.assert_still_held()

    def test_an_operator_reference_is_required(self):
        with self.assertRaisesMessage(CommandError, "operator-reference"):
            self.run_command(reference="  ", apply=True)
        self.assert_still_held()

    def test_a_pending_transaction_is_refused(self):
        Transaction.objects.create(
            user=self.user, service="Airtime - MTN", amount=Decimal("100.00"),
            direction=Transaction.OUT, transaction_status=Transaction.PENDING,
            reference="ZTCH-PENDING-1")
        with self.assertRaisesMessage(CommandError, "Pending transaction"):
            self.run_command(apply=True)
        self.assert_still_held()

    def test_a_stored_balance_that_differs_from_the_ledger_is_refused(self):
        Wallet.objects.filter(pk=self.wallet.pk).update(balance=Decimal("990.00"))
        with self.assertRaisesMessage(CommandError, "run integrity_check"):
            self.run_command(apply=True)
        self.assert_still_held()
