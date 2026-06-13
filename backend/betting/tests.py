"""Tests for betting wallet funding."""
import json
from decimal import Decimal

from django.test import Client, TestCase

from wallet.services import get_or_create_wallet
from wallet.tests import make_user

from .models import BettingPlatform


class BettingTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08030000001", "uche@zitch.test", balance="10000")
        BettingPlatform.objects.create(code="bet9ja", name="Bet9ja", color="#0B7A3B", service_id="bet9ja")

    def post(self, path, payload):
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    def balance(self):
        return get_or_create_wallet(self.user).balance

    def test_platforms_listed(self):
        res, body = self.post("/api/betting/list/", {})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(body["platforms"][0]["code"], "bet9ja")

    def test_fund_debits_wallet(self):
        res, body = self.post("/api/betting/fund/", {
            "access_token": self.token, "platform": "bet9ja", "user_id": "ZB12345",
            "amount": "2000", "transaction_pin": "1234",
        })
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["success"])
        self.assertEqual(self.balance(), Decimal("8000"))

    def test_fund_rejects_wrong_pin(self):
        res, _ = self.post("/api/betting/fund/", {
            "access_token": self.token, "platform": "bet9ja", "user_id": "ZB12345",
            "amount": "2000", "transaction_pin": "0000",
        })
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self.balance(), Decimal("10000"))

    def test_fund_rejects_insufficient(self):
        res, _ = self.post("/api/betting/fund/", {
            "access_token": self.token, "platform": "bet9ja", "user_id": "ZB12345",
            "amount": "999999", "transaction_pin": "1234",
        })
        self.assertEqual(res.status_code, 402)
        self.assertEqual(self.balance(), Decimal("10000"))

    def test_fund_rejects_short_user_id(self):
        res, _ = self.post("/api/betting/fund/", {
            "access_token": self.token, "platform": "bet9ja", "user_id": "ab",
            "amount": "2000", "transaction_pin": "1234",
        })
        self.assertEqual(res.status_code, 400)

    def test_fund_unknown_platform(self):
        res, _ = self.post("/api/betting/fund/", {
            "access_token": self.token, "platform": "nope", "user_id": "ZB12345",
            "amount": "2000", "transaction_pin": "1234",
        })
        self.assertEqual(res.status_code, 404)

    def test_fund_idempotent(self):
        payload = {
            "access_token": self.token, "platform": "bet9ja", "user_id": "ZB12345",
            "amount": "2000", "transaction_pin": "1234", "idempotency_key": "bet-key-1",
        }
        res1, _ = self.post("/api/betting/fund/", payload)
        res2, body2 = self.post("/api/betting/fund/", payload)
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(res2.status_code, 200)
        self.assertTrue(body2.get("duplicate"))
        # Debited exactly once despite the retry.
        self.assertEqual(self.balance(), Decimal("8000"))
