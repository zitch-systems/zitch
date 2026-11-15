"""A customer who has just been shown how to pay in is watched closely.

Wema sends no deposit notification (none has reached us since October), so a
deposit waits for its account's turn in the worker's minute poll or for the
cron. A customer looking at their account number is about to pay in, so the
worker reads that account every few seconds for a while, and the credit and
its alert follow the bank within seconds.
"""
import json
import time
from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from wallet import deposit_sync
from wallet.models import BankHistoryCheckpoint, Transaction, Wallet
from wallet.services import provision_wema_account, wema_account_reference
from wallet.tests import make_user


def _deposit(reference, amount="1500.00"):
    return {"referenceId": reference, "amount": amount, "creditType": "Credit",
            "status": "Successfull", "narration": "Bank transfer",
            "date": timezone.localdate().isoformat()}


def _statement(*rows):
    return {"success": True, "transactions": list(rows)}


def watched():
    return cache.get(deposit_sync.WATCH_KEY) or {}


@override_settings(PAYMENT_PROVIDER="wema", WEMA_EXPECTED_DEPOSIT_MINUTES=15,
                   WEMA_EXPECTED_DEPOSIT_POLL_SECONDS=10)
class ExpectedDepositTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user, self.token = make_user("08071230020", "expects@zitch.test")
        self.wallet = Wallet.objects.get(user=self.user)
        self.wallet.account_number = "0451230020"
        self.wallet.account_reference = wema_account_reference(self.user)
        self.wallet.bank_name = "Wema Bank"
        self.wallet.save(update_fields=["account_number", "account_reference", "bank_name"])
        BankHistoryCheckpoint.objects.create(
            wallet=self.wallet, account_number="0451230020", opening_review_required=False)

    def poll(self, statement=None, **kwargs):
        with patch("wallet.deposit_sync.statement_live", return_value=True), \
             patch("utility.wema.get_transactions",
                   return_value=statement or _statement()) as read:
            credited = deposit_sync.poll_expected_deposits(**kwargs)
        return credited, read.call_count

    def test_a_watched_account_has_its_deposit_credited_by_the_next_poll(self):
        deposit_sync.expect_deposit(self.user)
        credited, reads = self.poll(_statement(_deposit("DEP-WATCH")))
        self.assertEqual((credited, reads), (1, 1))
        self.assertTrue(Transaction.objects.filter(user=self.user,
                                                   direction=Transaction.IN).exists())

    def test_an_account_nobody_is_expecting_money_into_is_not_read(self):
        self.assertEqual(self.poll(), (0, 0))

    def test_a_watched_account_is_read_once_per_interval(self):
        deposit_sync.expect_deposit(self.user)
        self.assertEqual(self.poll()[1], 1)
        self.assertEqual(self.poll()[1], 0)      # within the ten seconds
        cache.delete(f"wema:expecting-deposit:read:{self.wallet.pk}")
        self.assertEqual(self.poll()[1], 1)

    def test_the_watch_ends(self):
        deposit_sync.expect_deposit(self.user, minutes=15)
        with patch("wallet.deposit_sync.time.time", return_value=time.time() + 16 * 60):
            self.assertEqual(self.poll()[1], 0)

    def test_nothing_is_read_while_the_bank_is_being_backed_off(self):
        deposit_sync.expect_deposit(self.user)
        cache.set(deposit_sync.BACKOFF_KEY, True, timeout=60)
        self.assertEqual(self.poll()[1], 0)

    def test_a_refused_read_backs_every_reader_off(self):
        deposit_sync.expect_deposit(self.user)
        self.poll({"success": False, "diagnostic": {"http_status": 403}})
        self.assertTrue(cache.get(deposit_sync.BACKOFF_KEY))

    def test_reads_per_poll_are_capped(self):
        others = []
        for n in range(3):
            user, _token = make_user(f"0807123003{n}", f"other{n}@zitch.test")
            wallet = Wallet.objects.get(user=user)
            wallet.account_number = f"045123003{n}"
            wallet.account_reference = wema_account_reference(user)
            wallet.save(update_fields=["account_number", "account_reference"])
            others.append(user)
        for user in [self.user, *others]:
            deposit_sync.expect_deposit(user)
        self.assertEqual(self.poll(max_reads=2)[1], 2)

    def test_a_customer_without_an_account_number_is_not_watched(self):
        user, _token = make_user("08071230099", "noacct@zitch.test")
        deposit_sync.expect_deposit(user)
        self.assertEqual(watched(), {})

    @override_settings(WEMA_EXPECTED_DEPOSIT_MINUTES=0)
    def test_it_can_be_switched_off(self):
        deposit_sync.expect_deposit(self.user)
        self.assertEqual(watched(), {})

    def test_a_cache_outage_never_reaches_the_customer(self):
        with patch.object(cache, "get", side_effect=RuntimeError("redis down")):
            deposit_sync.expect_deposit(self.user)          # must not raise
            self.assertEqual(self.poll(), (0, 0))

    def test_the_app_add_money_screen_watches_the_account(self):
        Client().post("/api/wallet/account/", data=json.dumps({"access_token": self.token}),
                      content_type="application/json")
        self.assertIn(self.wallet.pk, watched())

    def test_the_chat_account_details_watch_the_account(self):
        from whatsapp import router

        funding = {"has_account": True, "available": True, "can_fund": True,
                   "account_setup_state": "ready", "account_number": "0451230020",
                   "bank_name": "Wema Bank", "account_name": "ADA EZE"}
        with patch.object(router, "customer_funding_account", return_value=funding), \
             patch.object(router, "reply"):
            router._send_account_details("2348071230020", self.wallet)
        self.assertIn(self.wallet.pk, watched())

    def test_a_new_account_number_is_watched_for_half_an_hour(self):
        user, _token = make_user("08071230077", "newacct@zitch.test")
        provision_wema_account(user, account_number="0451230077", source="otp")
        wallet = Wallet.objects.get(user=user)
        until = watched()[wallet.pk]
        self.assertGreater(until, time.time() + 29 * 60)
