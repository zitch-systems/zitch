"""Tests for exam PIN purchase."""
import json
from decimal import Decimal

from django.test import Client, TestCase

from wallet.services import get_or_create_wallet
from wallet.tests import make_user

from .models import ExamProduct


class ExamTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08040000001", "bola@zitch.test", balance="10000")
        ExamProduct.objects.create(code="waec", name="WAEC", description="Result Checker PIN",
                                   price=Decimal("3500"), service_id="waec-registration")

    def post(self, path, payload):
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    def balance(self):
        return get_or_create_wallet(self.user).balance

    def test_list_exams(self):
        res, body = self.post("/api/exams/list/", {})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(body["exams"][0]["code"], "waec")
        self.assertEqual(body["exams"][0]["price"], "3500.00")

    def test_unmapped_exam_is_refused_without_debit(self):
        res, body = self.post("/api/exams/buy/", {
            "access_token": self.token, "exam": "waec", "quantity": 2,
            "phone": "08040000001", "transaction_pin": "1234",
        })
        # The retired VTU rail is gone and Wema has no exam-PIN route. The
        # provider contract refuses the unmapped service and the debit is
        # refunded by run_provider_purchase.
        self.assertEqual(res.status_code, 502)
        self.assertFalse(body.get("success"))
        self.assertEqual(self.balance(), Decimal("10000"))

    def test_buy_rejects_wrong_pin(self):
        res, _ = self.post("/api/exams/buy/", {
            "access_token": self.token, "exam": "waec", "quantity": 1,
            "phone": "08040000001", "transaction_pin": "0000",
        })
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self.balance(), Decimal("10000"))

    def test_buy_rejects_insufficient(self):
        res, _ = self.post("/api/exams/buy/", {
            "access_token": self.token, "exam": "waec", "quantity": 3,
            "phone": "08040000001", "transaction_pin": "1234",
        })
        self.assertEqual(res.status_code, 402)  # 3*3500 > 10000
        self.assertEqual(self.balance(), Decimal("10000"))

    def test_buy_unknown_exam(self):
        res, _ = self.post("/api/exams/buy/", {
            "access_token": self.token, "exam": "nope", "quantity": 1,
            "phone": "08040000001", "transaction_pin": "1234",
        })
        self.assertEqual(res.status_code, 404)

    def test_refused_exam_retry_does_not_charge_twice(self):
        payload = {
            "access_token": self.token, "exam": "waec", "quantity": 1,
            "phone": "08040000001", "transaction_pin": "1234", "idempotency_key": "exam-key-1",
        }
        res1, _ = self.post("/api/exams/buy/", payload)
        res2, body2 = self.post("/api/exams/buy/", payload)
        self.assertEqual(res1.status_code, 502)
        self.assertEqual(res2.status_code, 409)
        self.assertEqual(body2.get("code"), "duplicate")
        self.assertEqual(self.balance(), Decimal("10000"))
