"""Tests for the wallet core: balance, history, Paystack funding (idempotent),
Zitch-to-Zitch transfer, and the tier / face-verification send limits.

All run in MOCK provider mode (no keys), so funding settles automatically.
"""
import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from accounts.models import AccessToken
from utility.providers import to_kobo

from .models import FundingIntent, Transaction
from .services import credit, get_or_create_wallet

User = get_user_model()


def make_user(phone, email, pin="1234", balance="0", tier=1):
    u = User.objects.create(username=phone, phone=phone, email=email,
                            first_name="Ada", last_name="Eze", tier=tier)
    u.set_transaction_pin(pin)
    u.save()
    get_or_create_wallet(u)
    if Decimal(balance) > 0:
        credit(u, Decimal(balance), "Seed")
    return u, AccessToken.issue(u).key


class WalletTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08010000001", "ada@zitch.test", balance="20000")

    def post(self, path, payload):
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    def balance(self, user):
        return get_or_create_wallet(user).balance

    def test_balance_endpoint(self):
        res, body = self.post("/api/wallet_balance/", {"access_token": self.token})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["success"])
        self.assertEqual(Decimal(body["wallet"]), Decimal("20000"))
        self.assertEqual(body["user_first_name"], "Ada")

    def test_balance_requires_valid_token(self):
        res, _ = self.post("/api/wallet_balance/", {"access_token": "nope"})
        self.assertEqual(res.status_code, 401)

    # --- funding (idempotency is the whole point) ---
    def test_fund_verify_credits_once(self):
        _, init = self.post("/api/fund/initialize/", {"access_token": self.token, "amount": "5000"})
        ref = init["reference"]
        self.assertTrue(FundingIntent.objects.get(reference=ref).status == FundingIntent.PENDING)

        self.post("/api/fund/verify/", {"access_token": self.token, "reference": ref})
        self.assertEqual(self.balance(self.user), Decimal("25000"))
        # A duplicate verify (app retry) must not double-credit.
        self.post("/api/fund/verify/", {"access_token": self.token, "reference": ref})
        self.assertEqual(self.balance(self.user), Decimal("25000"))
        self.assertTrue(FundingIntent.objects.get(reference=ref).credited)

    def test_fund_webhook_credits_once_and_dedupes_with_verify(self):
        _, init = self.post("/api/fund/initialize/", {"access_token": self.token, "amount": "7500"})
        ref = init["reference"]
        event = {"event": "charge.success", "data": {"reference": ref, "amount": 750000}}
        # Webhook credits (mock signature accepted).
        r1 = self.client.post("/api/fund/webhook/", data=json.dumps(event), content_type="application/json")
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(self.balance(self.user), Decimal("27500"))
        # Webhook AND the app's verify racing: still only one credit.
        self.client.post("/api/fund/webhook/", data=json.dumps(event), content_type="application/json")
        self.post("/api/fund/verify/", {"access_token": self.token, "reference": ref})
        self.assertEqual(self.balance(self.user), Decimal("27500"))

    def test_fund_rejects_below_minimum(self):
        res, _ = self.post("/api/fund/initialize/", {"access_token": self.token, "amount": "50"})
        self.assertEqual(res.status_code, 400)

    # --- transfer ---
    def test_transfer_moves_funds_atomically(self):
        bob, _ = make_user("08020000002", "bob@zitch.test")
        res, body = self.post("/api/transfer/send/", {
            "access_token": self.token, "identifier": "08020000002",
            "amount": "5000", "transaction_pin": "1234",
        })
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["success"])
        self.assertEqual(self.balance(self.user), Decimal("15000"))
        self.assertEqual(self.balance(bob), Decimal("5000"))

    def test_transfer_rejects_wrong_pin(self):
        make_user("08020000002", "bob@zitch.test")
        res, _ = self.post("/api/transfer/send/", {
            "access_token": self.token, "identifier": "08020000002",
            "amount": "5000", "transaction_pin": "0000",
        })
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self.balance(self.user), Decimal("20000"))

    def test_transfer_rejects_insufficient_funds(self):
        make_user("08020000002", "bob@zitch.test")
        # 30,000 is within the tier-1 limit (50k) but above the 20k balance,
        # so this exercises the insufficient-funds path, not the limit guard.
        res, _ = self.post("/api/transfer/send/", {
            "access_token": self.token, "identifier": "08020000002",
            "amount": "30000", "transaction_pin": "1234",
        })
        self.assertEqual(res.status_code, 402)
        self.assertEqual(self.balance(self.user), Decimal("20000"))

    def test_cannot_transfer_to_self(self):
        res, _ = self.post("/api/transfer/send/", {
            "access_token": self.token, "identifier": "08010000001",
            "amount": "100", "transaction_pin": "1234",
        })
        self.assertEqual(res.status_code, 400)

    # --- tier / face limits (check_send_limits) ---
    def test_transfer_blocked_over_tier_limit(self):
        rich, token = make_user("08030000003", "rich@zitch.test", balance="500000", tier=1)
        make_user("08040000004", "x@zitch.test")
        # Tier 1 limit is 50,000.
        res = self.client.post("/api/transfer/send/", data=json.dumps({
            "access_token": token, "identifier": "08040000004",
            "amount": "60000", "transaction_pin": "1234",
        }), content_type="application/json")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["code"], "limit_exceeded")

    def test_large_transfer_requires_server_side_face_verification(self):
        _, token = make_user("08030000003", "rich@zitch.test", balance="500000", tier=3)
        make_user("08040000004", "x@zitch.test")
        body = {"access_token": token, "identifier": "08040000004",
                "amount": "150000", "transaction_pin": "1234"}
        # >= 100,000 needs face verification.
        res, b = self.post("/api/transfer/send/", body)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(b["code"], "face_required")
        # A client-asserted face_confirmed must NOT bypass the gate.
        res, _ = self.post("/api/transfer/send/", {**body, "face_confirmed": True})
        self.assertEqual(res.status_code, 403)
        # Durable, server-side face verification (mock-accepted) clears it.
        self.post("/api/kyc/face/", {"access_token": token})
        res, b = self.post("/api/transfer/send/", body)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(b["success"])


class KoboTests(TestCase):
    def test_to_kobo_is_exact(self):
        self.assertEqual(to_kobo(Decimal("1234.56")), 123456)  # float path gave 123455
        self.assertEqual(to_kobo("1000"), 100000)
        self.assertEqual(to_kobo(500), 50000)
        self.assertEqual(to_kobo(Decimal("201.90")), 20190)
