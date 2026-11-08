"""Uncertified imported cash can arrive but cannot initiate a new bank spend."""
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from wallet.models import BankHistoryCheckpoint, Transaction, Wallet
from wallet.reconciliation import reconcile_account_history
from wallet.services import (
    LimitExceeded, bank_spend_error, credit, debit, provision_wema_account,
)
from wallet.tests import make_user


class BankOpeningReviewTests(TestCase):
    def setUp(self):
        self.user, _ = make_user("08071110101", "opening-review@zitch.test", balance="1000")
        self.wallet = Wallet.objects.get(user=self.user)
        Wallet.objects.filter(pk=self.wallet.pk).update(
            account_number="0452491301", bank_tier=3, pnd_lifted=True)
        self.wallet.refresh_from_db()

    @patch("utility.wema.wema_live", return_value=True)
    def test_missing_or_unknown_opening_blocks_before_debit(self, _live):
        self.assertIn("balance needs review", bank_spend_error(self.user, Decimal("100")))
        with self.assertRaises(LimitExceeded) as caught:
            debit(self.user, Decimal("100"), "Airtime — MTN")
        self.assertIn("balance needs review", str(caught.exception))
        self.assertIn("Support", str(caught.exception))
        self.assertEqual(Wallet.objects.get(pk=self.wallet.pk).balance, Decimal("1000"))
        self.assertFalse(Transaction.objects.filter(user=self.user, direction=Transaction.OUT).exists())
        self.assertTrue(BankHistoryCheckpoint.objects.get(
            wallet=self.wallet, account_number=self.wallet.account_number).opening_review_required)

    @patch("utility.wema.wema_live", return_value=True)
    def test_current_verified_opening_allows_bank_spend(self, _live):
        BankHistoryCheckpoint.objects.create(
            wallet=self.wallet, account_number=self.wallet.account_number,
            opening_review_required=False)
        row = debit(self.user, Decimal("100"), "Airtime — MTN")
        self.assertEqual(row.transaction_status, Transaction.PENDING)
        self.assertEqual(Wallet.objects.get(pk=self.wallet.pk).balance, Decimal("900"))

    @patch("utility.wema.wema_live", return_value=True)
    def test_replaced_account_review_cannot_block_verified_current_account(self, _live):
        BankHistoryCheckpoint.objects.create(
            wallet=self.wallet, account_number="0452491399", opening_review_required=True)
        BankHistoryCheckpoint.objects.create(
            wallet=self.wallet, account_number=self.wallet.account_number,
            opening_review_required=False)
        self.assertIsNone(bank_spend_error(self.user, Decimal("100")))

    @patch("utility.wema.wema_live", return_value=True)
    def test_review_keeps_receipts_and_refunds_available_but_not_spendable(self, _live):
        BankHistoryCheckpoint.objects.create(
            wallet=self.wallet, account_number=self.wallet.account_number)
        credit(self.user, Decimal("50"), "Refund")
        row = {"referenceId": "OPENING-HELD-RECEIPT", "creditType": "Credit",
               "status": "Successfull", "amount": "250", "date": timezone.now().isoformat()}
        with patch("utility.wema.get_transactions", return_value={
                "success": True, "complete": True, "transactions": [row]}):
            result = reconcile_account_history(self.wallet)
        self.assertEqual(result["credited"], 1)
        self.assertEqual(result["error_code"], "history_opening_review")
        self.assertEqual(Wallet.objects.get(pk=self.wallet.pk).balance, Decimal("1300"))
        self.assertIn("balance needs review", bank_spend_error(self.user, Decimal("1")))

    def test_confirmed_new_issuance_records_normal_opening(self):
        Wallet.objects.filter(pk=self.wallet.pk).update(account_number="")
        wallet, outcome = provision_wema_account(
            self.user, account_number="0452491302", source="callback")
        self.assertEqual(outcome, "provisioned")
        self.assertFalse(BankHistoryCheckpoint.objects.get(
            wallet=wallet, account_number="0452491302").opening_review_required)
