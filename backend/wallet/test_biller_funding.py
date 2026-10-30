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
from banking.test_fixtures import make_bank_user as make_user
from banking.test_client import CONFIG
from wallet.funding_test_fixtures import funding_hold
from wallet.partnership import legacy_funding_blockers


@override_settings(WEMA_PARTNERSHIP_MODE="active", BANK_ACCOUNT_PROVIDER="partnership",
                   WEMA_BILLER_MODE="active")
@override_settings(NINEPSB=CONFIG)
class PartnershipBillFundingTests(TestCase):
    def setUp(self):
        self.user, _ = make_user("08088990001", "bills@example.test", balance="1000", tier=3)
        self.wallet = Wallet.objects.get(user=self.user)


    def test_immutable_own_source_authorizes_exact_pending_bill(self):
        txn = debit(self.user, "100", "Airtime — MTN")
        self.assertEqual(biller_source_for_transaction(txn.reference, amount="100"), self.wallet.account_number)
        for amount, source in (("101", ""), ("100", "0123456789")):
            with self.subTest(amount=amount, source=source), self.assertRaises(LimitExceeded):
                biller_source_for_transaction(txn.reference, amount=amount, source_account=source)
        with self.assertRaises(LimitExceeded):
            biller_source_for_transaction("missing", amount="100")

    def test_disabled_biller_fails_before_ledger_debit(self):
        with override_settings(NINEPSB={**CONFIG, "VAS_ENABLED": False}), self.assertRaises(LimitExceeded):
            debit(self.user, "100", "Airtime — MTN")
        self.assertFalse(BillFundingBinding.objects.exists())
        self.assertEqual(wallet_expected_balance(self.user.pk), Decimal("1000"))

    def test_partnership_pause_stops_new_bills(self):
        with override_settings(NINEPSB={**CONFIG, "ENABLED": False}), self.assertRaises(LimitExceeded):
            debit(self.user, "100", "Airtime — MTN")
        self.assertFalse(BillFundingBinding.objects.exists())

    def test_missing_live_account_is_not_replaced_by_pool(self):
        from banking.models import BankAccount
        from wallet.tests import make_user as unbanked_user
        self.user, _ = unbanked_user("08088990003", "unbanked@example.test", balance="1000")
        self.wallet = self.user.wallet
        with self.assertRaises(LimitExceeded):
            debit(self.user, "100", "Airtime — MTN")

    def test_opening_review_limits_and_contact_revocation_still_apply(self):
        from banking.models import BankAccount
        BankAccount.objects.filter(wallet=self.wallet).update(opening_review_required=True)
        with self.assertRaises(LimitExceeded):
            debit(self.user, "100", "Airtime — MTN")
        BankAccount.objects.filter(wallet=self.wallet).update(opening_review_required=False)
        with override_settings(NINEPSB={**CONFIG, "SINGLE_OUTBOUND_LIMITS": '{"3":"150"}'}):
            debit(self.user, "100", "Airtime — MTN")
            with self.assertRaises(LimitExceeded):
                debit(self.user, "151", "Data — MTN")
        self.user.phone_verified = False
        self.user.save(update_fields=["phone_verified"])
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




@skipUnlessDBFeature("has_select_for_update")
@override_settings(NINEPSB=CONFIG)
class PostgresBillFundingTests(TransactionTestCase):
    def setUp(self):
        self.user, _ = make_user("08088990002", "postgres-bill@example.test", balance="1000", tier=3)
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
        txn = debit(self.user, "100", "Airtime — MTN")
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
        txn = debit(self.user, "100", "Airtime — MTN")
        with self.assertRaises(IntegrityError), transaction.atomic():
            BillFundingRefund.objects.bulk_create([BillFundingRefund(binding=txn.bill_funding)])

    def test_concurrent_refunds_release_binding_and_wallet_exactly_once(self):
        txn = debit(self.user, "100", "Airtime — MTN")
        self.assertCountEqual(self.race([lambda: refund(txn), lambda: refund(txn)]), [True, False])
        self.assertEqual(BillFundingRefund.objects.count(), 1)
        self.assertEqual(Wallet.objects.get(pk=self.wallet.pk).balance, Decimal("1000"))
