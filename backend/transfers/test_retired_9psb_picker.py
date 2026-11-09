"""The retired 9PSB partner must not appear as a new Zitch payout destination.

Existing beneficiary, transaction and immutable financial history are preserved.
These checks deliberately force an old row back to active to prove the API still
fails closed if an admin or data restore accidentally reactivates it.
"""
import json
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from transfers.models import Bank
from wallet.services import get_or_create_wallet
from wallet.tests import make_user


class Retired9PSBRecipientTests(TestCase):
    def setUp(self):
        self.user, self.token = make_user(
            "08011112345", "retired-bank-test@example.test", balance="1500")
        self.retired = Bank.objects.create(
            code="9psb", name="9PSB", bank_code="120001", active=True)
        Bank.objects.create(
            code="wema", name="Wema Bank", bank_code="035", active=True)

    def post(self, path, payload):
        return self.client.post(
            path, data=json.dumps(payload), content_type="application/json")

    def test_retired_partner_not_offered_even_with_active_old_row(self):
        response = self.post("/api/transfers/banks/", {})
        self.assertEqual(response.status_code, 200)
        codes = [b["code"] for b in response.json()["banks"]]
        self.assertNotIn("9psb", codes)
        self.assertIn("wema", codes)

    def test_retired_partner_rejects_new_resolve_and_send_before_bank_call(self):
        payload = {
            "access_token": self.token, "account_number": "0123456789",
            "bank": "9psb", "name": "TEST CUSTOMER", "amount": "100",
            "transaction_pin": "1234", "idempotency_key": "retired-provider-test",
        }
        with patch("transfers.views.payout_resolve_account") as resolve:
            for endpoint in ("/api/transfers/resolve/", "/api/transfers/send/"):
                with self.subTest(endpoint=endpoint):
                    response = self.post(endpoint, payload)
                    self.assertEqual(response.status_code, 404)
        resolve.assert_not_called()
        self.assertEqual(get_or_create_wallet(self.user).balance, Decimal("1500"))

    def test_reseed_marks_retired_bank_inactive_but_keeps_history_record(self):
        call_command("seed_plans", stdout=StringIO())
        self.retired.refresh_from_db()
        self.assertFalse(self.retired.active)
        self.assertTrue(Bank.objects.filter(pk=self.retired.pk).exists())
