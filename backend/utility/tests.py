"""Tests for VTU purchases: the debit -> provider -> settle/refund invariant
that protects users from losing money when an aggregator call fails."""
import json
from decimal import Decimal
from unittest.mock import patch

from django.test import Client, TestCase

from wallet.models import Transaction
from wallet.services import get_or_create_wallet
from wallet.tests import make_user

from .models import DataPlan


class UtilityTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08010000001", "ada@zitch.test", balance="20000")

    def post(self, path, payload):
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    def balance(self):
        return get_or_create_wallet(self.user).balance

    # --- airtime ---
    def test_airtime_success_debits_once(self):
        res, body = self.post("/api/utility/buyairtime/", {
            "access_token": self.token, "amount": "1000", "network": "1",
            "phone": "08010000001", "transaction_pin": "1234",
        })
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.balance(), Decimal("19000"))
        self.assertEqual(Transaction.objects.get(reference=body["reference"]).transaction_status, Transaction.SUCCESS)

    def test_airtime_refunds_when_provider_fails(self):
        """If the aggregator declines, the debit must be reversed — the user
        keeps their money and the ledger row is marked Failed."""
        with patch("utility.views.vtu_purchase", return_value={"success": False, "message": "declined"}):
            res, _ = self.post("/api/utility/buyairtime/", {
                "access_token": self.token, "amount": "1000", "network": "1",
                "phone": "08010000001", "transaction_pin": "1234",
            })
        self.assertEqual(res.status_code, 502)
        self.assertEqual(self.balance(), Decimal("20000"))  # fully refunded
        self.assertTrue(Transaction.objects.filter(user=self.user, transaction_status=Transaction.FAILED).exists())

    def test_airtime_rejects_wrong_pin_without_debit(self):
        res, _ = self.post("/api/utility/buyairtime/", {
            "access_token": self.token, "amount": "1000", "network": "1",
            "phone": "08010000001", "transaction_pin": "0000",
        })
        # Wrong PIN -> rejected before any wallet movement.
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self.balance(), Decimal("20000"))

    # --- data ---
    def test_data_plan_listing_and_purchase(self):
        DataPlan.objects.create(network="1", plan_type="1", name="1.5GB", validity="30 days",
                                plan_code="mtn-1500", price=Decimal("1200"))
        _, plans = self.post("/api/utility/get_data_plans/", {"datanetwork": "1", "selectedPlanType": "1"})
        self.assertEqual(plans["data_plans"][0]["plan_code"], "mtn-1500")

        res, body = self.post("/api/utility/buydata/", {
            "access_token": self.token, "datanetwork": "1", "selectedDataPlan": "mtn-1500",
            "phone": "08010000001", "transaction_pin": "1234",
        })
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.balance(), Decimal("18800"))  # 20000 - 1200

    # --- electricity ---
    def test_electricity_enforces_minimum(self):
        res, _ = self.post("/api/utility/buyelectricity/", {
            "access_token": self.token, "amount": "100", "disco": "1",
            "meter": "1234567890", "transaction_pin": "1234",
        })
        self.assertEqual(res.status_code, 400)

    # --- customer-name lookups require auth (they return PII + hit a paid
    #     provider, like the bank/Zitch resolve endpoints) ---
    def test_validate_meter_requires_auth(self):
        res, _ = self.post("/api/utility/validate_meter/", {"disco": "1", "meter": "1234567890"})
        self.assertEqual(res.status_code, 401)
        res, body = self.post("/api/utility/validate_meter/",
                              {"access_token": self.token, "disco": "1", "meter": "1234567890"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["customer_name"])

    def test_validate_iuc_requires_auth(self):
        res, _ = self.post("/api/utility/validate_iuc/", {"cablenetwork": "2", "iuc": "1234567890"})
        self.assertEqual(res.status_code, 401)
        res, body = self.post("/api/utility/validate_iuc/",
                             {"access_token": self.token, "cablenetwork": "2", "iuc": "1234567890"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["customer_name"])
