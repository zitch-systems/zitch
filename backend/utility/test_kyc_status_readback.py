"""Tier authority must come from a completed, well-formed bank read."""
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from utility import wema


class KycStatusReadbackTests(SimpleTestCase):
    def read(self, body, status=200):
        response = Mock(status_code=status)
        response.json.return_value = body
        with patch.object(wema, "_product_live", return_value=True), \
                patch.object(wema, "_get", return_value=response):
            return wema.get_kyc_status("0123456789")

    def test_http_error_or_accepted_cannot_attest_a_tier(self):
        body = {"status": True, "data": {"accountTier": "Tier 3",
                "addressVerificationStatus": "Verified"}}
        for status in (202, 400, 401, 403, 500):
            with self.subTest(status=status):
                self.assertFalse(self.read(body, status)["success"])

    def test_malformed_and_contradictory_envelopes_fail_closed(self):
        for body in ([], "verified", {"status": True, "data": "Tier 3"},
                     {"status": "true", "data": {"accountTier": 3}},
                     {"status": True, "successful": False, "data": {"accountTier": 3}}):
            with self.subTest(body=body):
                self.assertFalse(self.read(body)["success"])

    def test_invalid_json_is_a_safe_status_failure(self):
        response = Mock(status_code=200)
        response.json.side_effect = ValueError("bad json")
        with patch.object(wema, "_product_live", return_value=True), \
                patch.object(wema, "_get", return_value=response):
            self.assertFalse(wema.get_kyc_status("0123456789")["success"])

    def test_documented_status_fields_are_retained(self):
        result = self.read({"status": True, "data": {"accountTier": "Tier 3",
            "addressVerificationStatus": "Verified", "accountName": "Ada Eze"}})
        self.assertTrue(result["success"])
        self.assertEqual(result["tier"], "Tier 3")
        self.assertEqual(result["address_verification"], "Verified")
