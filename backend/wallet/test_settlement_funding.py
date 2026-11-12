"""Zitch backing a customer's existing balance must never pay them twice."""
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from wallet.models import SettlementFunding, Transaction, Wallet
from wallet.reconciliation import _credit_accounted_for
from wallet.services import apply_wema_credit
from wallet.tests import make_user
from utility import wema


def _row(ref, amount, narration):
    return {"referenceId": ref, "tranId": ref, "rrn": ref, "amount": str(amount),
            "balance": "1", "creditType": "Credit", "status": "Successful",
            "narration": narration, "date": "2026-11-12T00:00:00"}


class SettlementFundingTests(TestCase):
    def setUp(self):
        self.user, _ = make_user("08011118001", "settle@example.test", balance="500")
        Wallet.objects.filter(user=self.user).update(account_number="0123456781")
        self.wallet = Wallet.objects.get(user=self.user)
        self.funding = SettlementFunding.objects.create(
            wallet=self.wallet, account_number="0123456781", amount=Decimal("500"),
            token="ZSETABCD1234", operator_reference="test")

    def test_tokened_settlement_is_matched_not_credited(self):
        count = Transaction.objects.count()
        row = _row("REF1", 500, "Zitch settlement ZSETABCD1234")
        self.assertIsNone(apply_wema_credit(self.wallet, row))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("500"))
        self.assertEqual(Transaction.objects.count(), count)
        self.funding.refresh_from_db()
        self.assertEqual(self.funding.status, SettlementFunding.MATCHED)
        self.assertTrue(_credit_accounted_for(self.wallet, row, wema.normalize_transaction(row)))
        # A replay of the same bank row stays matched and uncredited.
        self.assertIsNone(apply_wema_credit(self.wallet, row))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("500"))

    def test_wrong_amount_with_token_is_held_and_paged(self):
        with patch("utility.alerts.alert") as page:
            self.assertIsNone(apply_wema_credit(self.wallet, _row("REF2", 400, "ZSETABCD1234")))
        page.assert_called_once()
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("500"))
        self.funding.refresh_from_db()
        self.assertEqual(self.funding.status, SettlementFunding.PENDING)

    def test_ordinary_deposit_is_still_credited(self):
        apply_wema_credit(self.wallet, _row("REF3", 200, "transfer from friend"))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("700"))

    def test_token_for_another_wallet_is_ignored(self):
        other, _ = make_user("08011118002", "other-settle@example.test")
        Wallet.objects.filter(user=other).update(account_number="0123456782")
        other_wallet = Wallet.objects.get(user=other)
        apply_wema_credit(other_wallet, _row("REF4", 500, "ZSETABCD1234"))
        other_wallet.refresh_from_db()
        self.assertEqual(other_wallet.balance, Decimal("500"))
        self.funding.refresh_from_db()
        self.assertEqual(self.funding.status, SettlementFunding.PENDING)
