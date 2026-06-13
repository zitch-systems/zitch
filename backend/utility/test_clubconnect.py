"""ClubConnect VTU provider: request routing, response parsing, provider
dispatch, and the async pending -> reconcile path.

Live calls can't run in CI, so these pin the translation from the views'
service_id/payload to ClubConnect's GET endpoints + params and the normalisation
of its responses to the settle_or_refund contract. The codes/paths themselves
still need confirming against the ClubConnect dashboard (see clubconnect.py).
"""
import json
from decimal import Decimal
from unittest.mock import patch

from django.core.management import call_command
from django.test import Client, TestCase, override_settings

from wallet.models import Transaction
from wallet.services import get_or_create_wallet
from wallet.tests import make_user

from utility.clubconnect import _build, _parse

CC_CREDS = {"BASE_URL": "https://www.clubkonnect.com", "USER_ID": "u1", "API_KEY": "k1"}


class ClubConnectRoutingTests(TestCase):
    """Lock the service_id/payload -> (endpoint, query params) mapping."""

    def test_airtime(self):
        ep, p = _build("mtn-airtime", {"amount": "50", "phone": "08010000001"}, "REF1")
        self.assertEqual(ep, "APIAirtimeV1.asp")
        self.assertEqual(p["MobileNetwork"], "01")
        self.assertEqual(p["Amount"], 50)            # coerced to whole-naira int
        self.assertEqual(p["MobileNumber"], "08010000001")
        self.assertEqual(p["RequestID"], "REF1")     # our ledger ref = idempotency key

    def test_9mobile_maps_to_03(self):
        _, p = _build("9mobile-airtime", {"amount": "100", "phone": "0809"}, "R")
        self.assertEqual(p["MobileNetwork"], "03")

    def test_data(self):
        ep, p = _build("glo-data", {"billersCode": "0805", "variation_code": "GLO5GB", "phone": "0805"}, "R")
        self.assertEqual(ep, "APIDatabundleV1.asp")
        self.assertEqual(p["MobileNetwork"], "02")
        self.assertEqual(p["DataPlan"], "GLO5GB")
        self.assertEqual(p["MobileNumber"], "0805")

    def test_electricity(self):
        ep, p = _build("port harcourt-electric",
                       {"billersCode": "62100", "variation_code": "prepaid", "amount": "2000"}, "R")
        self.assertEqual(ep, "APIElectricityV1.asp")
        self.assertEqual(p["ElectricCompany"], "portharcourt-electric")
        self.assertEqual(p["MeterType"], "01")       # prepaid
        self.assertEqual(p["MeterNo"], "62100")
        self.assertEqual(p["Amount"], 2000)

    def test_cable(self):
        ep, p = _build("dstv", {"billersCode": "7032000", "variation_code": "COMPE36"}, "R")
        self.assertEqual(ep, "APICableTVV1.asp")
        self.assertEqual(p["CableTV"], "dstv")
        self.assertEqual(p["Package"], "COMPE36")
        self.assertEqual(p["SmartCardNo"], "7032000")

    def test_betting(self):
        ep, p = _build("bet9ja-betting", {"billersCode": "USER123", "amount": "500"}, "R")
        self.assertEqual(ep, "APIBettingV1.asp")
        self.assertEqual(p["BettingCompany"], "bet9ja")
        self.assertEqual(p["CustomerID"], "USER123")
        self.assertEqual(p["Amount"], 500)

    def test_exam_epin(self):
        ep, p = _build("waec-pin", {"phone": "0805", "quantity": 2}, "R")
        self.assertEqual(ep, "APIWAECV1.asp")
        self.assertEqual(p["ExamType"], "waec")
        self.assertEqual(p["Quantity"], 2)

    def test_unknown_service_is_rejected(self):
        ep, _ = _build("crypto", {}, "R")
        self.assertIsNone(ep)


class ClubConnectParseTests(TestCase):
    """Normalise ClubConnect statuses to the settle_or_refund contract."""

    def test_completed_is_success(self):
        r = _parse({"status": "ORDER_COMPLETED", "orderid": "OID1"})
        self.assertTrue(r["success"])
        self.assertEqual(r["provider_reference"], "OID1")
        self.assertNotIn("pending", r)

    def test_received_is_pending(self):
        # Async accept: held PENDING for requery, never refunded.
        r = _parse({"status": "ORDER_RECEIVED", "orderid": "OID2"})
        self.assertFalse(r["success"])
        self.assertTrue(r.get("pending"))

    def test_cancelled_is_definitive_failure(self):
        r = _parse({"status": "ORDER_CANCELLED", "orderid": "OID3"})
        self.assertFalse(r["success"])
        self.assertFalse(r.get("pending"))           # -> caller refunds

    def test_prepaid_meter_token_extracted(self):
        r = _parse({"status": "ORDER_COMPLETED", "metertoken": "1234-5678", "orderid": "x"})
        self.assertEqual(r["token"], "1234-5678")

    def test_exam_pins_extracted(self):
        r = _parse({"status": "ORDER_COMPLETED", "pins": [{"pin": "111", "serial": "S1"}], "orderid": "x"})
        self.assertEqual(r["pins"], [{"pin": "111", "serial": "S1"}])


@override_settings(VTU_PROVIDER="clubconnect", CLUBCONNECT=CC_CREDS)
class ClubConnectDispatchTests(TestCase):
    """With the switch flipped, the shared contract routes to ClubConnect and the
    async pending -> reconcile path settles correctly."""

    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08010000050", "cc@zitch.test", balance="20000")

    def post(self, path, payload):
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    def balance(self):
        return get_or_create_wallet(self.user).balance

    def test_vtu_purchase_dispatches_to_clubconnect(self):
        from utility.providers import vtu_purchase
        with patch("utility.clubconnect._get",
                   return_value={"status": "ORDER_COMPLETED", "orderid": "OID"}) as g:
            r = vtu_purchase("mtn-airtime", {"amount": "50", "phone": "0805"}, "REF")
        self.assertTrue(r["success"])
        self.assertEqual(g.call_args[0][0], "APIAirtimeV1.asp")   # ClubConnect endpoint hit

    def test_async_purchase_holds_pending_then_reconciles(self):
        # ORDER_RECEIVED on send -> money held PENDING + reconcile flag (PR #47),
        # never refunded for a maybe-delivered order.
        with patch("utility.clubconnect._get",
                   return_value={"status": "ORDER_RECEIVED", "orderid": "OID"}):
            res, body = self.post("/api/utility/buyairtime/", {
                "access_token": self.token, "amount": "1000", "network": "1",
                "phone": "08010000050", "transaction_pin": "1234",
            })
        self.assertTrue(body.get("pending"))
        txn = Transaction.objects.get(reference=body["reference"])
        self.assertEqual(txn.transaction_status, Transaction.PENDING)
        self.assertTrue(txn.meta.get("reconcile"))
        self.assertEqual(self.balance(), Decimal("19000"))

        # The reconcile sweep requeries -> ORDER_COMPLETED -> settled Successful.
        with patch("utility.clubconnect._get",
                   return_value={"status": "ORDER_COMPLETED", "orderid": "OID"}):
            call_command("reconcile_vtu", older_than_minutes=0)
        txn.refresh_from_db()
        self.assertEqual(txn.transaction_status, Transaction.SUCCESS)
        self.assertEqual(self.balance(), Decimal("19000"))       # correctly spent, not refunded

    def test_definitive_failure_refunds(self):
        # ORDER_CANCELLED is a hard failure: the view returns 502 and the wallet
        # is refunded (no reference in a fail body, so assert via ledger + balance).
        with patch("utility.clubconnect._get",
                   return_value={"status": "ORDER_CANCELLED", "orderid": "OID"}):
            res, body = self.post("/api/utility/buyairtime/", {
                "access_token": self.token, "amount": "1000", "network": "1",
                "phone": "08010000050", "transaction_pin": "1234",
            })
        self.assertEqual(res.status_code, 502)
        txn = Transaction.objects.get(user=self.user, direction=Transaction.OUT)
        self.assertEqual(txn.transaction_status, Transaction.FAILED)
        self.assertEqual(self.balance(), Decimal("20000"))       # refunded


class VtuProviderDefaultTests(TestCase):
    def test_default_provider_is_baxi(self):
        from utility.providers import _vtu_provider
        self.assertEqual(_vtu_provider(), "baxi")
