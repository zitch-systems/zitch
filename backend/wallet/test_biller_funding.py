"""Only Partnership funds new bills; retained liabilities/refunds stay attributable."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch

from django.db import IntegrityError, close_old_connections, connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone

from wallet.models import BillFundingBinding, BillFundingRefund, BankHistoryCheckpoint, Transaction, Wallet
from wallet.services import (DuplicateTransaction, LimitExceeded, bank_spend_error,
    biller_source_for_transaction, credit, customer_funding_account, debit, refund,
    settle_or_refund, wallet_balance_payload, wallet_expected_balance)
from wallet.tests import make_user
from wallet.legacy_funding import account_balance
from wallet.legacy_test_fixtures import account_fixture, retained_receipt, retained_bill
from wallet.partnership import legacy_funding_blockers


@override_settings(WEMA_PARTNERSHIP_MODE="active", BANK_ACCOUNT_PROVIDER="partnership",
                   WEMA_BILLER_MODE="active")
class PartnershipBillFundingTests(TestCase):
    def setUp(self):
        self.user, _ = make_user("08088990001", "bills@example.test", balance="1000", tier=3)
        self.wallet = Wallet.objects.get(user=self.user)
        self.wallet.account_number = "0458899001"
        self.wallet.save(update_fields=["account_number"])

    def test_immutable_own_source_authorizes_exact_pending_bill(self):
        txn = debit(self.user, "100", "Airtime — MTN")
        self.assertEqual(biller_source_for_transaction(txn.reference, amount="100"), self.wallet.account_number)
        self.assertIsNone(txn.bill_funding.vas_account_id)
        for amount, source in (("101", ""), ("100", "0123456789")):
            with self.subTest(amount=amount, source=source), self.assertRaises(LimitExceeded):
                biller_source_for_transaction(txn.reference, amount=amount, source_account=source)
        with self.assertRaises(LimitExceeded):
            biller_source_for_transaction("missing", amount="100")

    def test_disabled_biller_fails_before_ledger_debit(self):
        with override_settings(WEMA_BILLER_MODE="disabled"), self.assertRaises(LimitExceeded):
            debit(self.user, "100", "Airtime — MTN")
        self.assertFalse(BillFundingBinding.objects.exists())
        self.assertEqual(wallet_expected_balance(self.user.pk), Decimal("1000"))

    def test_partnership_pause_stops_new_bills(self):
        with override_settings(WEMA_PARTNERSHIP_MODE="archive"), self.assertRaises(LimitExceeded):
            debit(self.user, "100", "Airtime — MTN")
        self.assertFalse(BillFundingBinding.objects.exists())

    def test_missing_live_account_is_not_replaced_by_pool(self):
        self.wallet.account_number = ""
        self.wallet.save(update_fields=["account_number"])
        with patch("utility.wema.wema_live", return_value=True), self.assertRaises(LimitExceeded):
            debit(self.user, "100", "Airtime — MTN")

    def test_history_review_bank_cap_and_kyc_still_apply(self):
        with patch("utility.wema.wema_live", return_value=True):
            self.assertIn("review", bank_spend_error(self.user, 100, biller=True))
            checkpoint = BankHistoryCheckpoint.objects.get(wallet=self.wallet)
            checkpoint.opening_review_required = False
            checkpoint.save(update_fields=["opening_review_required"])
            with patch("utility.wema.bank_tier_limit", return_value=Decimal("150")):
                debit(self.user, "100", "Airtime — MTN")
                with self.assertRaises(LimitExceeded):
                    debit(self.user, "100", "Data — MTN")
        self.user.bvn_verified = self.user.nin_verified = False
        self.user.save(update_fields=["bvn_verified", "nin_verified"])
        with self.assertRaises(LimitExceeded):
            debit(self.user, "10", "Airtime — MTN")

    def test_duplicate_and_refund_do_not_repeat_binding_or_release(self):
        txn = debit(self.user, "100", "Airtime — MTN", idempotency_key="same-bill")
        with self.assertRaises(DuplicateTransaction):
            debit(self.user, "100", "Airtime — MTN", idempotency_key="same-bill")
        self.assertEqual(BillFundingBinding.objects.count(), 1)
        self.assertTrue(refund(txn))
        self.assertFalse(refund(txn))
        self.assertEqual(BillFundingRefund.objects.count(), 1)
        self.assertEqual(wallet_expected_balance(self.user.pk), Decimal("1000"))
        with self.assertRaises(LimitExceeded):
            biller_source_for_transaction(txn.reference, amount="100")


@override_settings(WEMA_PARTNERSHIP_MODE="active", BANK_ACCOUNT_PROVIDER="partnership",
                   WEMA_BILLER_MODE="active")
class HistoricalBillFundingTests(TestCase):
    def setUp(self):
        self.user, self.account = account_fixture()
        retained_receipt(self.account)
        self.wallet = Wallet.objects.get(user=self.user)
        Wallet.objects.filter(pk=self.wallet.pk).update(account_number="0458899001")

    def test_retirement_never_reserves_from_historical_account(self):
        before = Transaction.objects.count()
        for service in ("Airtime — MTN", "Transfer to Ada"):
            with self.subTest(service=service), self.assertRaises(LimitExceeded):
                debit(self.user, "100", service)
        self.assertFalse(BillFundingBinding.objects.exists())
        self.assertEqual(Transaction.objects.count(), before)
        self.assertEqual(account_balance(self.account), Decimal("1000"))

    def test_prior_pending_bill_cannot_authorize_another_provider_debit(self):
        from wallet.wema_callbacks import _authorize_payout
        bill = retained_bill(self.account)
        self.assertFalse(_authorize_payout(bill.reference, "", "127.0.0.1")[0])
        with self.assertRaises(LimitExceeded):
            biller_source_for_transaction(bill.reference, amount="100")
        self.assertEqual(account_balance(self.account), Decimal("900"))
        self.assertIn("unresolved_bills", legacy_funding_blockers(self.user))

    def test_prior_failed_bill_releases_once_to_original_historical_balance(self):
        bill = retained_bill(self.account, "200")
        binding_id = bill.bill_funding.pk
        source = bill.bill_funding.source_account
        self.assertEqual(settle_or_refund(bill, {"success": False}), "failed")
        self.assertEqual(settle_or_refund(bill, {"success": False}), "failed")
        self.assertFalse(refund(bill))
        self.assertEqual(BillFundingRefund.objects.count(), 1)
        self.assertEqual(BillFundingRefund.objects.get().binding_id, binding_id)
        self.assertEqual(bill.bill_funding.source_account, source)
        self.assertEqual(account_balance(self.account), Decimal("1000"))
        self.assertEqual(wallet_expected_balance(self.user.pk), Decimal("1000"))
        self.assertEqual(wallet_balance_payload(self.user)["available_balance"], 0)

    def test_prior_settlement_does_not_debit_or_refund_again(self):
        bill = retained_bill(self.account, "200")
        self.assertEqual(settle_or_refund(bill, {"success": True}), "success")
        self.assertEqual(settle_or_refund(bill, {"success": False}), "success")
        self.assertFalse(BillFundingRefund.objects.exists())
        self.assertEqual(account_balance(self.account), Decimal("800"))
        self.assertEqual(wallet_expected_balance(self.user.pk), Decimal("800"))

    def test_mutable_metadata_cannot_release_or_reassign_retained_money(self):
        bill = retained_bill(self.account)
        Transaction.objects.filter(pk=bill.pk).update(meta={"funding_rail": "partnership", "refunded": True})
        self.assertEqual(account_balance(self.account), Decimal("900"))
        self.assertIn("unresolved_bills", legacy_funding_blockers(self.user))
        binding = bill.bill_funding
        binding.source_account = "0000000000"
        with self.assertRaises(ValueError):
            binding.save()


@skipUnlessDBFeature("has_select_for_update")
class PostgresBillFundingTests(TransactionTestCase):
    def setUp(self):
        self.user, self.account = account_fixture()
        retained_receipt(self.account)
        self.wallet = Wallet.objects.get(user=self.user)

    def race(self, actions):
        barrier = Barrier(len(actions))
        def execute(action):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                return action()
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=len(actions)) as pool:
            return list(pool.map(execute, actions))

    def test_postgres_cannot_rewrite_or_delete_bill_attribution_and_release(self):
        txn = retained_bill(self.account)
        for action in (lambda: BillFundingBinding.objects.update(source_account="0000000000"),
                       lambda: BillFundingBinding.objects.all().delete()):
            with self.assertRaises(IntegrityError), transaction.atomic():
                action()
        refund(txn)
        for action in (lambda: BillFundingRefund.objects.update(created=timezone.now()),
                       lambda: BillFundingRefund.objects.all().delete()):
            with self.assertRaises(IntegrityError), transaction.atomic():
                action()

    def test_postgres_refuses_release_while_bill_pending_even_bypassing_save(self):
        txn = retained_bill(self.account)
        with self.assertRaises(IntegrityError), transaction.atomic():
            BillFundingRefund.objects.bulk_create([BillFundingRefund(binding=txn.bill_funding)])

    def test_concurrent_refunds_release_binding_and_wallet_exactly_once(self):
        txn = retained_bill(self.account)
        self.assertCountEqual(self.race([lambda: refund(txn), lambda: refund(txn)]), [True, False])
        self.assertEqual(BillFundingRefund.objects.count(), 1)
        self.assertEqual(account_balance(self.account), Decimal("1000"))
        self.assertEqual(Wallet.objects.get(pk=self.wallet.pk).balance, Decimal("1000"))

    def test_retired_authorization_and_refund_cannot_reopen_debit(self):
        from wallet.wema_callbacks import _authorize_payout
        txn = retained_bill(self.account)
        results = self.race([lambda: _authorize_payout(txn.reference, "", "127.0.0.1"), lambda: refund(txn)])
        self.assertFalse(results[0][0])
        self.assertTrue(results[1])
        self.assertFalse(_authorize_payout(txn.reference, "", "127.0.0.1")[0])
        self.assertEqual(account_balance(self.account), Decimal("1000"))
