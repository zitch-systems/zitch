"""Tests for VTU purchases: the debit -> provider -> settle/refund invariant
that protects users from losing money when an aggregator call fails."""
import json
from decimal import Decimal
from unittest.mock import patch

from django.core.management import call_command
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


class VtuReconciliationTests(TestCase):
    """A provider timeout must hold the purchase PENDING — never refund a
    possibly-delivered service. The reconcile job later requeries and settles."""

    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08010000001", "ada@zitch.test", balance="20000")

    def post(self, path, payload):
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    def balance(self):
        return get_or_create_wallet(self.user).balance

    def _buy_airtime_timed_out(self):
        """Buy airtime where the provider call times out (pending). Returns the ref."""
        with patch("utility.views.vtu_purchase",
                   return_value={"success": False, "pending": True, "message": "Aggregator unreachable"}):
            res, body = self.post("/api/utility/buyairtime/", {
                "access_token": self.token, "amount": "1000", "network": "1",
                "phone": "08010000001", "transaction_pin": "1234",
            })
        return res, body

    def test_timeout_holds_pending_without_refunding(self):
        res, body = self._buy_airtime_timed_out()
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body.get("pending"))
        self.assertEqual(self.balance(), Decimal("19000"))  # money held, not refunded
        txn = Transaction.objects.get(reference=body["reference"])
        self.assertEqual(txn.transaction_status, Transaction.PENDING)
        self.assertTrue(txn.meta.get("reconcile"))

    def test_reconcile_marks_delivered_purchase_successful(self):
        _, body = self._buy_airtime_timed_out()
        with patch("utility.management.commands.reconcile_vtu.vtu_requery", return_value={"success": True}):
            call_command("reconcile_vtu", older_than_minutes=0)
        self.assertEqual(Transaction.objects.get(reference=body["reference"]).transaction_status, Transaction.SUCCESS)
        self.assertEqual(self.balance(), Decimal("19000"))  # correctly spent

    def test_reconcile_refunds_definitively_failed_purchase(self):
        _, body = self._buy_airtime_timed_out()
        with patch("utility.management.commands.reconcile_vtu.vtu_requery",
                   return_value={"success": False, "provider_reference": "BX1", "message": "not found"}):
            call_command("reconcile_vtu", older_than_minutes=0)
        self.assertEqual(Transaction.objects.get(reference=body["reference"]).transaction_status, Transaction.FAILED)
        self.assertEqual(self.balance(), Decimal("20000"))  # refunded

    def test_reconcile_leaves_still_unknown_pending(self):
        _, body = self._buy_airtime_timed_out()
        with patch("utility.management.commands.reconcile_vtu.vtu_requery",
                   return_value={"success": False, "pending": True}):
            call_command("reconcile_vtu", older_than_minutes=0)
        self.assertEqual(Transaction.objects.get(reference=body["reference"]).transaction_status, Transaction.PENDING)
        self.assertEqual(self.balance(), Decimal("19000"))

    def test_reconcile_settle_is_idempotent(self):
        _, body = self._buy_airtime_timed_out()
        with patch("utility.management.commands.reconcile_vtu.vtu_requery", return_value={"success": False, "provider_reference": "BX1"}):
            call_command("reconcile_vtu", older_than_minutes=0)
            call_command("reconcile_vtu", older_than_minutes=0)  # second run must not double-refund
        self.assertEqual(self.balance(), Decimal("20000"))

    def test_crash_after_debit_leaves_a_reconcilable_row(self):
        """Worker dies mid-provider-call: the debit has committed but settle never
        runs. The committed PENDING row must still carry meta.reconcile so the
        sweep can recover it — otherwise the debit is orphaned forever (money gone,
        nothing ever settles or refunds it). Regression for the reconcile-flag gap:
        the flag is now written atomically with the debit, before the network call.
        """
        # vtu_purchase raising simulates the worker being killed during the
        # provider HTTP call — after run_provider_purchase's debit() has committed,
        # before settle_or_refund() can run.
        with patch("utility.views.vtu_purchase", side_effect=RuntimeError("worker killed mid-call")):
            with self.assertRaises(RuntimeError):
                self.post("/api/utility/buyairtime/", {
                    "access_token": self.token, "amount": "1000", "network": "1",
                    "phone": "08010000001", "transaction_pin": "1234",
                })

        txn = Transaction.objects.get(user=self.user, direction=Transaction.OUT)
        self.assertEqual(txn.transaction_status, Transaction.PENDING)
        self.assertTrue(txn.meta.get("reconcile"))          # discoverable by the sweep
        self.assertEqual(self.balance(), Decimal("19000"))  # debited, awaiting recovery

        # The sweep now finds the orphan and refunds the definitively-failed purchase.
        with patch("utility.management.commands.reconcile_vtu.vtu_requery",
                   return_value={"success": False, "provider_reference": "BX1", "message": "not found"}):
            call_command("reconcile_vtu", older_than_minutes=0)
        txn.refresh_from_db()
        self.assertEqual(txn.transaction_status, Transaction.FAILED)
        self.assertEqual(self.balance(), Decimal("20000"))  # money returned
