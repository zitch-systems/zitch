"""Tests for airtime-to-cash conversion and the live currency converter."""
import json
from decimal import Decimal
from unittest.mock import patch

import requests
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from wallet.services import get_or_create_wallet
from wallet.tests import make_user

from .models import ConversionRequest


class ConvertTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08030000002", "ada@zitch.test", balance="5000")

    def post(self, path, payload):
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    def balance(self):
        return get_or_create_wallet(self.user).balance

    def test_rates_listed(self):
        res, body = self.post("/api/convert/rates/", {})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(body["rates"]), 4)
        self.assertEqual(body["rates"][0]["network"], "1")

    def test_convert_credits_wallet(self):
        # MTN at 0.80: ₦1000 airtime -> ₦800 cash.
        res, body = self.post("/api/convert/airtime/", {
            "access_token": self.token, "network": "1", "phone": "08030000002",
            "amount": "1000", "transaction_pin": "1234",
        })
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["success"])
        self.assertEqual(body["payout"], "800.00")
        self.assertEqual(self.balance(), Decimal("5800.00"))
        conv = ConversionRequest.objects.get(user=self.user)
        self.assertEqual(conv.status, ConversionRequest.SUCCESS)
        self.assertEqual(conv.payout_amount, Decimal("800.00"))

    def test_convert_rejects_wrong_pin(self):
        res, _ = self.post("/api/convert/airtime/", {
            "access_token": self.token, "network": "1", "phone": "08030000002",
            "amount": "1000", "transaction_pin": "0000",
        })
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self.balance(), Decimal("5000"))

    def test_convert_rejects_below_minimum(self):
        res, _ = self.post("/api/convert/airtime/", {
            "access_token": self.token, "network": "1", "phone": "08030000002",
            "amount": "50", "transaction_pin": "1234",
        })
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.balance(), Decimal("5000"))

    @override_settings(DEBUG=False, TESTING=False)
    def test_convert_blocked_in_prod_while_provider_is_mock(self):
        # In a real deploy the mock airtime-collection seam must NOT mint cash
        # (free-money guard): refuse with 503 and leave the wallet untouched.
        res, _ = self.post("/api/convert/airtime/", {
            "access_token": self.token, "network": "1", "phone": "08030000002",
            "amount": "1000", "transaction_pin": "1234",
        })
        self.assertEqual(res.status_code, 503)
        self.assertEqual(self.balance(), Decimal("5000"))

    def test_convert_rejects_bad_network(self):
        res, _ = self.post("/api/convert/airtime/", {
            "access_token": self.token, "network": "9", "phone": "08030000002",
            "amount": "1000", "transaction_pin": "1234",
        })
        self.assertEqual(res.status_code, 400)

    def test_convert_requires_auth(self):
        res, _ = self.post("/api/convert/airtime/", {
            "network": "1", "phone": "08030000002", "amount": "1000", "transaction_pin": "1234",
        })
        self.assertEqual(res.status_code, 401)

    def test_convert_idempotent(self):
        payload = {
            "access_token": self.token, "network": "1", "phone": "08030000002",
            "amount": "1000", "transaction_pin": "1234", "idempotency_key": "conv-key-1",
        }
        res1, body1 = self.post("/api/convert/airtime/", payload)
        res2, body2 = self.post("/api/convert/airtime/", payload)
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(res2.status_code, 200)
        self.assertTrue(body2.get("duplicate"))
        # Credited exactly once despite the retry.
        self.assertEqual(self.balance(), Decimal("5800.00"))
        self.assertEqual(ConversionRequest.objects.filter(user=self.user).count(), 1)


class _FakeResp:
    """Minimal stand-in for a requests.Response (just .json())."""

    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


# A successful upstream payload (NGN base) from the FX provider.
FX_OK = {
    "result": "success",
    "time_last_update_utc": "Mon, 08 Jun 2026 00:02:31 +0000",
    "rates": {"NGN": 1, "USD": 0.000735, "GBP": 0.000549, "EUR": 0.000635},
}


class FxRatesTests(TestCase):
    """The live NGN -> currency converter endpoint (external provider mocked)."""

    def setUp(self):
        self.client = Client()
        cache.clear()  # rates are cached process-wide; isolate each test

    def post(self, path, payload=None):
        res = self.client.post(path, data=json.dumps(payload or {}), content_type="application/json")
        return res, res.json()

    @patch("convert.views.requests.get")
    def test_fx_rates_success(self, mock_get):
        mock_get.return_value = _FakeResp(FX_OK)
        res, body = self.post("/api/convert/fx/")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["success"])
        self.assertEqual(body["base"], "NGN")
        self.assertEqual([c["code"] for c in body["currencies"]], ["USD", "GBP", "EUR"])
        usd = body["currencies"][0]
        self.assertEqual(usd["symbol"], "$")
        self.assertEqual(usd["rate"], 0.000735)

    @patch("convert.views.requests.get")
    def test_fx_rates_cached(self, mock_get):
        mock_get.return_value = _FakeResp(FX_OK)
        self.post("/api/convert/fx/")
        self.post("/api/convert/fx/")
        # Second call is served from cache — the provider is hit only once.
        self.assertEqual(mock_get.call_count, 1)

    @patch("convert.views.requests.get", side_effect=requests.RequestException("boom"))
    def test_fx_rates_provider_unreachable(self, mock_get):
        res, body = self.post("/api/convert/fx/")
        self.assertEqual(res.status_code, 502)
        self.assertIn("message", body)

    @patch("convert.views.requests.get")
    def test_fx_rates_provider_error_result(self, mock_get):
        mock_get.return_value = _FakeResp({"result": "error"})
        res, _ = self.post("/api/convert/fx/")
        self.assertEqual(res.status_code, 502)
