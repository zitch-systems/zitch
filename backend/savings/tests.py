"""Tests for the Fixed Save flow: rates, quote, lock (create), list, maturity.

These also pin the JSON contract the Expo app depends on (the `My Fixed Saves`
screen reads `savings/list`, and `fixedsave` reads `savings/rates`), so a future
field rename here fails loudly instead of silently breaking the app.
"""
import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from accounts.models import AccessToken
from wallet.services import credit, get_or_create_wallet

from .models import FixedSave
from .services import run_maturities

User = get_user_model()


class SavingsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create(
            username="saver", phone="08010000001", email="saver@zitch.test",
            first_name="Sade", last_name="Okoro",
        )
        self.user.set_transaction_pin("1234")
        self.user.save()
        self.token = AccessToken.issue(self.user).key
        # Fund the wallet so locks succeed.
        get_or_create_wallet(self.user)
        credit(self.user, Decimal("250000"), "Test top-up")

    def post(self, path, payload):
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    # --- rates / quote (no auth) ---
    def test_rates_lists_every_tier(self):
        res, body = self.post("/api/savings/rates/", {})
        self.assertEqual(res.status_code, 200)
        days = {r["days"] for r in body["rates"]}
        self.assertEqual(days, set(FixedSave.RATES))
        self.assertEqual(body["min"], str(FixedSave.MIN_PRINCIPAL))

    def test_quote_matches_simple_interest_formula(self):
        res, body = self.post("/api/savings/quote/", {"access_token": self.token, "amount": "100000", "days": 365})
        self.assertEqual(res.status_code, 200)
        # 100000 * 0.22 * (365/365) = 22000.00
        self.assertEqual(body["interest"], "22000.00")
        self.assertEqual(body["maturity_value"], "122000.00")

    def test_quote_rejects_unknown_period(self):
        res, body = self.post("/api/savings/quote/", {"access_token": self.token, "amount": "1000", "days": 45})
        self.assertEqual(res.status_code, 400)

    # --- create (lock) ---
    def test_create_locks_funds_and_debits_wallet(self):
        res, body = self.post(
            "/api/savings/create/",
            {"access_token": self.token, "amount": "50000", "days": 90, "transaction_pin": "1234"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["success"])
        self.assertEqual(get_or_create_wallet(self.user).balance, Decimal("200000"))
        plan = FixedSave.objects.get(user=self.user)
        self.assertEqual(plan.principal, Decimal("50000"))
        self.assertEqual(plan.status, FixedSave.ACTIVE)
        # Returned plan dict carries every field the app renders.
        for key in ("reference", "principal", "interest", "rate", "duration_days",
                    "maturity_value", "status", "matures_at"):
            self.assertIn(key, body["plan"])

    def test_create_rejects_wrong_pin(self):
        res, body = self.post(
            "/api/savings/create/",
            {"access_token": self.token, "amount": "50000", "days": 90, "transaction_pin": "0000"},
        )
        self.assertEqual(res.status_code, 403)
        self.assertEqual(FixedSave.objects.count(), 0)

    def test_create_rejects_below_minimum(self):
        res, body = self.post(
            "/api/savings/create/",
            {"access_token": self.token, "amount": "500", "days": 90, "transaction_pin": "1234"},
        )
        self.assertEqual(res.status_code, 400)

    def test_create_rejects_when_insufficient(self):
        res, body = self.post(
            "/api/savings/create/",
            {"access_token": self.token, "amount": "500000", "days": 90, "transaction_pin": "1234"},
        )
        self.assertEqual(res.status_code, 402)

    # --- list ---
    def test_list_returns_total_locked_and_plan_contract(self):
        self.post("/api/savings/create/", {"access_token": self.token, "amount": "30000", "days": 30, "transaction_pin": "1234"})
        self.post("/api/savings/create/", {"access_token": self.token, "amount": "20000", "days": 180, "transaction_pin": "1234"})
        res, body = self.post("/api/savings/list/", {"access_token": self.token})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(Decimal(body["total_locked"]), Decimal("50000"))
        self.assertEqual(len(body["plans"]), 2)
        self.assertEqual(set(body["plans"][0]), {
            "reference", "principal", "interest", "rate", "duration_days",
            "maturity_value", "status", "matures_at",
        })

    def test_list_requires_auth(self):
        res, _ = self.post("/api/savings/list/", {"access_token": "bogus"})
        self.assertEqual(res.status_code, 401)

    # --- maturity payout ---
    def test_maturity_pays_principal_plus_interest_once(self):
        self.post("/api/savings/create/", {"access_token": self.token, "amount": "100000", "days": 365, "transaction_pin": "1234"})
        plan = FixedSave.objects.get(user=self.user)
        # Fast-forward maturity into the past.
        plan.matures_at = timezone.now() - timezone.timedelta(days=1)
        plan.save(update_fields=["matures_at"])

        before = get_or_create_wallet(self.user).balance  # 150000 (250k - 100k locked)
        self.assertEqual(run_maturities(), 1)
        after = get_or_create_wallet(self.user).balance
        self.assertEqual(after - before, Decimal("122000.00"))  # principal + 22% interest

        plan.refresh_from_db()
        self.assertEqual(plan.status, FixedSave.MATURED)
        self.assertTrue(plan.paid_out)

        # Re-running never double-pays.
        self.assertEqual(run_maturities(), 0)
        self.assertEqual(get_or_create_wallet(self.user).balance, after)
