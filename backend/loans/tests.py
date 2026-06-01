"""Tests for the loan lifecycle: status/quote, request (disburse), repay, and
the available-credit clamp."""
import json
from decimal import Decimal

from django.test import Client, TestCase

from wallet.services import get_or_create_wallet
from wallet.tests import make_user

from .models import Loan


class LoanTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08010000001", "ada@zitch.test", balance="20000")

    def post(self, path, payload):
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    def balance(self):
        return get_or_create_wallet(self.user).balance

    def test_status_with_no_loan(self):
        res, body = self.post("/api/loans/status/", {"access_token": self.token})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(Decimal(body["available"]), Loan.DEFAULT_LIMIT)
        self.assertIsNone(body["active_loan"])

    def test_quote_flat_interest(self):
        res, body = self.post("/api/loans/quote/", {"access_token": self.token, "amount": "100000", "tenure_days": 30})
        self.assertEqual(body["interest"], "4500.00")  # 100000 * 4.5% * (30/30)
        self.assertEqual(body["total_repayment"], "104500.00")

    def test_quote_rejects_bad_tenure(self):
        res, _ = self.post("/api/loans/quote/", {"access_token": self.token, "amount": "100000", "tenure_days": 45})
        self.assertEqual(res.status_code, 400)

    def test_request_disburses_to_wallet(self):
        res, body = self.post("/api/loans/request/", {
            "access_token": self.token, "amount": "100000", "tenure_days": 30, "transaction_pin": "1234",
        })
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["success"])
        self.assertEqual(self.balance(), Decimal("120000"))  # 20k + 100k disbursed
        self.assertEqual(Loan.objects.filter(user=self.user, status=Loan.ACTIVE).count(), 1)

    def test_only_one_active_loan(self):
        self.post("/api/loans/request/", {"access_token": self.token, "amount": "100000", "tenure_days": 30, "transaction_pin": "1234"})
        res, _ = self.post("/api/loans/request/", {"access_token": self.token, "amount": "20000", "tenure_days": 30, "transaction_pin": "1234"})
        self.assertEqual(res.status_code, 409)

    def test_request_rejects_below_minimum(self):
        res, _ = self.post("/api/loans/request/", {"access_token": self.token, "amount": "5000", "tenure_days": 30, "transaction_pin": "1234"})
        self.assertEqual(res.status_code, 400)

    def test_request_rejects_over_limit(self):
        res, _ = self.post("/api/loans/request/", {"access_token": self.token, "amount": "600000", "tenure_days": 30, "transaction_pin": "1234"})
        self.assertEqual(res.status_code, 403)

    def test_request_rejects_wrong_pin(self):
        res, _ = self.post("/api/loans/request/", {"access_token": self.token, "amount": "100000", "tenure_days": 30, "transaction_pin": "0000"})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(Loan.objects.count(), 0)

    def test_full_repayment_marks_repaid(self):
        self.post("/api/loans/request/", {"access_token": self.token, "amount": "100000", "tenure_days": 30, "transaction_pin": "1234"})
        # Overpay; repayment is capped at the outstanding 104,500.
        res, body = self.post("/api/loans/repay/", {"access_token": self.token, "amount": "200000", "transaction_pin": "1234"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.balance(), Decimal("15500"))  # 120000 - 104500
        loan = Loan.objects.get(user=self.user)
        self.assertEqual(loan.status, Loan.REPAID)
        self.assertEqual(loan.amount_repaid, Decimal("104500.00"))

    def test_available_credit_never_negative(self):
        """A loan at the full limit leaves outstanding > limit (interest);
        available credit must clamp to 0, not go negative."""
        self.post("/api/loans/request/", {"access_token": self.token, "amount": "500000", "tenure_days": 60, "transaction_pin": "1234"})
        _, body = self.post("/api/loans/status/", {"access_token": self.token})
        self.assertEqual(Decimal(body["available"]), Decimal("0.00"))
