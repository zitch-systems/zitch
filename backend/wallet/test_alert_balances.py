"""Alert balances preserve historical attribution without suggesting spendability."""
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from wallet.alerts import _describe, _email_alert_html, _sms_alert, _whatsapp_template_summary
from wallet.models import BillFundingBinding, Transaction, Wallet
from wallet.services import credit, debit, refund
from wallet.funding_test_fixtures import funding_hold
from wallet.tests import make_user


@override_settings(BANK_ACCOUNT_PROVIDER="partnership", WEMA_PARTNERSHIP_MODE="active",
                   WEMA_BILLER_MODE="active")
class TransactionAlertBalanceTests(TestCase):
    def setUp(self):
        self.user, _ = make_user("08099900001", "alert-hold@zitch.test", tier=3)
        for key, value in {"phone_verified": True, "email_verified": True,
                           "bvn_verified": True, "nin_verified": True, "tier": 3}.items():
            setattr(self.user, key, value)
        self.user.save()
        self.hold = funding_hold(self.user, "1000")
        self.held_credit = Transaction.objects.get(pk=self.hold.evidence["credits"][0]["transaction_id"])
        self.legacy_credit = credit(self.user, "5000", "Historical Partnership deposit")

    def assert_alert_balances(self, txn, *, total, available, historical=None, reversal=False):
        historical = historical or total
        _subject, body = _describe(txn, reversal=reversal)
        summary = _whatsapp_template_summary(txn, reversal=reversal)
        for text in (body, summary):
            self.assertIn(f"Total NGN wallet balance: ₦{total}", text)
            self.assertIn(f"Available balance: ₦{available}", text)
            self.assertIn(f"Funds under review: ₦{historical}", text)
            self.assertNotIn(f"Available balance: ₦{total}", text)
        self.assertNotIn("\n", summary)
        self.assertNotIn("\t", summary)
        self.assertNotIn("    ", summary)
        html = _email_alert_html(txn, reversal=reversal)
        self.assertIn("Total NGN wallet balance", html)
        self.assertIn("Available balance", html)
        self.assertIn("Funds under review", html)
        self.assertIn(f"₦{available}", html)
        sms = _sms_alert(txn, reversal=reversal)
        self.assertIn(f"Avail :NGN {available}", sms)
        self.assertLessEqual(len(sms), 160)

    def test_late_legacy_credit_does_not_overstate_available_balance(self):
        self.assert_alert_balances(self.legacy_credit, total="6,000.00", available="0.00")

    def test_pending_bill_reservation_reduces_available_balance_in_alerts(self):
        bill = debit(self.user, "100", "Airtime — MTN", enforce_limits=False)
        self.assert_alert_balances(bill, total="5,900.00", available="0.00")

    def test_reversal_does_not_release_held_money(self):
        bill = debit(self.user, "100", "Airtime — MTN", enforce_limits=False)
        refund(bill)
        self.assert_alert_balances(bill, total="6,000.00", available="0.00", reversal=True)

    def test_restricted_account_keeps_funds_visible_but_unavailable(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.assert_alert_balances(self.legacy_credit, total="6,000.00", available="0.00")
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("6000"))

    def test_disabled_collection_bills_do_not_show_spendable_funds(self):
        self.assert_alert_balances(self.legacy_credit, total="6,000.00", available="0.00")

    def test_unavailable_balance_read_does_not_suppress_payment_details(self):
        with patch("wallet.services.wallet_balance_payload", side_effect=RuntimeError("unavailable")):
            _subject, body = _describe(self.legacy_credit)
            summary = _whatsapp_template_summary(self.legacy_credit, reversal=False)
        for text in (body, summary):
            self.assertIn("Credit of ₦5,000.00", text)
            self.assertNotIn("balance:", text)
            self.assertNotIn("Available balance", text)

    def assert_account(self, txn, expected, *, reversal=False):
        for text in (_sms_alert(txn, reversal=reversal), _email_alert_html(txn, reversal=reversal)):
            self.assertIn(expected, text)
            self.assertNotIn("0123****89", text)  # The company's collection account is never customer-facing.

    def test_archived_credit_never_uses_current_account_or_spoofed_metadata(self):
        Wallet.objects.filter(user=self.user).update(account_number="0451234567")
        txn = self.held_credit
        txn.meta = {"account_number": "1111111111", "vas_account": "2222222222"}
        self.assertNotIn("9990****01", _sms_alert(txn))
        for text in (_sms_alert(txn), _email_alert_html(txn)):
            self.assertNotIn("0451****67", text)
            self.assertNotIn("1111****11", text)


    def test_partnership_credit_uses_current_account_and_ignores_metadata(self):
        Wallet.objects.filter(user=self.user).update(account_number="0451234567")
        self.legacy_credit.meta = {"vas_account": "9990000001"}
        self.assert_account(self.legacy_credit, "0451****67")
        for text in (_sms_alert(self.legacy_credit), _email_alert_html(self.legacy_credit)):
            self.assertNotIn("9990****01", text)

    def test_legacy_bill_uses_immutable_source_even_after_wallet_account_changes(self):
        bill = Transaction.objects.create(user=self.user, amount=Decimal("100"), direction=Transaction.OUT,
            service="Airtime — MTN", reference="legacy-bound-bill", transaction_status=Transaction.PENDING)
        BillFundingBinding.objects.create(transaction=bill, source_account="0451234567")
        Wallet.objects.filter(user=self.user).update(account_number="0457654321")
        self.assert_account(bill, "0451****67")

    def test_provenance_read_failure_omits_account_instead_of_falling_back_to_legacy(self):
        Wallet.objects.filter(user=self.user).update(account_number="0451234567")
        txn = self.held_credit
        with patch("wallet.funding_holds.UnsettledFunding.objects.filter", side_effect=RuntimeError("unavailable")):
            for text in (_sms_alert(txn), _email_alert_html(txn)):
                self.assertNotIn("0451****67", text)
                self.assertNotIn("9990****01", text)
