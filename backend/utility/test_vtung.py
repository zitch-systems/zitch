"""VTU.ng (v2) provider: request routing, response parsing, JWT token handling,
provider dispatch, and the async pending -> reconcile path.

Live calls can't run in CI, so these pin the translation from the views'
service_id/payload to VTU.ng's v2 JSON bodies and the normalisation of its
responses to the settle_or_refund contract.
"""
import json
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, TestCase, override_settings

from wallet.models import Transaction
from wallet.services import get_or_create_wallet
from wallet.tests import make_user

from utility.vtung import _build, _parse

# API_KEY set => _live() true and _token() returns it without a login round-trip.
VT_CREDS = {"BASE_URL": "https://vtu.ng", "API_KEY": "tok123", "USERNAME": "", "PASSWORD": ""}


class VtuNgRoutingTests(TestCase):
    """Lock the service_id/payload -> (endpoint, JSON body) mapping."""

    def test_airtime(self):
        ep, b = _build("mtn-airtime", {"amount": "50", "phone": "08010000001"}, "REF1")
        self.assertEqual(ep, "wp-json/api/v2/airtime")
        self.assertEqual(b["service_id"], "mtn")
        self.assertEqual(b["amount"], 50)
        self.assertEqual(b["phone"], "08010000001")
        self.assertEqual(b["request_id"], "REF1")   # our ledger ref = idempotency key

    def test_9mobile_maps_to_etisalat(self):
        _, b = _build("9mobile-airtime", {"amount": "100", "phone": "0809"}, "R")
        self.assertEqual(b["service_id"], "etisalat")

    def test_data(self):
        ep, b = _build("glo-data", {"billersCode": "0805", "variation_code": "glo-1gb", "phone": "0805"}, "R")
        self.assertEqual(ep, "wp-json/api/v2/data")
        self.assertEqual(b["service_id"], "glo")
        self.assertEqual(b["variation_id"], "glo-1gb")
        self.assertEqual(b["phone"], "0805")

    def test_cable(self):
        ep, b = _build("dstv", {"billersCode": "7032", "variation_code": "dstv-padi"}, "R")
        self.assertEqual(ep, "wp-json/api/v2/tv")
        self.assertEqual(b["service_id"], "dstv")
        self.assertEqual(b["customer_id"], "7032")
        self.assertEqual(b["variation_id"], "dstv-padi")

    def test_electricity(self):
        ep, b = _build("port harcourt-electric",
                       {"billersCode": "62100", "variation_code": "prepaid", "amount": "2000"}, "R")
        self.assertEqual(ep, "wp-json/api/v2/electricity")
        self.assertEqual(b["service_id"], "portharcourt-electric")
        self.assertEqual(b["customer_id"], "62100")
        self.assertEqual(b["variation_id"], "prepaid")
        self.assertEqual(b["amount"], 2000)

    def test_betting(self):
        ep, b = _build("bet9ja-betting", {"billersCode": "USER1", "amount": "500"}, "R")
        self.assertEqual(ep, "wp-json/api/v2/betting")
        self.assertEqual(b["service_id"], "bet9ja")
        self.assertEqual(b["customer_id"], "USER1")
        self.assertEqual(b["amount"], 500)

    def test_exam_is_unsupported(self):
        # VTU.ng has no exam e-PIN -> unsupported (resolves to failure -> refund).
        ep, _ = _build("waec-pin", {"phone": "0805", "quantity": 2}, "R")
        self.assertIsNone(ep)


class VtuNgParseTests(TestCase):
    """Normalise VTU.ng statuses to the settle_or_refund contract."""

    def test_completed_is_success(self):
        r = _parse({"code": "success", "data": {"order_id": "OID", "status": "completed-api"}})
        self.assertTrue(r["success"])
        self.assertEqual(r["provider_reference"], "OID")

    def test_processing_is_pending(self):
        r = _parse({"code": "success", "data": {"order_id": "OID", "status": "processing-api"}})
        self.assertFalse(r["success"])
        self.assertTrue(r.get("pending"))

    def test_failed_is_definitive_failure(self):
        r = _parse({"code": "success", "data": {"order_id": "OID", "status": "failed"}})
        self.assertFalse(r["success"])
        self.assertFalse(r.get("pending"))            # -> refund

    def test_refunded_is_definitive_failure(self):
        r = _parse({"code": "success", "data": {"status": "refunded"}})
        self.assertFalse(r["success"])
        self.assertFalse(r.get("pending"))

    def test_prepaid_meter_token_extracted(self):
        r = _parse({"code": "success", "data": {"status": "completed-api", "meter_token": "1234-5678"}})
        self.assertEqual(r["token"], "1234-5678")

    def test_rejection_code_is_failure(self):
        # Rejected before execution (e.g. insufficient wallet) -> refund, not pending.
        r = _parse({"code": "error", "message": "insufficient balance", "data": {}})
        self.assertFalse(r["success"])
        self.assertFalse(r.get("pending"))


@override_settings(VTU_PROVIDER="vtung", VTUNG=VT_CREDS)
class VtuNgDispatchTests(TestCase):
    """With VTU.ng selected, the shared contract routes to it and the async
    pending -> reconcile path settles correctly."""

    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08010000060", "vt@zitch.test", balance="20000")

    def post(self, path, payload):
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    def balance(self):
        return get_or_create_wallet(self.user).balance

    def test_vtu_purchase_dispatches_to_vtung(self):
        from utility.providers import vtu_purchase
        with patch("utility.vtung._request",
                   return_value={"code": "success", "data": {"order_id": "OID", "status": "completed-api"}}) as r:
            res = vtu_purchase("mtn-airtime", {"amount": "50", "phone": "0805"}, "REF")
        self.assertTrue(res["success"])
        self.assertEqual(r.call_args[0][1], "wp-json/api/v2/airtime")   # VTU.ng endpoint hit

    def test_async_purchase_holds_pending_then_reconciles(self):
        # processing-api on send -> held PENDING + reconcile flag (PR #47).
        with patch("utility.vtung._request",
                   return_value={"code": "success", "data": {"order_id": "OID", "status": "processing-api"}}):
            res, body = self.post("/api/utility/buyairtime/", {
                "access_token": self.token, "amount": "1000", "network": "1",
                "phone": "08010000060", "transaction_pin": "1234",
            })
        self.assertTrue(body.get("pending"))
        txn = Transaction.objects.get(reference=body["reference"])
        self.assertEqual(txn.transaction_status, Transaction.PENDING)
        self.assertTrue(txn.meta.get("reconcile"))
        self.assertEqual(self.balance(), Decimal("19000"))

        # The reconcile sweep requeries -> completed-api -> settled Successful.
        with patch("utility.vtung._request",
                   return_value={"code": "success", "data": {"order_id": "OID", "status": "completed-api"}}):
            call_command("reconcile_vtu", older_than_minutes=0)
        txn.refresh_from_db()
        self.assertEqual(txn.transaction_status, Transaction.SUCCESS)
        self.assertEqual(self.balance(), Decimal("19000"))           # correctly spent

    def test_failed_status_refunds(self):
        with patch("utility.vtung._request",
                   return_value={"code": "success", "data": {"order_id": "OID", "status": "failed"}}):
            res, body = self.post("/api/utility/buyairtime/", {
                "access_token": self.token, "amount": "1000", "network": "1",
                "phone": "08010000060", "transaction_pin": "1234",
            })
        self.assertEqual(res.status_code, 502)
        txn = Transaction.objects.get(user=self.user, direction=Transaction.OUT)
        self.assertEqual(txn.transaction_status, Transaction.FAILED)
        self.assertEqual(self.balance(), Decimal("20000"))           # refunded


@override_settings(DEBUG=False, TESTING=False, VTU_PROVIDER="vtung",
                   VTUNG={"BASE_URL": "https://vtu.ng", "API_KEY": "", "USERNAME": "", "PASSWORD": ""})
class VtuProdMockGuardTests(TestCase):
    """In production a provider with no credentials must FAIL CLOSED, never
    fake-success — otherwise a misconfigured deploy would charge customers for
    undelivered airtime/data. (Dev/tests still get the mock.)"""

    def test_purchase_fails_closed_not_mock_success(self):
        from utility.providers import vtu_purchase
        r = vtu_purchase("mtn-airtime", {"amount": "100", "phone": "0805"}, "REF")
        self.assertFalse(r["success"])
        self.assertNotIn("mock", r)               # not a faked success

    def test_requery_holds_pending_not_mock_delivered(self):
        from utility.providers import vtu_requery
        r = vtu_requery("REF")
        self.assertFalse(r["success"])
        self.assertTrue(r.get("pending"))         # leaves the row for a real check


@override_settings(VTUNG={"BASE_URL": "https://vtu.ng", "API_KEY": "", "USERNAME": "u", "PASSWORD": "p"})
class VtuNgTokenTests(TestCase):
    """JWT acquisition + caching."""

    def setUp(self):
        cache.delete("vtung_jwt_token")

    def test_login_fetches_and_caches_jwt(self):
        from utility.vtung import _token
        with patch("utility.vtung.requests.post") as p:
            p.return_value.json.return_value = {"token": "JWT123"}
            self.assertEqual(_token(), "JWT123")
        self.assertEqual(cache.get("vtung_jwt_token"), "JWT123")     # cached for reuse

    def test_static_api_key_skips_login(self):
        from utility.vtung import _token
        with override_settings(VTUNG={"BASE_URL": "https://vtu.ng", "API_KEY": "STATIC",
                                       "USERNAME": "", "PASSWORD": ""}):
            with patch("utility.vtung.requests.post") as p:
                self.assertEqual(_token(), "STATIC")
                p.assert_not_called()
