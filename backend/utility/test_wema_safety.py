"""Regression coverage for the September provisioning/status incident."""
import os
import subprocess
import sys
from unittest import mock

import requests
from django.test import SimpleTestCase, override_settings

from utility import providers, wema


LIVE = {"BASE_URL": "https://bank.example", "CHANNEL_ID": "test-channel",
        "KEYS": {"wallet": "test-wallet", "face_account": "test-face"},
        "SIMULATION": False}


@override_settings(WEMA=LIVE)
class StatusSafetyTests(SimpleTestCase):
    def test_timeout_alias_does_not_refund(self):
        with override_settings(WEMA={**LIVE, "VAS_STATUS_LEGEND":
                "200=success_or_pending,400=failed_insufficient_funds_or_network_timeout"}):
            result = wema._parse_vas({"hasError": False, "result": {
                "transactionStatus": 400}}, "R1", requery=True, http_status=200)
            self.assertTrue(result["pending"])
            self.assertFalse(result["success"])
            self.assertFalse(providers.vas_can_settle("airtime")[0])

    def test_conflicting_or_malformed_duplicate_codes_never_settle(self):
        for legend in ("1=success 1=failed", "01=failed 1=success",
                       "1=success 1=typo", "1=typo 1=failed 1=success"):
            with self.subTest(legend=legend), override_settings(
                    WEMA={**LIVE, "VAS_STATUS_LEGEND": legend}):
                self.assertNotIn("1", wema._vas_legend("airtime"))

    def test_unknown_product_never_borrows_airtime_legend(self):
        with override_settings(WEMA={**LIVE, "VAS_STATUS_LEGEND": "1=success"}):
            self.assertEqual(wema._vas_legend("typo"), {})

    def test_envelope_status_does_not_override_transaction_code(self):
        with override_settings(WEMA={**LIVE, "VAS_STATUS_LEGEND": "2=failed"}):
            result = wema._parse_vas({"status": "SUCCESS", "hasError": False,
                "result": {"transactionStatus": 2}}, "R1", requery=True, http_status=200)
        self.assertFalse(result["success"])
        self.assertFalse(result["pending"])

    def test_conflicting_transaction_status_signals_stay_pending(self):
        with override_settings(WEMA={**LIVE, "VAS_STATUS_LEGEND": "2=failed"}):
            result = wema._parse_vas({"hasError": False,
                "result": {"status": "SUCCESS", "transactionStatus": 2}},
                "R1", requery=True, http_status=200)
        self.assertTrue(result["pending"])
        self.assertFalse(result["success"])

    def test_numeric_code_without_successful_lookup_envelope_cannot_settle(self):
        with override_settings(WEMA={**LIVE, "VAS_STATUS_LEGEND": "1=success 2=failed"}):
            for code in (1, 2):
                result = wema._parse_vas({"result": {"transactionStatus": code}},
                    "R1", requery=True, http_status=200)
                self.assertTrue(result["pending"])
                self.assertFalse(result["success"])

    def test_failed_lookup_never_decodes_terminal_result(self):
        for http in (400, 401, 403, 404, 408, 422, 429, 500, 503):
            for code in (1, 2):
                with self.subTest(http=http, code=code), override_settings(
                        WEMA={**LIVE, "VAS_STATUS_LEGEND": "1=success 2=failed"}):
                    result = wema._parse_vas({"hasError": False,
                        "result": {"transactionStatus": code}}, "R1",
                        requery=True, http_status=http)
                    self.assertFalse(result["success"])
                    self.assertTrue(result["pending"])

    def test_error_envelope_and_reference_mismatch_stay_pending(self):
        for extra in ({"hasError": True}, {"successful": False}, {"status": False},
                      {"result": {"status": "SUCCESS", "transactionReference": "WRONG"}}):
            body = {"hasError": False, "result": {"status": "FAILED"}, **extra}
            result = wema._parse_vas(body, "R1", requery=True, http_status=200)
            self.assertFalse(result["success"])
            self.assertTrue(result["pending"])

    def test_malformed_json_is_pending_not_exception(self):
        response = mock.Mock(status_code=200)
        response.json.side_effect = ValueError("invalid JSON")
        with mock.patch.object(wema, "_post", return_value=response):
            self.assertTrue(wema.vas_status("R1")["pending"])
        for value in (None, [], "bad response"):
            self.assertTrue(wema._parse_vas(value, "R1", requery=True)["pending"])


@override_settings(WEMA=LIVE)
class FaceDiagnosticTests(SimpleTestCase):
    def create(self, response):
        with mock.patch.object(wema, "_post", return_value=response):
            return wema.create_wallet_with_face("08030000001", "private@example.test",
                identity_type="bvn", identity_value="22222222222", correlation_id="private-correlation")

    def test_branded_duplicate_is_classified_without_logging_pii(self):
        response = mock.Mock(status_code=400)
        response.json.return_value = {"message":
            "Wema BVN 22222222222 private@example.test already exists for this channel"}
        with self.assertLogs("zitch", level="WARNING") as captured:
            result = self.create(response)
        self.assertEqual(result["failure_category"], "existing_customer")
        self.assertEqual(result["account_state"], "review_required")
        self.assertEqual(result["message"], "Request failed")
        for sensitive in ("22222222222", "private@example.test", "private-correlation", "test-face"):
            self.assertNotIn(sensitive, " ".join(captured.output))

    def test_validation_category_and_http_status_are_preserved(self):
        response = mock.Mock(status_code=400)
        response.json.return_value = {"errors": {"correlationId": ["Invalid correlation reference"]}}
        result = self.create(response)
        self.assertEqual(result["failure_category"], "invalid_correlation")
        self.assertEqual(result["http_status"], 400)
        self.assertEqual(result["account_state"], "rejected")

    def test_accepted_and_unknown_outcomes_are_distinct(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {"status": True, "data": {"accountGenerationStatus": "Pending"}}
        self.assertEqual(self.create(response)["account_state"], "awaiting_callback")
        response.json.side_effect = ValueError("bad JSON")
        self.assertEqual(self.create(response)["account_state"], "unknown")
        with mock.patch.object(wema, "_post", side_effect=requests.Timeout()):
            result = wema.create_wallet_with_face("", "", identity_type="bvn",
                identity_value="22222222222", correlation_id="test-correlation")
        self.assertEqual(result["account_state"], "unknown")


class FaceOriginDefaultsTests(SimpleTestCase):
    def test_default_and_explicit_empty_verifier_cors_match(self):
        for value in (None, "", "https://face.example/path"):
            env = {k: v for k, v in os.environ.items() if k not in
                   {"WEMA_FACE_VERIFY_URL", "WEMA_FACE_CALLBACK_ORIGINS"}}
            if value is not None:
                env["WEMA_FACE_VERIFY_URL"] = value
            env["DJANGO_DEBUG"] = "true"
            result = subprocess.run([sys.executable, "-c",
                "from zitch_api import settings as s; "
                "u=s.WEMA['FACE_VERIFY_URL']; "
                "assert not u or s._url_origin(u) in s.CORS_ALLOWED_ORIGINS; "
                "assert not u or s._url_origin(u) in s.WEMA['FACE_CALLBACK_ORIGINS']"],
                env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
