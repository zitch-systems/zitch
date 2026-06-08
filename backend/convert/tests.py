"""Tests for airtime-to-cash conversion."""
import json
from decimal import Decimal

from django.test import Client, TestCase

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
