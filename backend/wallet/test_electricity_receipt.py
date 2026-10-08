"""Purchased meter value survives app restarts without leaking other receipts."""
import json
from decimal import Decimal

from django.test import TestCase

from accounts.models import AccessToken, User
from wallet.models import Transaction
from wallet.services import settle_or_refund


class ElectricityReceiptTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="receipt-owner", phone="08011112223")
        self.token = AccessToken.issue(self.user).key
        self.txn = Transaction.objects.create(
            user=self.user, service="Electricity — Ikeja", amount=Decimal("2000"),
            direction=Transaction.OUT, transaction_status=Transaction.PENDING,
            reference="MOBILE-METER-RECEIPT", meta={
                "meter": "12345678901", "meter_type": "prepaid",
                "customer_name": "Ada Eze", "customer_address": "1 Test Street",
                "provider_reference": "NOT-A-METER-TOKEN", "raw": {"secret": "private"},
            },
        )

    def detail(self, *, token=None):
        return self.client.post(
            "/api/transaction/status/", json.dumps({"reference": self.txn.reference}),
            content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {token or self.token}",
        )

    def settle(self):
        self.assertEqual(settle_or_refund(self.txn, {
            "success": True, "token": "1234-5678-9012-3456-7890", "units": "25.5",
        }), "success")

    def test_late_settlement_recovers_token_and_meter_for_owner_only(self):
        self.assertNotIn("token", self.detail().json()["transaction"])
        self.settle()
        response = self.detail()
        row = response.json()["transaction"]
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(row["token"], "1234-5678-9012-3456-7890")
        self.assertEqual(row["meter"], "12345678901")
        self.assertEqual(row["meter_type"], "prepaid")
        self.assertEqual(row["customer_name"], "Ada Eze")
        self.assertEqual(row["customer_address"], "1 Test Street")
        self.assertEqual(row["electricity_units"], "25.5")
        self.assertNotIn("raw", row)
        self.assertNotIn("provider_reference", row)

        other = User.objects.create(username="different-owner", phone="08011112224")
        response = self.detail(token=AccessToken.issue(other).key)
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("1234-5678", response.content.decode())

    def test_purchased_token_is_omitted_from_bulk_history(self):
        self.settle()
        response = self.client.post(
            "/api/user-transaction-history/", "{}", content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("token", response.json()["all_site_transactions"][0])

    def test_unsettled_failed_or_reviewed_receipts_never_expose_purchased_value(self):
        for status, review in ((Transaction.PENDING, False), (Transaction.FAILED, False),
                              (Transaction.SUCCESS, True)):
            with self.subTest(status=status, review=review):
                # Separate fixtures keep this compatible with append-only ledger
                # enforcement in the PostgreSQL suite.
                self.txn = Transaction.objects.create(
                    user=self.user, service="Electricity — Ikeja", amount=Decimal("1000"),
                    direction=Transaction.OUT, transaction_status=status,
                    reference=f"METER-HIDDEN-{status}-{review}",
                    meta={"token": "must-not-be-exposed", "meter": "12345678901",
                          "wema_reversal_quarantine": {"active": review}},
                )
                row = self.detail().json()["transaction"]
                self.assertNotIn("token", row)
                self.assertNotIn("meter", row)

    def test_provider_reference_is_not_mislabelled_as_meter_token(self):
        settle_or_refund(self.txn, {"success": True})
        self.assertEqual(self.detail().json()["transaction"]["token"], "")

    def test_postpaid_and_non_electricity_rows_do_not_publish_tokens(self):
        for service, meter_type in (("Electricity — Ikeja", "postpaid"), ("Airtime — MTN", "prepaid")):
            with self.subTest(service=service):
                self.txn = Transaction.objects.create(
                    user=self.user, service=service, amount=Decimal("1000"),
                    direction=Transaction.OUT, transaction_status=Transaction.SUCCESS,
                    reference=f"NON-TOKEN-{meter_type}",
                    meta={"token": "not-a-recharge-token", "meter_type": meter_type},
                )
                self.assertNotIn("token", self.detail().json()["transaction"])

