"""Tests for the loan lifecycle: status/quote, request (disburse), repay, and
the available-credit clamp."""
import json
from decimal import Decimal
from unittest.mock import patch

from django.test import Client, TestCase

from common.http import spend_key
from wallet.models import Transaction
from wallet.services import get_or_create_wallet
from wallet.tests import make_user

from .models import Loan
from .services import LoanError, disburse, repay as repay_loan


class LoanTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08010000001", "ada@zitch.test", balance="20000")
        self._key_seq = 0

    def post(self, path, payload):
        payload = dict(payload)
        if path in {"/api/loans/request/", "/api/loans/repay/"} \
                and "idempotency_key" not in payload:
            self._key_seq += 1
            payload["idempotency_key"] = f"loan-test-{self._key_seq}"
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    def balance(self):
        return get_or_create_wallet(self.user).balance

    def test_bnpl_offers_returns_eligibility(self):
        offers = [{"productId": 1, "productName": "BNPL 30d", "maximumTenor": 30}]
        with patch("loans.views.provider_bnpl_offers", return_value={"success": True, "offers": offers}):
            res, body = self.post("/api/loans/bnpl/offers/", {"access_token": self.token})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(body["offers"][0]["productName"], "BNPL 30d")

    def test_bnpl_offers_surfaces_unavailable(self):
        with patch("loans.views.provider_bnpl_offers",
                   return_value={"success": False, "message": "BNPL is not configured"}):
            res, _ = self.post("/api/loans/bnpl/offers/", {"access_token": self.token})
        self.assertEqual(res.status_code, 502)

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

    def test_request_requires_a_client_idempotency_key(self):
        response, body = self.post("/api/loans/request/", {
            "access_token": self.token, "amount": "100000", "tenure_days": 30,
            "transaction_pin": "1234", "idempotency_key": None,
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(body["code"], "idempotency_key_required")
        self.assertFalse(Loan.objects.exists())

    def test_request_idempotent_across_repay_does_not_double_disburse(self):
        # The one-active-loan guard blocks a fast retry, but once the loan is
        # repaid a replayed request (same idempotency_key) must NOT disburse again.
        body = {"access_token": self.token, "amount": "100000", "tenure_days": 30,
                "transaction_pin": "1234", "idempotency_key": "loan-key-1"}
        r1, b1 = self.post("/api/loans/request/", body)
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(b1["reference"], b1["loan"]["reference"])
        # Fully repay so the active-loan guard no longer blocks a retry.
        self.post("/api/loans/repay/", {"access_token": self.token, "amount": "300000", "transaction_pin": "1234"})
        self.assertFalse(Loan.objects.filter(user=self.user, status=Loan.ACTIVE).exists())
        bal_after_repay = self.balance()
        # Replay the ORIGINAL request: deduped, no second principal credited.
        r2, b2 = self.post("/api/loans/request/", body)
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(b2.get("duplicate"))
        self.assertEqual(b2["reference"], b1["reference"])
        self.assertEqual(Loan.objects.filter(user=self.user).count(), 1)
        self.assertEqual(self.balance(), bal_after_repay)  # no extra +100k

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

    def test_full_repayment_retry_replays_after_the_loan_is_closed(self):
        self.post("/api/loans/request/", {
            "access_token": self.token, "amount": "100000", "tenure_days": 30,
            "transaction_pin": "1234", "idempotency_key": "loan-open-repay-test",
        })
        payload = {
            "access_token": self.token, "amount": "200000", "transaction_pin": "1234",
            "idempotency_key": "loan-repay-key-1",
        }
        first, first_body = self.post("/api/loans/repay/", payload)
        after = self.balance()
        retry, body = self.post("/api/loans/repay/", payload)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(retry.status_code, 200)
        self.assertTrue(body["duplicate"])
        self.assertEqual(body["reference"], first_body["reference"])
        self.assertEqual(self.balance(), after)

    def test_stale_repayment_race_claims_key_without_a_second_debit(self):
        self.post("/api/loans/request/", {
            "access_token": self.token, "amount": "100000", "tenure_days": 30,
            "transaction_pin": "1234", "idempotency_key": "stale-loan-open",
        })
        selected = Loan.objects.get(user=self.user, status=Loan.ACTIVE)
        selected_reference = selected.reference
        payload = {
            "access_token": self.token,
            "amount": "1000",
            "transaction_pin": "1234",
            "idempotency_key": "stale-loan-repay",
        }

        def concurrent_winner_then_stale(user, stale, amount, idempotency_key):
            winner = Loan.objects.get(pk=stale.pk)
            winner_key = spend_key(
                "winning-loan-repay", user, "loan-repay", winner.outstanding,
            )
            repay_loan(
                user, winner, winner.outstanding, idempotency_key=winner_key,
            )
            return repay_loan(
                user, stale, amount, idempotency_key=idempotency_key,
            )

        with patch("loans.views.repay", side_effect=concurrent_winner_then_stale):
            response, body = self.post("/api/loans/repay/", payload)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(body["code"], "loan_repayment_stale")
        marker = Transaction.objects.get(
            user=self.user, idempotency_key="stale-loan-repay",
        )
        self.assertEqual(body["reference"], marker.reference)
        self.assertEqual(marker.transaction_status, Transaction.FAILED)
        self.assertEqual(marker.direction, Transaction.OUT)
        self.assertEqual(marker.amount, Decimal("1000.00"))
        self.assertEqual(marker.meta["loan"], selected_reference)
        self.assertEqual(marker.meta["loan_repayment_outcome"], "stale_loan")
        self.assertEqual(marker.meta["balance_movement"], "0.00")
        self.assertTrue(marker.meta["internal_evidence"])
        self.assertTrue(marker.meta["idempotency_fingerprint"])
        self.assertEqual(self.balance(), Decimal("15500.00"))

        # A retry receives the same stable failure and evidence reference.
        replay, replay_body = self.post("/api/loans/repay/", payload)
        self.assertEqual(replay.status_code, 409)
        self.assertEqual(replay_body["code"], "loan_repayment_stale")
        self.assertTrue(replay_body["duplicate"])
        self.assertEqual(replay_body["reference"], marker.reference)
        self.assertEqual(self.balance(), Decimal("15500.00"))

        # Even after a new loan becomes active, the old key remains bound to the
        # loan which lost the race and cannot debit the replacement loan.
        self.post("/api/loans/request/", {
            "access_token": self.token, "amount": "10000", "tenure_days": 15,
            "transaction_pin": "1234", "idempotency_key": "replacement-loan-open",
        })
        replacement = Loan.objects.get(user=self.user, status=Loan.ACTIVE)
        before_balance = self.balance()
        before_repaid = replacement.amount_repaid
        conflict, conflict_body = self.post("/api/loans/repay/", payload)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict_body["code"], "idempotency_conflict")
        replacement.refresh_from_db()
        self.assertEqual(replacement.amount_repaid, before_repaid)
        self.assertEqual(self.balance(), before_balance)

    def test_old_repayment_key_cannot_replay_against_a_new_active_loan(self):
        self.post("/api/loans/request/", {
            "access_token": self.token, "amount": "100000", "tenure_days": 30,
            "transaction_pin": "1234", "idempotency_key": "loan-a-open",
        })
        old_repayment = {
            "access_token": self.token, "amount": "200000",
            "transaction_pin": "1234", "idempotency_key": "loan-a-repay",
        }
        self.post("/api/loans/repay/", old_repayment)
        self.post("/api/loans/request/", {
            "access_token": self.token, "amount": "10000", "tenure_days": 15,
            "transaction_pin": "1234", "idempotency_key": "loan-b-open",
        })
        active = Loan.objects.get(user=self.user, status=Loan.ACTIVE)
        before_balance = self.balance()
        before_repaid = active.amount_repaid

        response, body = self.post("/api/loans/repay/", old_repayment)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(body["code"], "idempotency_conflict")
        active.refresh_from_db()
        self.assertEqual(active.amount_repaid, before_repaid)
        self.assertEqual(self.balance(), before_balance)

    def test_available_credit_never_negative(self):
        """A loan at the full limit leaves outstanding > limit (interest);
        available credit must clamp to 0, not go negative."""
        self.post("/api/loans/request/", {"access_token": self.token, "amount": "500000", "tenure_days": 60, "transaction_pin": "1234"})
        _, body = self.post("/api/loans/status/", {"access_token": self.token})
        self.assertEqual(Decimal(body["available"]), Decimal("0.00"))

    def test_disburse_service_blocks_second_active_loan(self):
        """Race backstop: the service re-checks eligibility inside the row lock,
        so a second disburse (even one that bypassed the view's checks) is
        rejected and never credits the wallet twice."""
        disburse(self.user, Decimal("100000"), 30)
        with self.assertRaises(LoanError):
            disburse(self.user, Decimal("50000"), 30)
        self.assertEqual(Loan.objects.filter(user=self.user, status=Loan.ACTIVE).count(), 1)
        self.assertEqual(self.balance(), Decimal("120000"))  # credited once: 20000 + 100000
