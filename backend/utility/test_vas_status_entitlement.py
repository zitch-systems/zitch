"""Status-query readiness needs positive, current, read-only gateway evidence."""
from unittest.mock import Mock, patch

import requests
from django.test import SimpleTestCase, override_settings

from utility import wema

LIVE = {"BASE_URL": "https://bank.example", "CHANNEL_ID": "test-channel",
        "KEYS": {"wallet": "wallet-key", "airtime": "airtime-key", "bills": "bills-key"},
        "SIMULATION": False}


@override_settings(WEMA=LIVE)
class VasStatusEntitlementTests(SimpleTestCase):
    def probe(self, body=None, *, status=200, error=None, product="airtime"):
        response = Mock(status_code=status)
        response.json.return_value = body
        response.json.side_effect = error
        with patch.object(wema, "_post", return_value=response) as post:
            result = wema.vas_status_entitlement(product)
        post.assert_called_once()
        self.assertNotIn("Purchase", post.call_args.args[1])
        self.assertNotIn("PayBill", post.call_args.args[1])
        return result, post.call_args

    def test_production_profile_failure_refuses_readiness_for_both_products(self):
        for product in ("airtime", "bills"):
            for http_status in (200, 400):
                with self.subTest(product=product, http_status=http_status):
                    result, _ = self.probe({"hasError": True, "message": "product_not_profiled"},
                                           status=http_status, product=product)
                    self.assertFalse(result[0])

    def test_transport_and_json_failures_are_unavailable_without_exception_details(self):
        for error in (requests.Timeout("PRIVATE account 0123456789"), requests.ConnectionError("PRIVATE")):
            with patch.object(wema, "_post", side_effect=error) as post:
                allowed, reason = wema.vas_status_entitlement()
            post.assert_called_once()
            self.assertFalse(allowed)
            self.assertNotIn("PRIVATE", reason)
            self.assertNotIn("0123456789", reason)
        result, _ = self.probe(error=ValueError("PRIVATE malformed response"))
        self.assertFalse(result[0])
        self.assertNotIn("PRIVATE", result[1])

    def test_http_failure_never_becomes_query_access_even_with_plausible_body(self):
        for status in (202, 204, 301, 400, 401, 403, 404, 408, 409, 422, 429, 500, 503):
            result, _ = self.probe({"hasError": False, "result": {"transactionStatus": 200}}, status=status)
            self.assertFalse(result[0], status)

    def test_malformed_or_unknown_responses_fail_closed(self):
        for body in (None, [], "ok", 1, {}, {"hasError": False},
                     {"hasError": False, "result": []},
                     {"hasError": False, "result": {"transactionStatus": "200"}},
                     {"hasError": False, "result": {"transactionStatus": True}},
                     {"hasError": False, "result": {"transactionStatus": 999}},
                     {"hasError": False, "result": {"status": "UNKNOWN"}},
                     {"hasError": True, "message": "Something went wrong"}):
            result, _ = self.probe(body)
            self.assertFalse(result[0], body)

    def test_exact_empty_no_record_envelope_proves_query_access(self):
        for product in ("airtime", "bills", "bill", "data"):
            result, call = self.probe({"hasError": True, "message": "Record not found", "result": None}, product=product)
            self.assertEqual(result, (True, ""))
            self.assertTrue(call.args[2]["transactionReference"].startswith("ZITCH-PREFLIGHT-"))
            if product in ("bills", "bill"):
                self.assertEqual(call.args[:2], ("bills", "/api/PartnerPayment/checktransactionstatus"))
                self.assertNotIn("transactionType", call.args[2])
            else:
                self.assertEqual(call.args[2]["transactionType"], 1)

    def test_generic_missing_or_contradictory_no_record_response_is_not_evidence(self):
        for body in ({"message": "Record not found"},
                     {"hasError": False, "message": "Record not found"},
                     {"hasError": True, "status": True, "message": "Record not found"},
                     {"hasError": True, "message": "Record not found: PRIVATE account"},
                     {"hasError": True, "message": "Record not found", "responseCode": 401},
                     {"hasError": True, "message": "Record not found", "statusCode": 401},
                     {"hasError": True, "message": "Record not found", "result": {"transactionStatus": 401}}):
            result, _ = self.probe(body)
            self.assertFalse(result[0], body)

    def test_confirmed_query_results_prove_access_without_resolving_any_payment(self):
        for code in (200, 400):
            result, _ = self.probe({"hasError": False, "result": {"transactionStatus": code}})
            self.assertEqual(result, (True, ""))

    def test_structured_message_fields_cannot_hide_refusal_evidence(self):
        for message in (["Record not found", "product_not_profiled"],
                        {"first": "Record not found", "second": "Authentication Failed"},
                        [], {}, False, 0):
            with self.subTest(message=message):
                result, _ = self.probe({"hasError": True, "message": message})
                self.assertFalse(result[0])
                result, _ = self.probe({"hasError": False, "result": {
                    "transactionStatus": 200, "message": message}})
                self.assertFalse(result[0])

    def test_nested_refusal_or_ambiguous_envelope_fields_fail_closed(self):
        for evidence in ({"hasError": True}, {"success": False}, {"successful": False},
                         {"hasError": "false"}, {"success": 1}, {"successful": None},
                         {"pending": True}, {"pending": "false"}, {"pending": 0},
                         {"statusCode": 401}, {"status_code": 401}, {"code": 200},
                         {"errorCode": 401}, {"error_code": 401},
                         {"responseCode": 401}, {"response_code": 401}):
            with self.subTest(evidence=evidence):
                result, _ = self.probe({"hasError": False, "result": {
                    "transactionStatus": 200, **evidence}})
                self.assertFalse(result[0])

    def test_consistent_nested_envelope_flags_allow_query_access(self):
        for code, status in ((200, "SUCCESS"), (400, "FAILED")):
            result, _ = self.probe({"hasError": False, "result": {
                "transactionStatus": code, "status": status, "hasError": False,
                "success": True, "successful": True, "pending": False}})
            self.assertEqual(result, (True, ""))

    def test_authentication_and_conflicting_result_signals_fail_closed(self):
        for body in ({"hasError": False, "result": {"transactionStatus": 401}},
                     {"hasError": False, "statusCode": 401, "result": {"transactionStatus": 200}},
                     {"hasError": False, "success": False, "result": {"transactionStatus": 200}},
                     {"hasError": False, "result": {"transactionStatus": 200, "status": "FAILED"}},
                     {"hasError": False, "result": {"transactionStatus": 400, "status": "SUCCESS"}},
                     {"hasError": False, "message": "Authentication Failed PRIVATE", "result": {"transactionStatus": 200}},
                     {"hasError": False, "message": "Unexpected error", "result": {"transactionStatus": 200}},
                     {"hasError": False, "result": {"transactionStatus": 200, "transactionReference": "different"}}):
            allowed, reason = self.probe(body)[0]
            self.assertFalse(allowed, body)
            self.assertNotIn("PRIVATE", reason)

    def test_prior_success_cannot_mask_a_new_failure(self):
        good = Mock(status_code=200)
        good.json.return_value = {"hasError": True, "message": "Record not found"}
        bad = Mock(status_code=400)
        bad.json.return_value = {"hasError": True, "message": "product_not_profiled"}
        with patch.object(wema, "_post", side_effect=[good, bad]) as post:
            self.assertTrue(wema.vas_status_entitlement()[0])
            self.assertFalse(wema.vas_status_entitlement()[0])
        self.assertEqual(post.call_count, 2)

    def test_nonlive_product_is_not_probed_and_unknown_product_is_denied(self):
        with override_settings(WEMA={"KEYS": {}, "SIMULATION": False}), patch.object(wema, "_post") as post:
            self.assertEqual(wema.vas_status_entitlement("airtime"), (True, ""))
            self.assertFalse(wema.vas_status_entitlement("unknown")[0])
        post.assert_not_called()
