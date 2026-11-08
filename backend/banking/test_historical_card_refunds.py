"""Retired issuer refunds restore historical liabilities, never new-bank funds."""
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from accounts.models import User
from cards.models import VirtualCard
from cards.services import finalize_card_funding, resolve_card_funding
from wallet.models import Transaction, Wallet
from .accounts import historical_balance, spendable
from .models import HistoricalBalanceAdjustment, PreviousBankArchive


class HistoricalCardRefundTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="archived-card")
        self.wallet = Wallet.objects.create(user=self.user, balance=600)
        self.archive = PreviousBankArchive.objects.create(wallet=self.wallet,
            account_number="0123456789", balance=400)
        self.card = VirtualCard.objects.create(user=self.user, card_token="old-test-card",
            last4="1234", expiry="12/29")
        self.txn = Transaction.objects.create(user=self.user, service="Card funding",
            amount=100, direction=Transaction.OUT, transaction_status=Transaction.PENDING,
            reference="HISTORICAL-CARD-LOAD", meta={"card": self.card.pk,
                "card_funding": True, "reconcile": True, "card_balance_applied": False})

    def assert_retained(self):
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("700"))
        self.assertEqual(historical_balance(self.wallet), Decimal("500"))
        self.assertEqual(spendable(self.wallet), Decimal("200"))
        self.assertEqual(HistoricalBalanceAdjustment.objects.get().amount, Decimal("100"))

    def resolve(self):
        return resolve_card_funding(self.txn.reference, disposition="confirm_failed",
            reason="Issuer evidence confirms no card load", actor=self.user, approval_id=17)

    def test_automatic_failure_preserves_archive_and_is_idempotent(self):
        self.assertEqual(finalize_card_funding(self.txn, {"success": False}), "failed")
        self.assertEqual(finalize_card_funding(self.txn, {"success": False}), "failed")
        self.assert_retained()

    def test_operator_failure_preserves_archive_and_cannot_repeat(self):
        self.resolve()
        with self.assertRaises(ValueError):
            self.resolve()
        self.assert_retained()

    def test_archive_write_failure_rolls_back_money_and_transaction(self):
        for finish in (lambda: finalize_card_funding(self.txn, {"success": False}), self.resolve):
            with self.subTest(path=finish), patch("banking.accounts.retain_historical_refund",
                    side_effect=RuntimeError("evidence unavailable")), self.assertRaises(RuntimeError):
                finish()
            self.wallet.refresh_from_db()
            self.txn.refresh_from_db()
            self.assertEqual(self.wallet.balance, Decimal("600"))
            self.assertEqual(self.txn.transaction_status, Transaction.PENDING)
            self.assertFalse(HistoricalBalanceAdjustment.objects.exists())

    def test_pending_and_successful_loads_do_not_create_refund_evidence(self):
        self.assertEqual(finalize_card_funding(self.txn, {"pending": True}), "pending")
        self.assertEqual(finalize_card_funding(self.txn, {"success": True}), "success")
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("600"))
        self.assertFalse(HistoricalBalanceAdjustment.objects.exists())
