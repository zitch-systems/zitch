"""Re-onboarding tests must not migrate an existing customer's real funds."""
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from utility import providers
from wallet.models import Transaction, Wallet
from wallet.services import (LimitExceeded, apply_wema_credit, biller_source_for_transaction,
    customer_funding_account, debit, wallet_balance_payload, wallet_expected_balance)
from wallet.tests import make_user
from wema_vas.models import Receipt, VirtualAccount
from wema_vas.services import is_restricted, process_notification
from wema_vas.tests import LIVE, VALIDATION, payload


@override_settings(WEMA_VAS=VALIDATION, BANK_ACCOUNT_PROVIDER="wema_vas",
                   WEMA_PARTNERSHIP_MODE="archive", WEMA_BILLER_MODE="active")
class ExistingUserValidationIsolationTests(TestCase):
    def setUp(self):
        self.user, _ = make_user("08088112233", "vas-restart@example.test", balance="0", tier=3)
        self.wallet = Wallet.objects.get(user=self.user)
        self.wallet.account_number = "0458811223"
        self.wallet.account_reference = f"WEMA-WALLET-{self.user.pk}"
        self.wallet.save(update_fields=["account_number", "account_reference"])
        self.sample = self.account("validation", "7118811223")

    def account(self, mode, number):
        return VirtualAccount.objects.create(user=self.user, number=number,
            display_name="Zitch/Ada Eze", encrypted_identity="encrypted-test-fixture",
            verification_reference="test-proof", consent_reference="test-explicit-consent",
            verified_at=timezone.now(), mode=mode, prefix=number[:3])

    def legacy_credit(self):
        incoming = {"referenceId": "legacy-after-711", "amount": "1000", "creditType": "Credit",
            "status": "Successfull", "date": timezone.now().isoformat(),
            "narration": "Late legacy deposit", "sender": "Customer"}
        apply_wema_credit(self.wallet, incoming)
        apply_wema_credit(self.wallet, incoming)
        self.wallet.refresh_from_db()

    def test_test_receipt_never_changes_old_account_or_real_ledger(self):
        self.legacy_credit()
        before = list(Transaction.objects.filter(user=self.user).values("pk", "amount", "transaction_status"))
        body = payload(self.sample)
        first, status = process_notification(body)
        second, repeated_status = process_notification(body)
        self.assertEqual((status, repeated_status, first), (200, 200, second))
        self.wallet.refresh_from_db()
        self.sample.refresh_from_db()
        self.assertEqual(self.wallet.account_number, "0458811223")
        self.assertEqual(self.wallet.balance, Decimal("1000"))
        self.assertEqual(self.sample.validation_balance, Decimal("1250"))
        self.assertEqual(list(Transaction.objects.filter(user=self.user).values("pk", "amount", "transaction_status")), before)
        self.assertEqual(Receipt.objects.get().state, Receipt.VALIDATION)
        self.assertIsNone(Receipt.objects.get().transaction_id)

    def test_test_enrollment_preserves_late_legacy_funds_and_bill_source(self):
        self.legacy_credit()
        self.assertEqual(wallet_balance_payload(self.user), {
            "balance": Decimal("1000"), "available_balance": Decimal("1000"),
            "historical_balance": Decimal("0"), "vas_balance": Decimal("0"),
        })
        txn = debit(self.user, "100", "Airtime — MTN")
        self.assertEqual(biller_source_for_transaction(txn.reference, amount="100"), "0458811223")
        self.assertIsNone(txn.bill_funding.vas_account_id)
        self.assertEqual(wallet_expected_balance(self.user.pk), Decimal("900"))
        with self.assertRaises(LimitExceeded):
            debit(self.user, "100", "Transfer to Other")

    def test_blocking_test_account_does_not_block_retained_legacy_bills(self):
        self.legacy_credit()
        VirtualAccount.objects.filter(pk=self.sample.pk).update(active=False)
        self.assertFalse(is_restricted(self.user))
        txn = debit(self.user, "100", "Airtime — MTN")
        self.assertIsNone(txn.bill_funding.vas_account_id)
        self.assertEqual(biller_source_for_transaction(txn.reference, amount="100"), "0458811223")

    def test_test_record_does_not_claim_a_live_cutover_to_provider_guards(self):
        with override_settings(BANK_ACCOUNT_PROVIDER="partnership", WEMA_PARTNERSHIP_MODE="active"):
            self.assertTrue(providers.partnership_new_business_allowed(self.user))
            self.assertFalse(providers._partnership_reference_blocked(source_account=self.wallet.account_number))
            self.assertTrue(providers._partnership_reference_blocked(source_account=self.sample.number))
        self.assertFalse(providers.partnership_new_business_allowed(self.user))
        self.assertTrue(providers._partnership_reference_blocked(source_account=self.wallet.account_number))

    def test_later_live_account_takes_financial_precedence_over_older_test_record(self):
        self.legacy_credit()
        live = self.account("live", "9998811223")
        with override_settings(WEMA_VAS=LIVE):
            process_notification(payload(live, paymentreference="LIVE-PAY", sessionid="LIVE-SESSION", amount="250"))
        with override_settings(WEMA_VAS_BILLER_ENABLED=False):
            shown = wallet_balance_payload(self.user)
            self.assertEqual(shown["balance"], Decimal("1250"))
            self.assertEqual(shown["vas_balance"], Decimal("250"))
            self.assertEqual(shown["historical_balance"], Decimal("1000"))
            self.assertEqual(shown["available_balance"], Decimal("0"))
            with self.assertRaises(LimitExceeded):
                debit(self.user, "100", "Airtime — MTN")
        with override_settings(BANK_ACCOUNT_PROVIDER="partnership", WEMA_PARTNERSHIP_MODE="active"):
            self.assertFalse(providers.partnership_new_business_allowed(self.user))
            self.assertTrue(providers._partnership_reference_blocked(source_account=self.wallet.account_number))
        self.assertEqual(VirtualAccount.objects.filter(user=self.user).count(), 2)

    def test_legacy_reconciliation_still_includes_711_only_customer(self):
        self.legacy_credit()
        with patch("utility.wema.wema_live", return_value=True), \
                patch("utility.wema.get_balance", return_value={"success": True, "balance_naira": Decimal("1000")}) as bank, \
                patch("utility.alerts.alert"):
            call_command("reconcile_balances", "--fail-nonzero", stdout=StringIO(), stderr=StringIO())
        bank.assert_called_once_with("0458811223")

    def test_test_notice_survives_retained_bill_capability(self):
        shown = customer_funding_account(self.user)
        self.assertTrue(shown["test_mode"])
        self.assertTrue(shown["bill_payments_available"])
        self.assertIn("test", shown["migration_message"].lower())
        self.assertNotEqual(shown["account_number"], self.sample.number)
