"""Preserve legacy bills while keeping collection-funded spend attributable."""
from decimal import Decimal
from inspect import unwrap
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.db import IntegrityError, close_old_connections, connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone

from wallet.models import BillFundingBinding, BillFundingRefund, BankHistoryCheckpoint, Transaction, Wallet
from wallet.services import (DuplicateTransaction, LimitExceeded, bank_spend_error,
    biller_source_for_transaction, credit, customer_funding_account, customer_spendable_balance,
    debit, refund, settle_or_refund, wallet_balance_payload, wallet_expected_balance)
from wallet.tests import make_user
from wema_vas.models import VirtualAccount
from wema_vas.services import account_balance, mini_statement, process_notification
from wema_vas.tests import LIVE, account_fixture, payload

COLLECTION = "0123456789"
VAS = {**LIVE, "ENABLE_ENROLLMENT": True, "LIVE_APPROVAL_REFERENCE": "bank-validation-test",
       "RELEASE_PHASE": "general", "GENERAL_APPROVAL_REFERENCE": "launch-test",
       "COLLECTION_ACCOUNT": COLLECTION}


@override_settings(WEMA_PARTNERSHIP_MODE="archive", BANK_ACCOUNT_PROVIDER="wema_vas",
                   WEMA_BILLER_MODE="active")
class RetainedLegacyBillTests(TestCase):
    def setUp(self):
        self.user, _ = make_user("08088990001", "legacy-bills@example.test", balance="1000", tier=3)
        self.wallet = Wallet.objects.get(user=self.user)
        self.wallet.account_number = "0458899001"
        self.wallet.save(update_fields=["account_number"])

    def test_archive_preserves_bills_with_immutable_own_source_but_not_transfers(self):
        txn = debit(self.user, "100", "Airtime — MTN")
        self.assertEqual(biller_source_for_transaction(txn.reference, amount="100"), self.wallet.account_number)
        self.assertIsNone(txn.bill_funding.vas_account_id)
        self.assertTrue(customer_funding_account(self.user)["bill_payments_available"])
        with self.assertRaises(LimitExceeded):
            debit(self.user, "100", "Transfer to Other")

    def test_historical_unbound_pending_bill_authorization_is_preserved(self):
        from wallet.wema_callbacks import _authorize_payout
        txn = Transaction.objects.create(user=self.user, amount="100", service="Airtime — MTN",
                                         direction=Transaction.OUT, reference="prior-release-bill")
        allowed, _ = _authorize_payout(txn.reference, "", "127.0.0.1")
        self.assertTrue(allowed)

    def test_disabled_biller_fails_before_ledger_debit(self):
        with override_settings(WEMA_BILLER_MODE="disabled"), self.assertRaises(LimitExceeded):
            debit(self.user, "100", "Airtime — MTN")
        self.assertFalse(BillFundingBinding.objects.exists())
        self.assertEqual(wallet_expected_balance(self.user.pk), Decimal("1000"))

    def test_legacy_source_missing_is_not_replaced_by_pool(self):
        self.wallet.account_number = ""
        self.wallet.save(update_fields=["account_number"])
        with self.assertRaises(LimitExceeded):
            debit(self.user, "100", "Airtime — MTN")

    def test_legacy_balance_presentation_remains_unchanged(self):
        self.assertEqual(wallet_balance_payload(self.user), {
            "balance": Decimal("1000"), "available_balance": Decimal("1000"),
            "historical_balance": Decimal("0"), "vas_balance": Decimal("0"),
        })

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
        self.user.bvn_verified = False
        self.user.save(update_fields=["bvn_verified"])
        with self.assertRaises(LimitExceeded):
            debit(self.user, "10", "Airtime — MTN")


@override_settings(WEMA_VAS=VAS, WEMA_PARTNERSHIP_MODE="archive", BANK_ACCOUNT_PROVIDER="wema_vas",
                   WEMA_BILLER_MODE="active", WEMA_VAS_BILLER_ENABLED=True,
                   WEMA_VAS_BILLER_SOURCE_ACCOUNT=COLLECTION,
                   WEMA_VAS_BILLER_APPROVAL_REFERENCE="bank-biller-test-approval")
class VasBillFundingTests(TestCase):
    def setUp(self):
        self.user, self.account = account_fixture()
        for key, value in {"phone_verified": True, "email_verified": True,
                           "bvn_verified": True, "nin_verified": True, "tier": 3}.items():
            setattr(self.user, key, value)
        self.user.save()
        process_notification(payload(self.account, created_at=timezone.now().isoformat(), amount="1000.00"))
        self.wallet = Wallet.objects.get(user=self.user)

    def bill(self, amount="100", **kwargs):
        return debit(self.user, amount, "Airtime — MTN", **kwargs)

    def test_collection_binding_matches_exact_amount_and_source(self):
        txn = self.bill()
        self.assertEqual(biller_source_for_transaction(txn.reference, amount="100"), COLLECTION)
        self.assertEqual(txn.bill_funding.vas_account_id, self.account.pk)
        self.assertEqual(account_balance(self.account), Decimal("900"))
        for amount, source in (("101", ""), ("100", self.account.number), ("100", "0458899001")):
            with self.subTest(amount=amount, source=source), self.assertRaises(LimitExceeded):
                biller_source_for_transaction(txn.reference, amount=amount, source_account=source)
        with self.assertRaises(LimitExceeded):
            biller_source_for_transaction("missing", amount="100", source_account=COLLECTION)

    def test_late_legacy_or_unattributed_credits_do_not_become_vas_spendable(self):
        credit(self.user, "5000", "Historical Partnership deposit")
        with self.assertRaises(LimitExceeded):
            self.bill("1001")
        self.assertEqual(account_balance(self.account), Decimal("1000"))

    def test_customer_balance_separates_legacy_funds_from_available_vas_bills(self):
        from accounts.models import AccessToken
        credit(self.user, "5000", "Historical Partnership deposit")
        state = wallet_balance_payload(self.user)
        self.assertEqual(state, {"balance": Decimal("6000"), "available_balance": Decimal("1000"),
                                 "historical_balance": Decimal("5000"), "vas_balance": Decimal("1000")})
        self.assertEqual(customer_spendable_balance(self.user), Decimal("1000"))
        token = AccessToken.issue(self.user).key
        response = self.client.post("/api/wallet_balance/", data={"access_token": token},
                                    content_type="application/json")
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["wallet"], "6000.00")
        self.assertEqual(result["available_balance"], "1000.00")
        self.assertEqual(result["historical_balance"], "5000.00")
        self.assertEqual(result["vas_balance"], "1000.00")

    def test_disabled_or_restricted_vas_balance_is_retained_but_not_available(self):
        credit(self.user, "5000", "Historical Partnership deposit")
        with override_settings(WEMA_VAS_BILLER_ENABLED=False):
            state = wallet_balance_payload(self.user)
            self.assertEqual(state["available_balance"], 0)
            self.assertEqual(state["historical_balance"], 5000)
            self.assertEqual(state["vas_balance"], 1000)
        VirtualAccount.objects.filter(pk=self.account.pk).update(active=False)
        self.assertEqual(customer_spendable_balance(self.user), 0)
        self.assertEqual(wallet_balance_payload(self.user)["balance"], 6000)

    def test_display_balance_tracks_reservations_and_once_only_refunds(self):
        credit(self.user, "5000", "Historical Partnership deposit")
        txn = self.bill("200")
        self.assertEqual(customer_spendable_balance(self.user), 800)
        self.assertEqual(wallet_balance_payload(self.user)["historical_balance"], 5000)
        refund(txn)
        refund(txn)
        self.assertEqual(customer_spendable_balance(self.user), 1000)
        self.assertEqual(wallet_balance_payload(self.user)["historical_balance"], 5000)

    def test_double_spend_reservations_count_pending_bills(self):
        self.bill("700")
        with self.assertRaises(LimitExceeded):
            self.bill("400")
        self.assertEqual(account_balance(self.account), Decimal("300"))

    def test_settlement_does_not_debit_again_and_failure_releases_once(self):
        delivered = self.bill("100")
        self.assertEqual(settle_or_refund(delivered, {"success": True}), "success")
        self.assertEqual(settle_or_refund(delivered, {"success": False}), "success")
        failed = self.bill("200")
        self.assertEqual(settle_or_refund(failed, {"success": False}), "failed")
        self.assertEqual(settle_or_refund(failed, {"success": False}), "failed")
        self.assertFalse(refund(failed))
        self.assertEqual(BillFundingRefund.objects.count(), 1)
        self.assertEqual(account_balance(self.account), Decimal("900"))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, account_balance(self.account))
        self.assertEqual(wallet_expected_balance(self.user.pk), self.wallet.balance)
        movements = mini_statement(self.account)["transactions"]
        self.assertEqual(len(movements), 4)
        self.assertEqual(sum(Decimal(row["amount"]) * (1 if row["direction"] == "Credit" else -1)
                             for row in movements), Decimal("900"))

    def test_explicit_refund_path_releases_once_without_another_ledger_credit(self):
        txn = self.bill()
        self.assertTrue(refund(txn))
        self.assertFalse(refund(txn))
        self.assertEqual(account_balance(self.account), Decimal("1000"))
        self.assertEqual(Transaction.objects.filter(direction=Transaction.IN).count(), 1)
        with self.assertRaises(LimitExceeded):
            biller_source_for_transaction(txn.reference, amount="100")

    def test_idempotent_debit_collision_cannot_create_second_binding(self):
        self.bill(idempotency_key="same-bill")
        with self.assertRaises(DuplicateTransaction):
            self.bill(idempotency_key="same-bill")
        self.assertEqual(BillFundingBinding.objects.count(), 1)
        self.assertEqual(account_balance(self.account), Decimal("900"))

    def test_missing_approval_and_inconsistent_source_fail_closed(self):
        for changes in ({"WEMA_VAS_BILLER_ENABLED": False},
                        {"WEMA_VAS_BILLER_APPROVAL_REFERENCE": "pending"},
                        {"WEMA_VAS_BILLER_SOURCE_ACCOUNT": "7110000001"},
                        {"WEMA_VAS_BILLER_SOURCE_ACCOUNT": "0101010101"}):
            with self.subTest(changes=changes), override_settings(**changes), self.assertRaises(LimitExceeded):
                self.bill()
        self.assertFalse(BillFundingBinding.objects.exists())

    def test_vas_bills_never_consult_former_nuban_bank_tier(self):
        self.wallet.account_number = "0458899001"
        self.wallet.save(update_fields=["account_number"])
        with patch("utility.wema.bank_tier_limit", side_effect=AssertionError("obsolete NUBAN cap")):
            txn = self.bill()
        self.assertEqual(biller_source_for_transaction(txn.reference, amount="100"), COLLECTION)
        with self.assertRaises(LimitExceeded):
            debit(self.user, "100", "Transfer to Other")

    def test_block_denies_bills_but_retains_refunds_and_statement(self):
        from wallet.wema_callbacks import _authorize_payout
        from wema_vas.views import block
        txn = self.bill()
        self.assertTrue(_authorize_payout(txn.reference, "", "127.0.0.1")[0])
        unwrap(block)({"accountnumber": self.account.number, "blockreason": "Bank investigation"})
        self.assertFalse(_authorize_payout(txn.reference, "", "127.0.0.1")[0])
        with self.assertRaises(LimitExceeded):
            self.bill()
        with self.assertRaises(LimitExceeded):
            biller_source_for_transaction(txn.reference, amount="100")
        self.assertTrue(refund(txn))
        self.assertEqual(account_balance(self.account), Decimal("1000"))
        self.assertEqual(len(mini_statement(self.account)["transactions"]), 3)

    def test_metadata_cannot_reassign_funding_or_release_the_reservation(self):
        from wallet.wema_callbacks import _authorize_payout
        txn = self.bill()
        Transaction.objects.filter(pk=txn.pk).update(meta={"funding_rail": "partnership", "refunded": True})
        self.assertEqual(biller_source_for_transaction(txn.reference, amount="100"), COLLECTION)
        self.assertEqual(account_balance(self.account), Decimal("900"))
        self.assertTrue(_authorize_payout(txn.reference, "", "127.0.0.1")[0])
        binding = txn.bill_funding
        binding.source_account = "0000000000"
        with self.assertRaises(ValueError):
            binding.save()

    def test_customer_state_does_not_expose_collection_source_or_approval(self):
        state = customer_funding_account(self.user)
        self.assertTrue(state["bill_payments_available"])
        self.assertFalse(state["transfers_available"])
        self.assertNotIn(COLLECTION, str(state))
        self.assertNotIn("bank-biller-test-approval", str(state))


@skipUnlessDBFeature("has_select_for_update")
@override_settings(WEMA_VAS=VAS, WEMA_PARTNERSHIP_MODE="archive", BANK_ACCOUNT_PROVIDER="wema_vas",
                   WEMA_BILLER_MODE="active", WEMA_VAS_BILLER_ENABLED=True,
                   WEMA_VAS_BILLER_SOURCE_ACCOUNT=COLLECTION,
                   WEMA_VAS_BILLER_APPROVAL_REFERENCE="bank-biller-test-approval")
class PostgresBillFundingTests(TransactionTestCase):
    def setUp(self):
        VasBillFundingTests.setUp(self)

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

    def test_concurrent_bills_reserve_only_the_customers_available_vas_money(self):
        def buy():
            user = type(self.user).objects.get(pk=self.user.pk)
            try:
                debit(user, "700", "Airtime — MTN")
                return "reserved"
            except LimitExceeded:
                return "refused"
        self.assertCountEqual(self.race([buy, buy]), ["reserved", "refused"])
        self.assertEqual(BillFundingBinding.objects.count(), 1)
        self.assertEqual(account_balance(self.account), Decimal("300"))

    def test_concurrent_refunds_release_binding_and_wallet_exactly_once(self):
        txn = debit(self.user, "100", "Airtime — MTN")
        self.assertCountEqual(self.race([lambda: refund(txn), lambda: refund(txn)]), [True, False])
        self.assertEqual(BillFundingRefund.objects.count(), 1)
        self.assertEqual(account_balance(self.account), Decimal("1000"))
        self.assertEqual(Wallet.objects.get(pk=self.wallet.pk).balance, Decimal("1000"))

    def test_authorization_and_refund_share_lock_order_and_do_not_reopen_debit(self):
        from wallet.wema_callbacks import _authorize_payout
        txn = debit(self.user, "100", "Airtime — MTN")
        results = self.race([lambda: _authorize_payout(txn.reference, "", "127.0.0.1"),
                             lambda: refund(txn)])
        self.assertTrue(results[1])
        self.assertFalse(_authorize_payout(txn.reference, "", "127.0.0.1")[0])
        self.assertEqual(account_balance(self.account), Decimal("1000"))

    def test_block_serializes_with_bill_reservation_and_denies_later_authorization(self):
        from wallet.wema_callbacks import _authorize_payout
        from wema_vas.views import block
        def buy():
            try:
                return debit(type(self.user).objects.get(pk=self.user.pk), "100", "Airtime — MTN").reference
            except LimitExceeded:
                return None
        reference, _ = self.race([buy, lambda: unwrap(block)({
            "accountnumber": self.account.number, "blockreason": "Bank investigation"})])
        if reference:
            self.assertFalse(_authorize_payout(reference, "", "127.0.0.1")[0])
        self.assertFalse(VirtualAccount.objects.get(pk=self.account.pk).active)
