"""Replay the supplied VAS Postman example shapes through the real money path.

Each response below keeps the example's keys, value types and formats (the
bank's own 19-digit transactionReference, whole-naira amount strings, dotted
data-plan IDs, HTTP 200 duplicate failures). Names, phones and accounts are
synthetic; the source collection stays outside this repository. Not live UAT.
"""
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings

from wallet.models import Transaction
from . import bills, test_vas
from .client import Client, ProviderError, Reply, vas_amount
from .models import Instruction
from .payments import requery
from .test_client import CONFIG

PLANS = [{"productId": "MTN-200MB-2", "dataBundle": "200MB", "amount": "200", "validity": "2days"},
         {"productId": "MTN-2.5GB-27", "dataBundle": "2.5GB", "amount": "100", "validity": "2days"}]


def sample(data, *, message="Airtime topup successful"):
    return Reply({"status": "success", "responseCode": "200", "message": message, "data": data}, 200)


@override_settings(NINEPSB=CONFIG)
class VasWireFormatTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_amounts_use_the_samples_whole_naira_form_without_rounding_kobo(self):
        self.assertEqual(vas_amount("100"), "100")
        self.assertEqual(vas_amount("1000.00"), "1000")
        self.assertEqual(vas_amount(Decimal("200")), "200")
        self.assertEqual(vas_amount("100.50"), "100.50")
        for value in ("0", "-1", "1.001", "nan"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                vas_amount(value)

    def test_dotted_data_plan_ids_from_the_catalog_reach_the_wire(self):
        with patch.object(Client, "request", return_value=sample({})) as request:
            for product in ("MTN-2.5GB-27", "MTN-1.5GB-4", "MTN-4.5GB-6", "MTN-200MB-2"):
                Client("vas").topup("data", "Z90000000000000001", "500", "1100000001",
                                    "08000000001", "MTN", product_id=product)
                self.assertEqual(request.call_args.args[1]["productId"], product)
        for product in ("", ".5GB", "MTN..1", "MTN/1", "MTN 1"):
            with self.subTest(product=product), self.assertRaises(ValueError):
                Client("vas").topup("data", "Z90000000000000001", "500", "1100000001",
                                    "08000000001", "MTN", product_id=product)

    def test_dotted_ids_remain_invalid_as_url_path_segments(self):
        with self.assertRaises(ValueError):
            Client("vas").billers("1.5")

    def test_sample_millisecond_expiry_is_bounded_to_one_hour(self):
        # The sample says expiresIn 7200000 for a two-hour token; the PDF says seconds.
        with patch.object(Client, "_send", return_value=Reply({"status": "success", "responseCode": "200",
                "message": "Authentication Successful",
                "data": {"accessToken": "synthetic", "expiresIn": 7200000}}, 200)), \
                patch("banking.client.cache.set") as stored:
            self.assertEqual(Client("vas").token(), "synthetic")
        self.assertEqual(stored.call_args.kwargs["timeout"], 3600)

    def test_sample_invalid_credentials_never_issue_a_token(self):
        with patch.object(Client, "_send", return_value=Reply({"status": "failed", "responseCode": "401",
                "message": "Bad credentials"}, 401)), self.assertRaises(ProviderError) as raised:
            Client("vas").token()
        self.assertEqual((raised.exception.http_status, raised.exception.response_code), (401, "401"))


@override_settings(NINEPSB=CONFIG, SECURE_SSL_REDIRECT=False, RATELIMIT_ENABLE=False,
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class VasSampleJourneyTests(TestCase):
    """Documented success/failure bodies drive settlement exactly once."""

    def setUp(self):
        test_vas.VasJourneyTests.setUp(self)

    def assert_balance(self, expected):
        test_vas.VasJourneyTests.assert_balance(self, expected)

    def topup(self, kind, reply, *, amount="100", product="", key="sample"):
        catalog = lambda kind, **kw: {"network": "MTN"} if kind == "network" else PLANS
        with patch("banking.bills.catalog", side_effect=catalog), \
             patch.object(Client, "token", return_value="synthetic"), \
             patch.object(Client, "_send", return_value=reply) as send:
            txn = bills.purchase_topup(self.user, kind, "07041267880", amount,
                                       product_id=product, idempotency_key=key)
        return txn, send.call_args.kwargs["payload"]

    def test_documented_airtime_success_with_bank_reference_settles(self):
        txn, wire = self.topup("airtime", sample({"recipient": "07041267880", "amount": "100",
            "network": "MTN", "transactionReference": "2304031007421680414"}))
        self.assertEqual(txn.transaction_status, Transaction.SUCCESS)
        self.assertEqual(Instruction.objects.get().last_error, "")
        self.assertEqual(wire["amount"], "100")
        self.assertEqual(len(wire["transactionReference"]), 18)
        self.assertNotEqual(wire["transactionReference"], "2304031007421680414")
        self.assert_balance("400")

    def test_documented_data_success_for_a_dotted_plan_settles_at_the_catalog_price(self):
        txn, wire = self.topup("data", sample({"recipient": "07041267880", "network": "MTN",
            "amount": "100", "dataPlan": "MTN-2.5GB-27", "transactionReference": "2304031029193386661"},
            message="Data topup Successful"), product="MTN-2.5GB-27")
        self.assertEqual(txn.transaction_status, Transaction.SUCCESS)
        self.assertEqual((wire["productId"], wire["amount"]), ("MTN-2.5GB-27", "100"))
        self.assert_balance("400")

    def test_echoed_recipient_or_amount_contradiction_still_holds_the_payment(self):
        for index, data in enumerate(({"recipient": "08011112222", "amount": "100", "network": "MTN"},
                                      {"recipient": "07041267880", "amount": "101", "network": "MTN"})):
            with self.subTest(data=data):
                txn, _ = self.topup("airtime", sample({**data, "transactionReference": "2304031007421680414"}),
                                    key="contradiction-" + str(index))
                self.assertEqual(txn.transaction_status, Transaction.PENDING)
        self.assert_balance("300")

    def test_documented_duplicate_reference_is_pending_and_settles_from_status(self):
        duplicate = Reply({"status": "failed", "responseCode": "300",
            "message": "Duplicate Slip/Instrument No. 'VT1_Z90000000000000001' detected."}, 200)
        txn, _ = self.topup("airtime", duplicate)
        self.assertEqual(txn.transaction_status, Transaction.PENDING)
        self.assert_balance("400")
        status = sample({"transactionStatus": "success", "description": "Topup transaction successful"})
        with patch.object(Client, "vas_status", return_value=status):
            self.assertTrue(requery(Instruction.objects.get())["success"])
        txn.refresh_from_db()
        self.assertEqual(txn.transaction_status, Transaction.SUCCESS)
        self.assert_balance("400")

    def test_documented_unknown_reference_status_never_refunds(self):
        txn, _ = self.topup("airtime", Reply({"status": "failed", "responseCode": "500",
            "message": "Error"}, 500))
        self.assertEqual(txn.transaction_status, Transaction.PENDING)
        missing = Reply({"status": "failed", "responseCode": "404", "message": "Invalid transaction reference"}, 404)
        with patch.object(Client, "vas_status", return_value=missing):
            self.assertTrue(requery(Instruction.objects.get())["pending"])
        self.assert_balance("400")
