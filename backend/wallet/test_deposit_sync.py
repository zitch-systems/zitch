"""A deposit made moments ago is in the balance the customer asks for."""
import json
import threading
import time
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from wallet import deposit_sync
from wallet.models import (
    BankHistoryCheckpoint,
    Transaction,
    TransactionAlertDelivery,
    Wallet,
)
from wallet.services import wema_account_reference
from wallet.tests import make_user

ALERTS_ON = {"EMAIL": True, "SMS": False, "WHATSAPP": True, "PUSH": True}


def _deposit(reference, amount="1500.00"):
    return {"referenceId": reference, "amount": amount, "creditType": "Credit",
            "status": "Successfull", "narration": "Bank transfer",
            "date": timezone.localdate().isoformat()}


def _statement(*rows):
    return {"success": True, "transactions": list(rows)}


@override_settings(PAYMENT_PROVIDER="wema", TXN_ALERTS=ALERTS_ON)
class BalanceRequestDepositTests(TestCase):
    def setUp(self):
        cache.clear()
        deposit_sync._inflight.clear()
        self.user, self.token = make_user("08071230010", "asks@zitch.test")
        self.wallet = Wallet.objects.get(user=self.user)
        self.wallet.account_number = "0451230010"
        self.wallet.account_reference = wema_account_reference(self.user)
        self.wallet.bank_name = "Wema Bank"
        self.wallet.save(update_fields=["account_number", "account_reference", "bank_name"])
        BankHistoryCheckpoint.objects.create(
            wallet=self.wallet, account_number="0451230010", opening_review_required=False)

    def _app(self, path):
        return Client().post(path, data=json.dumps({"access_token": self.token}),
                             content_type="application/json").json()

    def test_a_deposit_sent_moments_ago_is_in_the_whatsapp_balance_reply(self):
        from whatsapp import router

        funding = {"has_account": True, "available": True, "can_fund": True,
                   "account_setup_state": "ready", "account_number": "0451230010",
                   "transfers_available": True, "bill_payments_available": True}
        with patch("wallet.deposit_sync.statement_live", return_value=True), \
             patch("utility.wema.get_transactions",
                   return_value=_statement(_deposit("DEP-CHAT"))), \
             patch.object(router, "customer_funding_account", return_value=funding), \
             patch.object(router, "reply") as reply:
            router._do_balance(self.user, "2348071230010")

        self.assertIn("₦1,500.00", reply.call_args.args[1])

    def test_the_app_transaction_list_includes_a_deposit_sent_moments_ago(self):
        with patch("wallet.deposit_sync.statement_live", return_value=True), \
             patch("utility.wema.get_transactions",
                   return_value=_statement(_deposit("DEP-LIST"))):
            history = self._app("/api/user-transaction-history/")

        self.assertEqual(len(history["all_site_transactions"]), 1)

    def test_the_app_balance_includes_a_deposit_sent_moments_ago(self):
        with patch("wallet.deposit_sync.statement_live", return_value=True), \
             patch("utility.wema.get_transactions",
                   return_value=_statement(_deposit("DEP-APP"))):
            balance = self._app("/api/wallet_balance/")

        self.assertEqual(Decimal(balance["wallet"]), Decimal("1500.00"))

    def test_a_deposit_found_this_way_queues_every_alert_including_whatsapp(self):
        with patch("wallet.deposit_sync.statement_live", return_value=True), \
             patch("utility.wema.get_transactions",
                   return_value=_statement(_deposit("DEP-ALERT"))):
            self.assertEqual(deposit_sync.sync_wallet_deposits(self.user), 1)

        credit = Transaction.objects.get(user=self.user, direction=Transaction.IN)
        channels = set(TransactionAlertDelivery.objects.filter(
            transaction=credit).values_list("channel", flat=True))
        self.assertIn("whatsapp", channels)

    def test_reading_the_same_statement_again_never_credits_twice(self):
        with patch("wallet.deposit_sync.statement_live", return_value=True), \
             patch("utility.wema.get_transactions",
                   return_value=_statement(_deposit("DEP-ONCE"))):
            deposit_sync.sync_wallet_deposits(self.user)
            cache.clear()  # past the throttle: the bank is asked again
            self.assertEqual(deposit_sync.sync_wallet_deposits(self.user), 0)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("1500.00"))
        self.assertEqual(Transaction.objects.filter(user=self.user).count(), 1)

    def test_repeated_balance_requests_do_not_ask_the_bank_each_time(self):
        with patch("wallet.deposit_sync.statement_live", return_value=True), \
             patch("utility.wema.get_transactions", return_value=_statement()) as history:
            deposit_sync.sync_wallet_deposits(self.user)
            deposit_sync.sync_wallet_deposits(self.user)

        history.assert_called_once()

    def test_the_worker_polls_rate_limit_backoff_is_respected(self):
        cache.set(deposit_sync.BACKOFF_KEY, True, timeout=60)
        with patch("wallet.deposit_sync.statement_live", return_value=True), \
             patch("utility.wema.get_transactions") as history:
            self.assertEqual(deposit_sync.sync_wallet_deposits(self.user), 0)
        history.assert_not_called()

    def test_a_rate_limited_read_backs_off_every_reader(self):
        limited = {"success": False, "transactions": [], "diagnostic": {"http_status": 429}}
        with patch("wallet.deposit_sync.statement_live", return_value=True), \
             patch("utility.wema.get_transactions", return_value=limited):
            self.assertEqual(deposit_sync.sync_wallet_deposits(self.user), 0)
        self.assertTrue(cache.get(deposit_sync.BACKOFF_KEY))

    def test_without_statement_access_the_bank_is_not_called(self):
        with patch("wallet.deposit_sync.statement_live", return_value=False), \
             patch("utility.wema.get_transactions") as history:
            self.assertEqual(deposit_sync.sync_wallet_deposits(self.user), 0)
        history.assert_not_called()

    def test_a_customer_without_a_partner_account_never_reaches_the_bank(self):
        user, _ = make_user("08071230011", "noacct@zitch.test")
        with patch("wallet.deposit_sync.statement_live", return_value=True), \
             patch("utility.wema.get_transactions") as history:
            self.assertEqual(deposit_sync.sync_wallet_deposits(user), 0)
        history.assert_not_called()

    def test_a_bank_error_never_breaks_the_balance_request(self):
        with patch("wallet.deposit_sync.statement_live", return_value=True), \
             patch("utility.wema.get_transactions", side_effect=RuntimeError("bank down")):
            self.assertEqual(deposit_sync.sync_wallet_deposits(self.user), 0)


class BoundedRefreshTests(TestCase):
    """The threaded path: a slow bank delays a reply by WAIT seconds at most."""

    def setUp(self):
        cache.clear()
        deposit_sync._inflight.clear()
        self.user = SimpleNamespace(pk=987654)
        self.account = SimpleNamespace(pk=123456)

    def _wait_for_reads_to_finish(self):
        deadline = time.monotonic() + 3
        while deposit_sync._inflight and time.monotonic() < deadline:
            time.sleep(0.01)

    def test_a_slow_bank_holds_the_reply_for_the_wait_budget_only(self):
        release = threading.Event()

        def slow_read(_wallet, _user_pk):
            release.wait(3)
            return 1

        with patch("wallet.deposit_sync.statement_live", return_value=True), \
             patch("wallet.deposit_sync._customer_account", return_value=self.account), \
             patch("wallet.deposit_sync._read_customer_statement", side_effect=slow_read):
            started = time.monotonic()
            result = deposit_sync.refresh_deposits_briefly(self.user, wait_seconds=0.1)
            elapsed = time.monotonic() - started
            release.set()
            self._wait_for_reads_to_finish()

        self.assertIsNone(result)
        self.assertLess(elapsed, 1.0)

    def test_parallel_balance_and_history_requests_share_one_bank_read(self):
        release = threading.Event()
        reads = []

        def slow_read(_wallet, _user_pk):
            reads.append(True)
            release.wait(3)
            return 1

        with patch("wallet.deposit_sync.statement_live", return_value=True), \
             patch("wallet.deposit_sync._customer_account", return_value=self.account), \
             patch("wallet.deposit_sync._read_customer_statement", side_effect=slow_read):
            first = deposit_sync.refresh_deposits_briefly(self.user, wait_seconds=0.05)
            threading.Timer(0.1, release.set).start()
            second = deposit_sync.refresh_deposits_briefly(self.user, wait_seconds=3)
            self._wait_for_reads_to_finish()

        self.assertIsNone(first)
        self.assertEqual(second, 1)
        self.assertEqual(len(reads), 1)

    def test_zero_wait_turns_the_balance_check_off(self):
        with patch("wallet.deposit_sync.statement_live", return_value=True), \
             patch("wallet.deposit_sync._read_customer_statement") as read:
            self.assertIsNone(deposit_sync.refresh_deposits_briefly(self.user, wait_seconds=0))
        read.assert_not_called()
