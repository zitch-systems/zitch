"""Alert balances must respect retained legacy funds and VAS reservations."""
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from wallet.alerts import _describe, _email_alert_html, _sms_alert, _whatsapp_template_summary
from wallet.models import BillFundingBinding, Transaction, Wallet
from wallet.services import credit, debit, refund
from wema_vas.models import VirtualAccount
from wema_vas.services import process_notification
from wema_vas.tests import LIVE, account_fixture, payload


COLLECTION = "0123456789"
VAS = {**LIVE, "ENABLE_ENROLLMENT": True, "LIVE_APPROVAL_REFERENCE": "bank-validation-test",
       "RELEASE_PHASE": "general", "GENERAL_APPROVAL_REFERENCE": "launch-test",
       "COLLECTION_ACCOUNT": COLLECTION}


@override_settings(WEMA_VAS=VAS, WEMA_PARTNERSHIP_MODE="archive", BANK_ACCOUNT_PROVIDER="wema_vas",
                   WEMA_BILLER_MODE="active", WEMA_VAS_BILLER_ENABLED=True,
                   WEMA_VAS_BILLER_SOURCE_ACCOUNT=COLLECTION,
                   WEMA_VAS_BILLER_APPROVAL_REFERENCE="bank-biller-test-approval")
class TransactionAlertBalanceTests(TestCase):
    def setUp(self):
        self.user, self.account = account_fixture()
        for key, value in {"phone_verified": True, "email_verified": True,
                           "bvn_verified": True, "nin_verified": True, "tier": 3}.items():
            setattr(self.user, key, value)
        self.user.save()
        process_notification(payload(self.account, created_at=timezone.now().isoformat(), amount="1000.00"))
        self.legacy_credit = credit(self.user, "5000", "Historical Partnership deposit")

    def assert_alert_balances(self, txn, *, total, available, historical="5,000.00", reversal=False):
        _subject, body = _describe(txn, reversal=reversal)
        summary = _whatsapp_template_summary(txn, reversal=reversal)
        for text in (body, summary):
            self.assertIn(f"Total NGN wallet balance: ₦{total}", text)
            self.assertIn(f"Available for bills: ₦{available}", text)
            self.assertIn(f"Historical funds unavailable for bills: ₦{historical}", text)
            self.assertNotIn(f"Available balance: ₦{total}", text)
        self.assertNotIn("\n", summary)
        self.assertNotIn("\t", summary)
        self.assertNotIn("    ", summary)
        html = _email_alert_html(txn, reversal=reversal)
        self.assertIn("Total NGN wallet balance", html)
        self.assertIn("Available for bills", html)
        self.assertIn("Historical funds unavailable for bills", html)
        self.assertIn(f"₦{available}", html)
        self.assertNotIn("Available balance", html)
        sms = _sms_alert(txn, reversal=reversal)
        self.assertIn(f"Avail bills :NGN {available}", sms)
        self.assertLessEqual(len(sms), 160)

    def test_late_legacy_credit_does_not_overstate_available_balance(self):
        self.assert_alert_balances(self.legacy_credit, total="6,000.00", available="1,000.00")

    def test_pending_bill_reservation_reduces_available_balance_in_alerts(self):
        bill = debit(self.user, "100", "Airtime — MTN")
        self.assert_alert_balances(bill, total="5,900.00", available="900.00")

    def test_reversal_restores_only_the_vas_available_amount(self):
        bill = debit(self.user, "100", "Airtime — MTN")
        refund(bill)
        self.assert_alert_balances(bill, total="6,000.00", available="1,000.00", reversal=True)

    def test_restricted_account_keeps_funds_visible_but_unavailable(self):
        VirtualAccount.objects.filter(pk=self.account.pk).update(active=False)
        self.assert_alert_balances(self.legacy_credit, total="6,000.00", available="0.00")
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("6000"))

    @override_settings(WEMA_VAS_BILLER_ENABLED=False)
    def test_disabled_collection_bills_do_not_show_spendable_funds(self):
        self.assert_alert_balances(self.legacy_credit, total="6,000.00", available="0.00")

    def test_unavailable_balance_read_does_not_suppress_payment_details(self):
        with patch("wallet.services.wallet_balance_payload", side_effect=RuntimeError("unavailable")):
            _subject, body = _describe(self.legacy_credit)
            summary = _whatsapp_template_summary(self.legacy_credit, reversal=False)
        for text in (body, summary):
            self.assertIn("Credit of ₦5,000.00", text)
            self.assertNotIn("balance:", text)
            self.assertNotIn("Available for bills", text)

    def assert_account(self, txn, expected, *, reversal=False):
        for text in (_sms_alert(txn, reversal=reversal), _email_alert_html(txn, reversal=reversal)):
            self.assertIn(expected, text)
            self.assertNotIn("0123****89", text)  # The company's collection account is never customer-facing.

    def test_vas_credit_uses_immutable_receipt_account_despite_old_wallet_or_spoofed_metadata(self):
        Wallet.objects.filter(user=self.user).update(account_number="0451234567")
        txn = self.account.receipts.get().transaction
        txn.meta = {"account_number": "1111111111", "vas_account": "2222222222"}
        self.assert_account(txn, "9990****01")
        for text in (_sms_alert(txn), _email_alert_html(txn)):
            self.assertNotIn("0451****67", text)
            self.assertNotIn("1111****11", text)

    def test_vas_bill_and_refund_show_customer_account_not_company_source(self):
        Wallet.objects.filter(user=self.user).update(account_number="0451234567")
        bill = debit(self.user, "100", "Airtime — MTN")
        self.assert_account(bill, "9990****01")
        refund(bill)
        self.assert_account(bill, "9990****01", reversal=True)

    def test_late_legacy_credit_retains_old_account_after_live_vas_and_ignores_metadata(self):
        Wallet.objects.filter(user=self.user).update(account_number="0451234567")
        self.legacy_credit.meta = {"vas_account": self.account.number}
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
        txn = self.account.receipts.get().transaction
        with patch("wema_vas.models.Receipt.objects.select_related", side_effect=RuntimeError("unavailable")):
            for text in (_sms_alert(txn), _email_alert_html(txn)):
                self.assertNotIn("0451****67", text)
                self.assertNotIn("9990****01", text)


@override_settings(WEMA_VAS={**VAS, "MODE": "validation", "PREFIX": "711"})
class ValidationTransactionAlertBalanceTests(TestCase):
    def test_validation_account_balance_never_enters_real_money_alerts(self):
        user, account = account_fixture(suffix="2", mode="validation")
        account.validation_balance = Decimal("9000")
        account.save(update_fields=["validation_balance"])
        txn = credit(user, "1000", "Historical Partnership deposit")
        _subject, body = _describe(txn)
        summary = _whatsapp_template_summary(txn, reversal=False)
        for text in (body, summary):
            self.assertIn("Available balance: ₦1,000.00", text)
            self.assertNotIn("9,000", text)
            self.assertNotIn("10,000", text)
            self.assertNotIn("Historical funds unavailable", text)
        Wallet.objects.filter(user=user).update(account_number=account.number)
        for text in (_sms_alert(txn), _email_alert_html(txn)):
            self.assertNotIn("7110****02", text)
