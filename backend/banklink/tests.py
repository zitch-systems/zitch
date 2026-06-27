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
