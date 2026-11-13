"""Deposits show up within seconds, not at the next 10-minute cron run."""
import json
import threading
import time
from datetime import timedelta
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


class _PartnerAccountMixin:
    def setUp(self):
        cache.clear()
        deposit_sync._logged_at.clear()
        deposit_sync._inflight.clear()

    def partner_account(self, phone, email, number):
        user, token = make_user(phone, email)
        wallet = Wallet.objects.get(user=user)
        wallet.account_number = number
        wallet.account_reference = wema_account_reference(user)
        wallet.bank_name = "Wema Bank"
        wallet.save(update_fields=["account_number", "account_reference", "bank_name"])
        BankHistoryCheckpoint.objects.create(
            wallet=wallet, account_number=number, opening_review_required=False)
        return user, token, wallet


@override_settings(PAYMENT_PROVIDER="wema", TXN_ALERTS=ALERTS_ON)
class WorkerDepositPollTests(_PartnerAccountMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user, _, self.wallet = self.partner_account(
            "08071230001", "poll@zitch.test", "0451230001")

    def test_a_new_deposit_is_credited_and_queues_every_alert_including_whatsapp(self):
        with patch("wallet.deposit_sync.statement_live", return_value=True), \
             patch("utility.wema.get_transactions",
                   return_value=_statement(_deposit("DEP-1"))) as history:
            summary = deposit_sync.poll_deposits()

        history.assert_called_once()
        self.assertEqual(summary["credited"], 1)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("1500.00"))
        credit = Transaction.objects.get(user=self.user, direction=Transaction.IN)
        channels = set(TransactionAlertDelivery.objects.filter(
            transaction=credit).values_list("channel", flat=True))
        self.assertIn("whatsapp", channels)

    def test_reading_the_same_statement_again_never_credits_twice(self):
        with patch("wallet.deposit_sync.statement_live", return_value=True), \
             patch("utility.wema.get_transactions",
                   return_value=_statement(_deposit("DEP-ONCE"))):
            deposit_sync.poll_deposits()
            cache.clear()  # past the throttle: the bank is asked again
            second = deposit_sync.poll_deposits()

        self.assertEqual(second["credited"], 0)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("1500.00"))
        self.assertEqual(Transaction.objects.filter(user=self.user).count(), 1)

    def test_an_account_read_moments_ago_is_not_asked_again(self):
        with patch("wallet.deposit_sync.statement_live", return_value=True), \
             patch("utility.wema.get_transactions",
                   return_value=_statement()) as history:
            deposit_sync.poll_deposits()
            deposit_sync.poll_deposits()

        history.assert_called_once()

    def test_without_statement_access_the_bank_is_not_called_and_the_cause_is_logged_once(self):
        with patch("wallet.deposit_sync.statement_live", return_value=False), \
             patch("utility.wema.get_transactions") as history, \
             self.assertLogs("wallet", level="WARNING") as logs:
            first = deposit_sync.poll_deposits()
            deposit_sync.poll_deposits()

        history.assert_not_called()
        self.assertTrue(first["skipped"])
        self.assertEqual(sum("wema_deposit_poll_not_live" in line for line in logs.output), 1)

    def test_the_stalest_account_is_read_first_and_each_pass_is_capped(self):
        _, _, fresh = self.partner_account("08071230002", "fresh@zitch.test", "0451230002")
        now = timezone.now()
        BankHistoryCheckpoint.objects.filter(wallet=self.wallet).update(
            last_completed_at=now - timedelta(minutes=5))
        BankHistoryCheckpoint.objects.filter(wallet=fresh).update(
            last_completed_at=now - timedelta(seconds=20))

        with patch("wallet.deposit_sync.statement_live", return_value=True), \
             patch("utility.wema.get_transactions",
                   return_value=_statement()) as history:
            summary = deposit_sync.poll_deposits(limit=1)

        self.assertEqual(summary["checked"], 1)
        self.assertEqual(history.call_args.args[0], "0451230001")

    def test_a_second_worker_instance_skips_while_one_is_polling(self):
        cache.add(deposit_sync.POLL_LOCK_KEY, "another-instance", timeout=60)
        with patch("wallet.deposit_sync.statement_live", return_value=True), \
             patch("utility.wema.get_transactions") as history:
            summary = deposit_sync.poll_deposits()

        self.assertTrue(summary["skipped"])
        history.assert_not_called()

    def test_one_account_failing_does_not_stop_the_others(self):
        self.partner_account("08071230003", "other@zitch.test", "0451230003")
        with patch("wallet.deposit_sync.statement_live", return_value=True), \
             patch("wallet.deposit_sync._read_statement",
                   side_effect=[RuntimeError("bank timeout"), {"credited": 1}]):
            summary = deposit_sync.poll_deposits()

        self.assertEqual(summary["checked"], 2)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["credited"], 1)


@override_settings(PAYMENT_PROVIDER="wema", TXN_ALERTS=ALERTS_ON)
class BalanceRequestDepositTests(_PartnerAccountMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user, self.token, self.wallet = self.partner_account(
            "08071230010", "asks@zitch.test", "0451230010")

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

    def _app(self, path):
        return Client().post(path, data=json.dumps({"access_token": self.token}),
                             content_type="application/json").json()

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


class WorkerSchedulesTheDepositPollTests(TestCase):
    @override_settings(WHATSAPP_WORKER_RECONCILE=False)
    def test_the_continuous_worker_polls_deposits_and_one_shot_runs_do_not(self):
        import signal
        from io import StringIO

        from django.core.management import call_command

        handlers = {}

        class InlineThread:
            def __init__(self, *, target, **_kwargs):
                self.target = target

            def start(self):
                self.target()

        def register(signum, handler):
            handlers[signum] = handler

        def drain_once(_batch):
            handlers[signal.SIGTERM](signal.SIGTERM, None)
            return 0, 0

        worker = "whatsapp.management.commands.whatsapp_worker"
        with patch(f"{worker}.signal.signal", side_effect=register), \
             patch(f"{worker}.threading.Thread", InlineThread), \
             patch(f"{worker}.time.monotonic", side_effect=[0, 0, 0, 0]), \
             patch(f"{worker}.time.sleep"), \
             patch(f"{worker}.close_old_connections"), \
             patch(f"{worker}.connections.close_all"), \
             patch(f"{worker}.process_once", side_effect=drain_once), \
             patch("wallet.services.repair_missing_funding_accounts"), \
             patch("wallet.alerts.retry_pending_whatsapp_alerts", return_value=0), \
             patch("wallet.deposit_sync.poll_deposits") as poll:
            call_command("whatsapp_worker", stdout=StringIO())
        poll.assert_called_once()

        with patch(f"{worker}.process_once", return_value=(0, 0)), \
             patch("wallet.deposit_sync.poll_deposits") as poll:
            call_command("whatsapp_worker", "--once", stdout=StringIO())
        poll.assert_not_called()
