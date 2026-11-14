"""What the bank's gateway answered is recorded when it cannot be read.

On 14 November 2026 the gateway stopped answering in JSON. Every statement read
failed, and the log said only ``error_type=JSONDecodeError``: not the status,
not whether the body was a block page or empty. An airtime purchase made in that
window was held pending with no log line at all, so nothing showed whether the
bank had been asked.
"""
from unittest import mock

import requests
from django.test import SimpleTestCase, override_settings

from utility import wema

WEMA_LIVE = {
    "BASE_URL": "https://gw.example",
    "CHANNEL_ID": "chan",
    "KEYS": {"wallet": "wkey", "airtime": "akey", "bills": "bkey"},
    "SOURCE_ACCOUNT": "0100000001",
}


def _http(status_code, body: bytes, content_type="text/html"):
    resp = requests.Response()
    resp.status_code = status_code
    resp._content = body
    resp.headers["Content-Type"] = content_type
    return resp


class NonJsonAnswerIsRecordedTests(SimpleTestCase):
    def test_a_block_page_is_logged_with_its_status_type_and_size(self):
        page = b"<html><body>403 Forbidden for account 0458386712</body></html>"
        with self.assertLogs("zitch", level="WARNING") as logs:
            out = wema._note_non_json(
                "acct_mgt", "/api/AccountMaintenance/CustomerAccount/transhistoryV2",
                _http(403, page))
        self.assertEqual(out.status_code, 403)
        line = "\n".join(logs.output)
        self.assertIn("wema_gateway_non_json", line)
        self.assertIn("product=acct_mgt", line)
        self.assertIn("route=/api/AccountMaintenance/CustomerAccount/transhistoryV2", line)
        self.assertIn("http_status=403", line)
        self.assertIn("content_type=text/html", line)
        self.assertIn(f"bytes={len(page)}", line)
        # The body is described, never quoted.
        self.assertNotIn("0458386712", line)
        self.assertNotIn("Forbidden", line)

    def test_an_empty_reply_is_logged_too(self):
        with self.assertLogs("zitch", level="WARNING") as logs:
            wema._note_non_json("airtime", "/api/Airtime/Client/PurchaseAirtime",
                                _http(200, b"", ""))
        self.assertIn("http_status=200", logs.output[0])
        self.assertIn("bytes=0", logs.output[0])

    def test_a_json_answer_is_not_logged(self):
        with self.assertNoLogs("zitch", level="WARNING"):
            wema._note_non_json("debit", "/api/Shared/GetAllBanks",
                                _http(200, b'{"result": []}', "application/json"))

    def test_a_response_without_bytes_is_passed_through_unread(self):
        resp = mock.Mock()
        self.assertIs(wema._note_non_json("debit", "/api/Shared/GetAllBanks", resp), resp)
        resp.json.assert_not_called()

    def test_every_gateway_call_goes_through_the_check(self):
        session = mock.Mock()
        session.post.return_value = _http(403, b"<html>blocked</html>")
        session.get.return_value = _http(200, b"")
        with override_settings(WEMA=WEMA_LIVE), \
             mock.patch.object(wema, "_gateway", return_value=session), \
             self.assertLogs("zitch", level="WARNING") as logs:
            wema._post("airtime", "/api/Airtime/Client/PurchaseAirtime", {})
            wema._get("debit", "/api/Shared/GetAllBanks")
        self.assertEqual(sum("wema_gateway_non_json" in line for line in logs.output), 2)


class GatewayRouteTests(SimpleTestCase):
    def test_identifiers_at_the_end_of_a_path_are_dropped(self):
        for path, route in (
            ("/api/Shared/AccountNameEnquiry/044/0061230613", "/api/Shared/AccountNameEnquiry"),
            ("/api/IntraBankTransfer/ConfirmClientTransferStatus/ZTCH09872F384D24",
             "/api/IntraBankTransfer/ConfirmClientTransferStatus"),
            ("/api/RemitaPayment/ValidateRrr/290019681818/1", "/api/RemitaPayment/ValidateRrr"),
            ("/api/Partner/partnerCard/virtual-card-details/tokenwithoutdigits",
             "/api/Partner/partnerCard/virtual-card-details"),
            ("/api/AccountMaintenance/CustomerAccount/transhistoryV2",
             "/api/AccountMaintenance/CustomerAccount/transhistoryV2"),
        ):
            self.assertEqual(wema._gateway_route(path), route)


class UnreachableLogCarriesTheStatusTests(SimpleTestCase):
    def test_a_refused_status_is_in_the_log_line(self):
        error = requests.HTTPError("bank gateway returned HTTP 503", response=_http(503, b""))
        with self.assertLogs("zitch", level="WARNING") as logs:
            out = wema._unreachable(error)
        self.assertIn("error_type=HTTPError http_status=503", logs.output[0])
        self.assertEqual(out["diagnostic"]["http_status"], 503)

    def test_an_answer_that_never_arrived_has_no_status(self):
        with self.assertLogs("zitch", level="WARNING") as logs:
            wema._unreachable(requests.ConnectTimeout())
        self.assertIn("error_type=ConnectTimeout http_status=None", logs.output[0])


class PurchaseOutcomeIsRecordedTests(SimpleTestCase):
    """A purchase left unsettled says so in the log, whichever way it happened."""

    def setUp(self):
        resolver = mock.patch(
            "wallet.services.biller_source_for_transaction",
            side_effect=lambda reference, *, amount, source_account="": source_account)
        resolver.start()
        self.addCleanup(resolver.stop)

    def _json(self, status_code, payload):
        resp = mock.Mock()
        resp.status_code = status_code
        resp.json.return_value = payload
        return resp

    @override_settings(WEMA=WEMA_LIVE)
    def test_a_purchase_the_bank_is_still_processing_is_logged(self):
        processing = {"hasError": False, "result": {
            "status": "Pending", "message": "Your transaction is processing.",
            "transactionReference": "ZTCH-AIR-1"}}
        with mock.patch.object(wema, "_post", return_value=self._json(200, processing)), \
             self.assertLogs("zitch", level="WARNING") as logs:
            res = wema.purchase_airtime(70, "ZTCH-AIR-1", "08166938327", "MTN",
                                        source_account="0100000001")
        self.assertTrue(res["pending"])
        line = "\n".join(logs.output)
        self.assertIn("wema_vas_purchase_pending ref=ZTCH-AIR-1 product=airtime status=PENDING", line)
        self.assertIn("Your transaction is processing.", line)

    @override_settings(WEMA=WEMA_LIVE)
    def test_a_purchase_answered_with_a_page_is_held_and_logged(self):
        session = mock.Mock()
        session.post.return_value = _http(403, b"<html>blocked</html>")
        with mock.patch.object(wema, "_gateway", return_value=session), \
             self.assertLogs("zitch", level="WARNING") as logs:
            res = wema.purchase_airtime(70, "ZTCH-AIR-2", "08166938327", "MTN",
                                        source_account="0100000001")
        # Unreadable is not refused: the debit is held, as before.
        self.assertEqual((res["success"], res["pending"]), (False, True))
        line = "\n".join(logs.output)
        self.assertIn("wema_gateway_non_json product=airtime", line)
        self.assertIn("http_status=403", line)
        self.assertIn("wema_vas_purchase_unanswered ref=ZTCH-AIR-2 product=airtime", line)

    @override_settings(WEMA=WEMA_LIVE)
    def test_data_and_bill_purchases_are_logged_the_same_way(self):
        timeout = requests.ReadTimeout()
        with mock.patch.object(wema, "_post", side_effect=timeout), \
             self.assertLogs("zitch", level="WARNING") as logs:
            data = wema.purchase_data(500, "ZTCH-DATA-1", "08166938327", "MTN", "12",
                                      source_account="0100000001")
            bill = wema.pay_bill(2000, "ZTCH-BILL-1", package_id="7", identifier="1234567",
                                 source_account="0100000001")
        self.assertTrue(data["pending"] and bill["pending"])
        line = "\n".join(logs.output)
        self.assertIn("wema_vas_purchase_unanswered ref=ZTCH-DATA-1 product=data "
                      "error_type=ReadTimeout", line)
        self.assertIn("wema_vas_purchase_unanswered ref=ZTCH-BILL-1 product=bills "
                      "error_type=ReadTimeout", line)

    @override_settings(WEMA=WEMA_LIVE)
    def test_a_settled_purchase_adds_no_warning(self):
        done = {"hasError": False, "result": {"status": "SUCCESS",
                                              "transactionReference": "ZTCH-AIR-3"}}
        with mock.patch.object(wema, "_post", return_value=self._json(200, done)), \
             self.assertNoLogs("zitch", level="WARNING"):
            res = wema.purchase_airtime(70, "ZTCH-AIR-3", "08166938327", "MTN",
                                        source_account="0100000001")
        self.assertTrue(res["success"])
