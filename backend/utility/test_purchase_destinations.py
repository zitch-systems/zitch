"""Malformed bill destinations must never reach PIN, money or provider calls."""
import json
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from utility.models import DataPlan
from wallet.models import Transaction, Wallet
from wallet.tests import make_user


class PurchaseDestinationTests(TestCase):
    def setUp(self):
        self.user, self.token = make_user("08010000672", "destination@zitch.test", balance="20000")
        DataPlan.objects.create(network="1", plan_type="1", plan_code="mtn-valid", name="1GB", validity="30 days", price="1000")

    def post(self, path, **data):
        return self.client.post("/api/utility/" + path + "/", json.dumps({"access_token": self.token, **data}),
                                content_type="application/json")

    def test_invalid_mobile_network_and_phone_never_spend(self):
        before = Transaction.objects.count()
        for endpoint, network_key in (("buyairtime", "network"), ("buydata", "datanetwork")):
            for invalid in ({network_key: "unknown", "phone": "08010000672"},
                            {network_key: "1", "phone": "0801abc0672"},
                            {network_key: "1", "phone": {"value": "08010000672"}},
                            {network_key: "1", "phone": "0801000067"},
                            {network_key: "1", "phone": "+447911123456"}):
                with self.subTest(endpoint=endpoint, invalid=invalid), \
                     patch("utility.views._check_pin") as pin, patch("utility.views.vtu_purchase") as provider:
                    response = self.post(endpoint, amount="100", selectedDataPlan="mtn-valid", idempotency_key="bad-destination", **invalid)
                self.assertEqual(response.status_code, 400)
                pin.assert_not_called()
                provider.assert_not_called()
        self.assertEqual(Transaction.objects.count(), before)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("20000"))

    def test_international_and_local_phone_retry_share_one_purchase(self):
        with patch("utility.views.vtu_purchase", return_value={"success": True}) as provider:
            first = self.post("buyairtime", network="2", phone="+234 801-000-0672", amount="100",
                              transaction_pin="1234", idempotency_key="same-destination")
            replay = self.post("buyairtime", network="2", phone="08010000672", amount="100",
                               transaction_pin="1234", idempotency_key="same-destination")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.json()["duplicate"])
        self.assertEqual(first.json()["reference"], replay.json()["reference"])
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(provider.call_args.args[0], "glo-airtime")
        self.assertEqual(provider.call_args.args[1]["phone"], "08010000672")
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("19900"))

    def test_unknown_billers_and_invalid_types_do_not_fall_back_to_another_provider(self):
        cases = [
            ("validate_iuc", {"cablenetwork": "bad", "iuc": "1234567890"}),
            ("validate_iuc", {"cablenetwork": "1", "iuc": ["1234567890"]}),
            ("buycable", {"cablenetwork": "bad", "iuc": "1234567890", "selectedcablePlan": "x"}),
            ("validate_meter", {"disco": "bad", "meter": "1234567890"}),
            ("validate_meter", {"disco": "1", "meter": "1234567890", "meter_type": "other"}),
            ("buyelectricity", {"disco": "1", "meter": {"value": "1234567890"}}),
        ]
        before = Transaction.objects.count()
        for endpoint, payload in cases:
            with self.subTest(endpoint=endpoint, payload=payload), \
                 patch("utility.views.vtu_verify_customer") as verify, \
                 patch("utility.views.vtu_purchase") as provider, patch("utility.views._check_pin") as pin:
                response = self.post(endpoint, amount="1000", idempotency_key="bad-biller", **payload)
            self.assertEqual(response.status_code, 400)
            verify.assert_not_called()
            provider.assert_not_called()
            pin.assert_not_called()
        self.assertEqual(Transaction.objects.count(), before)
