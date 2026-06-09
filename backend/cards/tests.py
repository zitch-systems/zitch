"""Tests for virtual cards: create, details reveal, fund, freeze."""
import json
from decimal import Decimal

from django.test import Client, TestCase

from wallet.services import get_or_create_wallet
from wallet.tests import make_user

from .models import VirtualCard


class CardTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08020000001", "kola@zitch.test", balance="50000")

    def post(self, path, payload):
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    def balance(self):
        return get_or_create_wallet(self.user).balance

    def _create(self):
        return self.post("/api/cards/create/", {"access_token": self.token})

    def test_create_mints_one_card(self):
        res, body = self._create()
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["success"])
        self.assertEqual(len(body["card"]["last4"]), 4)
        self.assertEqual(VirtualCard.objects.filter(user=self.user).count(), 1)

    def test_create_is_idempotent(self):
        self._create()
        self._create()
        self.assertEqual(VirtualCard.objects.filter(user=self.user).count(), 1)

    def test_details_require_correct_pin(self):
        self._create()
        res, _ = self.post("/api/cards/details/", {"access_token": self.token, "transaction_pin": "0000"})
        self.assertEqual(res.status_code, 403)

    def test_details_reveal_pan_and_cvv(self):
        self._create()
        res, body = self.post("/api/cards/details/", {"access_token": self.token, "transaction_pin": "1234"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["pan"])
        self.assertTrue(body["cvv"])

    def test_fund_debits_wallet_and_loads_card(self):
        self._create()
        res, body = self.post("/api/cards/fund/", {"access_token": self.token, "amount": "10000", "transaction_pin": "1234"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(body["card"]["balance"], "10000.00")
        self.assertEqual(self.balance(), Decimal("40000"))

    def test_fund_rejects_insufficient_balance(self):
        self._create()
        # Tier 2 (₦200k limit) so the amount clears the KYC tier/face gate and is
        # below the ₦100k face-verification threshold — it still exceeds the wallet
        # balance (₦50k), so funding is rejected at the balance check (402) with the
        # wallet untouched. (Card funding now also enforces send limits.)
        self.user.tier = 2
        self.user.save(update_fields=["tier"])
        res, _ = self.post("/api/cards/fund/", {"access_token": self.token, "amount": "60000", "transaction_pin": "1234"})
        self.assertEqual(res.status_code, 402)
        self.assertEqual(self.balance(), Decimal("50000"))

    def test_fund_rejects_above_tier_limit(self):
        # New guard: loading more than the KYC tier allows is blocked up front, so a
        # low-tier user can't use card funding to bypass the transfer limits.
        self._create()
        res, _ = self.post("/api/cards/fund/", {"access_token": self.token, "amount": "999999", "transaction_pin": "1234"})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self.balance(), Decimal("50000"))

    def test_freeze_then_fund_blocked(self):
        self._create()
        self.post("/api/cards/freeze/", {"access_token": self.token})
        res, _ = self.post("/api/cards/fund/", {"access_token": self.token, "amount": "5000", "transaction_pin": "1234"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.balance(), Decimal("50000"))

    def test_freeze_toggles_status(self):
        self._create()
        _, b1 = self.post("/api/cards/freeze/", {"access_token": self.token})
        self.assertTrue(b1["card"]["frozen"])
        _, b2 = self.post("/api/cards/freeze/", {"access_token": self.token})
        self.assertFalse(b2["card"]["frozen"])
