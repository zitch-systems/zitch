"""Tests for the Mono open-banking provider (utility.mono) and the banklink app.

Two layers, neither needing real Mono credentials:
- MOCK mode (no key): provider functions return success stubs; endpoints work
  end-to-end offline.
- Simulated LIVE mode: utility.mono.requests is patched so functions build the
  real request and parse Mono's {status, data} envelope.
Webhook crediting reuses the wallet's idempotent settle_funding path.
"""
import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import Client, SimpleTestCase, TestCase, override_settings

from utility import mono
from transfers.models import Bank
from wallet.models import FundingIntent, Wallet
from wallet.tests import make_user

from .models import LinkedBankAccount

MONO_LIVE = {"BASE_URL": "https://api.withmono.com", "SECRET_KEY": "test_sk",
             "PUBLIC_KEY": "", "WEBHOOK_SECRET": "whsec"}


def _resp(body):
    m = MagicMock()
    m.json.return_value = body
    return m


class MonoMockTests(SimpleTestCase):
    def test_mock_mode_active(self):
        self.assertFalse(mono.mono_live())

    def test_exchange_and_account_mock(self):
        r = mono.exchange_token("code-123")
        self.assertTrue(r["success"])
        self.assertTrue(r["account_id"].startswith("mock_acct_"))
        self.assertTrue(mono.get_account(r["account_id"])["success"])

    def test_directpay_mock(self):
        r = mono.initiate_directpay(5000, "ZMONO-1", email="a@b.com")
        self.assertTrue(r["success"])
        self.assertTrue(r["authorization_url"].startswith("mock://mono/directpay/"))

    def test_webhook_accepts_in_dev_without_secret(self):
        with override_settings(MONO={**MONO_LIVE, "SECRET_KEY": "", "WEBHOOK_SECRET": ""}):
            self.assertTrue(mono.verify_webhook({"event": "x"}, ""))


@override_settings(MONO=MONO_LIVE)
class MonoLiveTests(SimpleTestCase):
    def test_mono_live_true(self):
        self.assertTrue(mono.mono_live())

    @patch("utility.mono.requests.post")
    def test_exchange_live(self, mock_post):
        mock_post.return_value = _resp({"status": "successful", "data": {"id": "acc_99"}})
        r = mono.exchange_token("auth-code")
        self.assertTrue(r["success"])
        self.assertEqual(r["account_id"], "acc_99")
        self.assertTrue(mock_post.call_args[0][0].endswith("/v2/accounts/auth"))
        self.assertEqual(mock_post.call_args[1]["headers"]["mono-sec-key"], "test_sk")

    @patch("utility.mono.requests.get")
    def test_balance_live_converts_kobo(self, mock_get):
        mock_get.return_value = _resp({"status": "successful", "data": {"balance": 8420010}})
        r = mono.get_balance("acc_99")
        self.assertEqual(r["balance_naira"], Decimal("84200.10"))

    @patch("utility.mono.requests.post")
    def test_directpay_live_sends_kobo(self, mock_post):
        mock_post.return_value = _resp({"status": "successful",
                                        "data": {"mono_url": "https://pay.mono/x", "reference": "ZMONO-1"}})
        r = mono.initiate_directpay(5000, "ZMONO-1", email="a@b.com")
        self.assertTrue(r["success"])
        self.assertEqual(r["authorization_url"], "https://pay.mono/x")
        self.assertEqual(mock_post.call_args[1]["json"]["amount"], 500000)  # kobo

    def test_webhook_signature(self):
        self.assertTrue(mono.verify_webhook({"event": "x"}, "whsec"))
        self.assertFalse(mono.verify_webhook({"event": "x"}, "wrong"))
        self.assertFalse(mono.verify_webhook({"event": "x"}, ""))


class BanklinkEndpointTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08044400001", "bank@zitch.app")

    def _post(self, path, body):
        return self.client.post(path, data=json.dumps({**body, "access_token": self.token}),
                                content_type="application/json")

    def test_connect_list_refresh_unlink(self):
        r = self._post("/api/banklink/connect/", {"code": "mono-code"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["account"]["bank_name"])
        # list
        items = self._post("/api/banklink/list/", {}).json()["accounts"]
        self.assertEqual(len(items), 1)
        lid = items[0]["id"]
        self.assertTrue(items[0]["account_number"].startswith("****"))
        # refresh
        self.assertEqual(self._post("/api/banklink/refresh/", {"linked_id": lid}).status_code, 200)
        # unlink -> drops from list
        self._post("/api/banklink/unlink/", {"linked_id": lid})
        self.assertEqual(len(self._post("/api/banklink/list/", {}).json()["accounts"]), 0)

    def test_fund_creates_intent_and_returns_url(self):
        lid = self._post("/api/banklink/connect/", {"code": "c"}).json()["account"]["id"]
        r = self._post("/api/banklink/fund/", {"linked_id": lid, "amount": "5000"})
        self.assertEqual(r.status_code, 200)
        ref = r.json()["reference"]
        intent = FundingIntent.objects.get(reference=ref)
        self.assertEqual(intent.meta["provider"], "mono")
        self.assertEqual(intent.amount, Decimal("5000"))

    def test_webhook_payment_success_credits_wallet_once(self):
        lid = self._post("/api/banklink/connect/", {"code": "c"}).json()["account"]["id"]
        ref = self._post("/api/banklink/fund/", {"linked_id": lid, "amount": "5000"}).json()["reference"]
        event = {"event": "mono.events.payment_received", "data": {"reference": ref}}
        r = self.client.post("/api/banklink/webhook/", data=json.dumps(event),
                             content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("5000"))
        # redelivered webhook does not double-credit
        self.client.post("/api/banklink/webhook/", data=json.dumps(event),
                         content_type="application/json")
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("5000"))

    def test_webhook_credits_settled_amount_not_requested_amount(self):
        # Mono reports a SMALLER settled amount (kobo) than the user requested —
        # credit what actually moved, not the requested intent amount.
        lid = self._post("/api/banklink/connect/", {"code": "c"}).json()["account"]["id"]
        ref = self._post("/api/banklink/fund/", {"linked_id": lid, "amount": "5000"}).json()["reference"]
        event = {"event": "mono.events.payment_received", "data": {"reference": ref, "amount": 300000}}
        self.client.post("/api/banklink/webhook/", data=json.dumps(event), content_type="application/json")
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("3000"))

    def test_webhook_never_credits_more_than_requested(self):
        # An over-reported (or forged) settled amount can't credit above the intent.
        lid = self._post("/api/banklink/connect/", {"code": "c"}).json()["account"]["id"]
        ref = self._post("/api/banklink/fund/", {"linked_id": lid, "amount": "5000"}).json()["reference"]
        event = {"event": "mono.events.payment_received", "data": {"reference": ref, "amount": 900000}}
        self.client.post("/api/banklink/webhook/", data=json.dumps(event), content_type="application/json")
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("5000"))


class BanklinkPayoutTests(TestCase):
    """Money OUT: wallet debit -> linked bank, PIN-verified, via the transfers rail."""

    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08044400002", "payout@zitch.app", balance="50000")
        # One active bank so the linked account number can be routed (mock detect).
        Bank.objects.create(code="gtb", name="GTBank", bank_code="058", color="#E32119")

    def _post(self, path, body):
        return self.client.post(path, data=json.dumps({**body, "access_token": self.token}),
                                content_type="application/json")

    def _link(self):
        return self._post("/api/banklink/connect/", {"code": "c"}).json()["account"]["id"]

    def test_payout_debits_wallet(self):
        lid = self._link()
        r = self._post("/api/banklink/payout/", {"linked_id": lid, "amount": "10000", "pin": "1234"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body.get("success") or body.get("pending"))
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("40000"))

    def test_payout_wrong_pin_does_not_debit(self):
        lid = self._link()
        r = self._post("/api/banklink/payout/", {"linked_id": lid, "amount": "10000", "pin": "9999"})
        self.assertIn(r.json().get("code"), ("pin_incorrect", "pin_locked"))
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("50000"))

    def test_payout_below_minimum_rejected(self):
        lid = self._link()
        r = self._post("/api/banklink/payout/", {"linked_id": lid, "amount": "50", "pin": "1234"})
        self.assertFalse(r.json().get("success"))
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("50000"))

    def test_payout_idempotent_on_retry(self):
        lid = self._link()
        body = {"linked_id": lid, "amount": "10000", "pin": "1234", "idempotency_key": "payout-key-1"}
        self._post("/api/banklink/payout/", body)
        self._post("/api/banklink/payout/", body)  # replay — must not debit twice
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("40000"))

    def test_payout_rejected_when_account_maps_to_multiple_banks(self):
        # An ambiguous NUBAN (valid at two banks, possibly different holders) must
        # not be routed by guessing matches[0] — reject and keep the wallet whole.
        lid = self._link()
        two = [{"bank": "gtb", "bank_name": "GTBank", "name": "ADA EZE"},
               {"bank": "access", "bank_name": "Access Bank", "name": "JOHN DOE"}]
        with patch("banklink.views.detect_account_banks", return_value=two):
            r = self._post("/api/banklink/payout/", {"linked_id": lid, "amount": "10000", "pin": "1234"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("50000"))
