"""Both channels must show existing opening-balance holds before payment entry."""
import json
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from wallet.models import BankHistoryCheckpoint, Transaction, Wallet
from wallet.services import (
    BANK_HISTORY_REVIEW_MESSAGE, LimitExceeded, bank_spend_error,
    credit, customer_funding_account, debit, wallet_balance_payload,
    wallet_expected_balance,
)
from wallet.tests import make_user
from wema_vas.models import VirtualAccount


@override_settings(BANK_ACCOUNT_PROVIDER="partnership", WEMA_PARTNERSHIP_MODE="active",
                   WEMA_PARTNERSHIP_RESTORE_VAS=True, WEMA_BILLER_MODE="active")
@patch("utility.wema.wema_live", return_value=True)
class BankHistoryReviewVisibilityTests(TestCase):
    def setUp(self):
        self.user, self.token = make_user(
            "08099990216", "history-review@example.test", balance="1000", tier=3)
        self.wallet = Wallet.objects.get(user=self.user)
        self.wallet.account_number = "0459999216"
        self.wallet.account_name = "Ada Eze"
        self.wallet.bank_name = "Wema Bank"
        self.wallet.bank_tier = 3
        self.wallet.pnd_lifted = True
        self.wallet.save()

    def assert_held_payload(self):
        funding = customer_funding_account(self.user)
        self.assertEqual(funding["account_setup_state"], "bank_history_review")
        self.assertEqual(funding["account_number"], self.wallet.account_number)
        self.assertTrue(funding["has_account"])
        self.assertTrue(funding["available"])
        for field in ("spending_available", "transfers_available", "bill_payments_available"):
            self.assertFalse(funding[field])
        self.assertEqual(funding["migration_message"], BANK_HISTORY_REVIEW_MESSAGE)
        balances = wallet_balance_payload(self.user)
        self.assertEqual(balances["balance"], Decimal("1000"))
        self.assertEqual(balances["available_balance"], Decimal("0"))
        self.assertEqual(balances["historical_balance"], Decimal("1000"))

    def test_missing_checkpoint_is_shown_as_review_without_creating_evidence(self, _live):
        self.assert_held_payload()
        self.assertFalse(BankHistoryCheckpoint.objects.exists())
        self.assertEqual(wallet_expected_balance(self.user.pk), Decimal("1000"))

    def test_existing_review_keeps_funding_but_hides_spendability(self, _live):
        checkpoint = BankHistoryCheckpoint.objects.create(
            wallet=self.wallet, account_number=self.wallet.account_number)
        self.assert_held_payload()
        checkpoint.refresh_from_db()
        self.assertTrue(checkpoint.opening_review_required)
        for path in ("/api/wallet/account/", "/api/wallet_balance/"):
            with self.subTest(path=path):
                response = self.client.post(path, json.dumps({"access_token": self.token}),
                                            content_type="application/json")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["account_setup_state"], "bank_history_review")
                self.assertEqual(response.json()["account_number"], self.wallet.account_number)
                self.assertFalse(response.json()["transfers_available"])

    def test_receipts_remain_visible_and_debits_remain_blocked(self, _live):
        BankHistoryCheckpoint.objects.create(
            wallet=self.wallet, account_number=self.wallet.account_number)
        credit(self.user, Decimal("50"), "Incoming bank credit")
        balances = wallet_balance_payload(self.user)
        self.assertEqual(balances["balance"], Decimal("1050"))
        self.assertEqual(balances["available_balance"], Decimal("0"))
        self.assertEqual(balances["historical_balance"], Decimal("1050"))
        self.assertIn("needs review", bank_spend_error(self.user, Decimal("1")))
        for service in ("Transfer to Ada", "Airtime — MTN"):
            with self.subTest(service=service), self.assertRaises(LimitExceeded):
                debit(self.user, Decimal("1"), service)
        self.assertFalse(Transaction.objects.filter(user=self.user, direction=Transaction.OUT).exists())
        self.assertEqual(wallet_expected_balance(self.user.pk), Decimal("1050"))

    def test_receipt_alerts_explain_bank_review_without_vas_bill_labels(self, _live):
        from wallet.alerts import _describe, _sms_alert, _whatsapp_template_summary

        BankHistoryCheckpoint.objects.create(
            wallet=self.wallet, account_number=self.wallet.account_number)
        receipt = credit(self.user, Decimal("50"), "Incoming bank credit")
        _subject, body = _describe(receipt)
        for text in (body, _whatsapp_template_summary(receipt, reversal=False)):
            self.assertIn("Total NGN wallet balance: ₦1,050.00", text)
            self.assertIn("Available balance: ₦0.00", text)
            self.assertIn("Funds under review: ₦1,050.00", text)
            self.assertNotIn("for bills", text)
        sms = _sms_alert(receipt)
        self.assertIn("Avail :NGN 0.00", sms)
        self.assertNotIn("Avail bills", sms)
        self.assertLessEqual(len(sms), 160)

    def test_certified_current_account_ignores_old_account_review(self, _live):
        BankHistoryCheckpoint.objects.create(
            wallet=self.wallet, account_number="0459999999")
        BankHistoryCheckpoint.objects.create(
            wallet=self.wallet, account_number=self.wallet.account_number,
            opening_review_required=False)
        funding = customer_funding_account(self.user)
        self.assertTrue(funding["spending_available"])
        self.assertTrue(funding["transfers_available"])
        self.assertTrue(funding["bill_payments_available"])
        self.assertNotIn("account_setup_state", funding)
        self.assertEqual(wallet_balance_payload(self.user)["available_balance"], Decimal("1000"))

    def test_restored_vas_account_keeps_independent_bank_opening_review(self, _live):
        VirtualAccount.objects.create(
            user=self.user, number="9990000216", display_name="Zitch/Ada Eze",
            encrypted_identity="retained", verification_reference="proof-test",
            consent_reference="consent-test", verified_at=timezone.now(), mode="live", prefix="999")
        self.assert_held_payload()
        self.assertEqual(wallet_balance_payload(self.user)["vas_balance"], Decimal("0"))

    def test_missing_account_keeps_normal_onboarding(self, _live):
        self.wallet.account_number = ""
        self.wallet.save(update_fields=["account_number"])
        funding = customer_funding_account(self.user)
        self.assertNotIn("account_setup_state", funding)
        self.assertFalse(funding["has_account"])

    def test_isolated_simulation_does_not_manufacture_bank_review(self, live):
        live.return_value = False
        self.assertNotIn("account_setup_state", customer_funding_account(self.user))
        self.assertEqual(wallet_balance_payload(self.user)["available_balance"], Decimal("1000"))
