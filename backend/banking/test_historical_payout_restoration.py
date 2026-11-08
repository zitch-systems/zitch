"""Operator correction of a retired payout must preserve new-bank principal."""
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from accounts.models import User
from wallet.models import Transaction, Wallet
from wallet.services import (InsufficientFunds, resolve_reversal_quarantine,
    reversal_quarantine_evidence, settle_or_refund, wallet_expected_balance)
from .accounts import historical_balance, spendable
from .models import HistoricalBalanceAdjustment, PreviousBankArchive


class HistoricalPayoutRestorationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="historical-restoration")
        self.wallet = Wallet.objects.create(user=self.user, balance=600)
        Transaction.objects.create(user=self.user, service="Opening test balance", amount=700,
            reference="OPENING-HISTORICAL", direction=Transaction.IN,
            transaction_status=Transaction.SUCCESS)
        self.archive = PreviousBankArchive.objects.create(wallet=self.wallet,
            account_number="0123456789", balance=400)
        self.payout = Transaction.objects.create(user=self.user, service="Bank transfer", amount=100,
            reference="RETIRED-PAYOUT", direction=Transaction.OUT,
            transaction_status=Transaction.PENDING, meta={"bank": "Wema", "reconcile": True})
        with patch("utility.alerts.alert"):
            self.assertEqual(settle_or_refund(self.payout, {"success": False}), "failed")
            self.assertEqual(settle_or_refund(self.payout, {"success": True}), "quarantined")
        self.evidence = reversal_quarantine_evidence(self.payout)

    def resolve(self):
        return resolve_reversal_quarantine(self.payout.reference,
            disposition="confirm_provider_success", reason="Bank statement confirms the original payout",
            evidence_snapshot=self.evidence, actor=self.user, approval_id=31, confirmed_amount="100")

    def test_restore_updates_archive_once_without_a_second_ledger_debit(self):
        self.resolve()
        self.wallet.refresh_from_db()
        self.payout.refresh_from_db()
        self.assertEqual(self.payout.transaction_status, Transaction.SUCCESS)
        self.assertEqual(self.wallet.balance, Decimal("600"))
        self.assertEqual(historical_balance(self.wallet), Decimal("400"))
        self.assertEqual(spendable(self.wallet), Decimal("200"))
        self.assertEqual(wallet_expected_balance(self.user.pk), self.wallet.balance)
        self.assertEqual(set(HistoricalBalanceAdjustment.objects.values_list("amount", flat=True)),
                         {Decimal("100"), Decimal("-100")})
        with self.assertRaises(ValueError):
            self.resolve()
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("600"))

    def test_new_bank_funds_cannot_cover_a_shortfall_in_archived_funds(self):
        # A reviewed earlier correction consumes the historical component.
        marker = Transaction.objects.create(user=self.user, service="Historical correction", amount=500,
            reference="EARLIER-CORRECTION", direction=Transaction.OUT,
            transaction_status=Transaction.SUCCESS)
        HistoricalBalanceAdjustment.objects.create(archive=self.archive, transaction=marker, amount=-500)
        Wallet.objects.filter(pk=self.wallet.pk).update(balance=200)
        with self.assertRaisesRegex(InsufficientFunds, "archived balance"):
            self.resolve()
        self.wallet.refresh_from_db()
        self.payout.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("200"))
        self.assertEqual(spendable(self.wallet), Decimal("200"))
        self.assertEqual(self.payout.transaction_status, Transaction.FAILED)

    def test_failed_archive_record_rolls_back_correction_and_status(self):
        with patch("banking.accounts.retain_historical_refund", side_effect=RuntimeError("evidence down")), \
                self.assertRaises(RuntimeError):
            self.resolve()
        self.wallet.refresh_from_db()
        self.payout.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("700"))
        self.assertEqual(self.payout.transaction_status, Transaction.FAILED)
        self.assertEqual(HistoricalBalanceAdjustment.objects.count(), 1)
        self.assertFalse(Transaction.objects.filter(service="Historical debit restoration evidence").exists())
