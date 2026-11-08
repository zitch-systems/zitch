"""A catalogue price change cannot silently change the customer's approved debit."""
import json
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from exams.models import ExamProduct
from utility.models import CablePlan, DataPlan
from wallet.models import Transaction
from wallet.services import get_or_create_wallet
from wallet.tests import make_user


class ConfirmedPurchasePriceTests(TestCase):
    def setUp(self):
        self.user, self.token = make_user("08012223334", "confirmed-price@zitch.test", balance="50000")
        wallet = get_or_create_wallet(self.user)
        wallet.account_number = "0100000001"
        wallet.save(update_fields=["account_number"])
        self.data = DataPlan.objects.create(
            network="1", plan_type="1", name="1GB", validity="30 days",
            plan_code="confirmed-data", wema_code="D001", price=Decimal("1000"),
        )
        self.cable = CablePlan.objects.create(
            provider="2", name="Test bouquet", cable_plan_code="confirmed-cable",
            wema_code="C001", price=Decimal("4000"),
        )
        self.exam = ExamProduct.objects.create(code="confirmed-exam", name="Exam PIN", price=Decimal("1500"))
        self.routes = [
            ("/api/utility/buydata/", self.data, {"datanetwork": "1", "selectedDataPlan": self.data.plan_code,
                                              "phone": self.user.phone}, Decimal("1000")),
            ("/api/utility/buycable/", self.cable, {"cablenetwork": "2", "selectedcablePlan": self.cable.cable_plan_code,
                                                "iuc": "12345678901"}, Decimal("4000")),
            ("/api/exams/buy/", self.exam, {"exam": self.exam.code, "phone": self.user.phone,
                                          "quantity": 2}, Decimal("3000")),
        ]

    def post(self, path, body):
        return self.client.post(path, json.dumps({"transaction_pin": "1234", **body}),
                                content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {self.token}")

    def test_changed_prices_reject_before_provider_work_or_wallet_debit(self):
        with patch("utility.views.vtu_purchase") as utility_purchase, patch("exams.views.vtu_purchase") as exam_purchase, \
                patch("utility.views.vtu_verify_customer") as verify:
            for index, (path, _product, body, amount) in enumerate(self.routes):
                with self.subTest(path=path):
                    response = self.post(path, {**body, "expected_amount": str(amount - 100),
                                                "idempotency_key": f"changed-price-{index}"})
                    self.assertEqual(response.status_code, 409, response.content)
                    self.assertEqual(response.json()["code"], "price_changed")
                    self.assertEqual(Decimal(response.json()["current_price"]), amount)
            utility_purchase.assert_not_called()
            exam_purchase.assert_not_called()
            verify.assert_not_called()
        self.assertEqual(get_or_create_wallet(self.user).balance, Decimal("50000"))
        self.assertFalse(Transaction.objects.filter(user=self.user, direction=Transaction.OUT).exists())

    def test_matching_totals_settle_once_and_later_price_changes_do_not_hide_replays(self):
        total = Decimal("0")
        provider_result = {"success": True, "pins": ["PIN-123"], "status": "SUCCESS"}
        with patch("utility.views.vtu_purchase", return_value=provider_result) as utility_purchase, \
                patch("exams.views.vtu_purchase", return_value=provider_result) as exam_purchase, \
                patch("utility.views.vtu_verify_customer", return_value={"success": True, "customer_name": "Ada Eze"}):
            for index, (path, product, body, amount) in enumerate(self.routes):
                with self.subTest(path=path):
                    payload = {**body, "expected_amount": str(amount), "idempotency_key": f"approved-price-{index}"}
                    first = self.post(path, payload)
                    self.assertEqual(first.status_code, 200, first.content)
                    self.assertTrue(first.json().get("success"), first.content)
                    product.price += 500
                    product.save(update_fields=["price"])
                    replay = self.post(path, {**payload, "transaction_pin": "0000"})
                    self.assertEqual(replay.status_code, 200, replay.content)
                    self.assertTrue(replay.json().get("duplicate"), replay.content)
                    self.assertEqual(replay.json()["reference"], first.json()["reference"])
                    self.assertEqual(Transaction.objects.get(reference=first.json()["reference"]).amount, amount)
                    total += amount
            self.assertEqual(utility_purchase.call_count, 2)
            self.assertEqual(exam_purchase.call_count, 1)
        self.assertEqual(get_or_create_wallet(self.user).balance, Decimal("50000") - total)

    def test_invalid_expected_totals_cannot_start_a_debit(self):
        path, _product, body, _amount = self.routes[0]
        for index, value in enumerate((None, "", "NaN", "Infinity", True, {}, 0, -10)):
            with self.subTest(value=value):
                response = self.post(path, {**body, "expected_amount": value,
                                            "idempotency_key": f"invalid-price-{index}"})
                self.assertEqual(response.status_code, 400, response.content)
                self.assertEqual(response.json()["code"], "invalid_expected_amount")
        self.assertEqual(get_or_create_wallet(self.user).balance, Decimal("50000"))
        self.assertFalse(Transaction.objects.filter(user=self.user, direction=Transaction.OUT).exists())
