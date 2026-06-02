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
from .providers import _baxi_build_request


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


class BaxiRoutingTests(TestCase):
    """Pin the Baxi per-service request routing (endpoint paths + field mapping).

    Live calls can't run in CI, so these lock the translation from the views'
    service_id/payload to Baxi's per-service endpoint and body. The service_type
    codes themselves still need confirming against the Baxi dashboard.
    """
    def test_airtime_routes_to_airtime_endpoint(self):
        ep, body = _baxi_build_request("mtn-airtime", {"amount": "50", "phone": "08010000001"})
        self.assertEqual(ep, "services/airtime/request")
        self.assertEqual(body["service_type"], "mtn")
        self.assertEqual(body["amount"], 50)  # coerced to whole-naira int
        self.assertEqual(body["phone"], "08010000001")
        self.assertIn("agentReference", body)

    def test_9mobile_maps_to_etisalat(self):
        _, body = _baxi_build_request("9mobile-airtime", {"amount": "100", "phone": "0809"})
        self.assertEqual(body["service_type"], "etisalat")

    def test_data_routes_to_databundle(self):
        ep, body = _baxi_build_request("glo-data", {"billersCode": "0805", "variation_code": "GLO5GB", "phone": "0805"})
        self.assertEqual(ep, "services/databundle/request")
        self.assertEqual(body["service_type"], "glo-data")
        self.assertEqual(body["datacode"], "GLO5GB")

    def test_electricity_routes_with_disco_code(self):
        ep, body = _baxi_build_request("port harcourt-electric",
                                       {"billersCode": "62100", "variation_code": "prepaid", "amount": "2000"})
        self.assertEqual(ep, "services/electricity/request")
        self.assertEqual(body["service_type"], "portharcourt_electric")
        self.assertEqual(body["account_number"], "62100")
        self.assertEqual(body["MeterType"], "prepaid")
        self.assertEqual(body["amount"], 2000)

    def test_cable_routes_to_multichoice(self):
        ep, body = _baxi_build_request("dstv", {"billersCode": "7032000", "variation_code": "COMPE36"})
        self.assertEqual(ep, "services/multichoice/request")
        self.assertEqual(body["service_type"], "dstv")
        self.assertEqual(body["product_code"], "COMPE36")

    def test_unknown_service_is_rejected(self):
        ep, _ = _baxi_build_request("crypto", {})
        self.assertIsNone(ep)

    def test_agent_reference_is_unique_per_call(self):
        _, b1 = _baxi_build_request("mtn-airtime", {"amount": "50", "phone": "0805"})
        _, b2 = _baxi_build_request("mtn-airtime", {"amount": "50", "phone": "0805"})
        self.assertNotEqual(b1["agentReference"], b2["agentReference"])
