"""BVN/NIN acceptance follows the provider result code, not HTTP success alone."""
from copy import deepcopy
from unittest.mock import Mock, patch

import requests
from django.test import SimpleTestCase, override_settings

from utility import providers


@override_settings(PREMBLY={"BASE_URL": "https://identity.example.test", "API_KEY": "test", "APP_ID": ""},
                   WEMA={"SIMULATION": False}, KYC_PROVIDER="prembly")
class PremblyIdentityContractTests(SimpleTestCase):
    def payload(self):
        return {"status": True, "response_code": "00", "data": {
            "first_name": "ADA", "last_name": "EZE", "phone_number": "08012345678"}}

    def lookup(self, payload, *, http_status=200, error=None, kind="bvn"):
        response = Mock(status_code=http_status)
        response.json.side_effect = error
        response.json.return_value = payload
        with patch("utility.providers.requests.post", return_value=response) as post:
            result = getattr(providers, "verify_" + kind)("12345678901", name="Ada Eze")
        post.assert_called_once()
        self.assertFalse(post.call_args.kwargs["allow_redirects"])
        self.assertNotIn("app-id", post.call_args.kwargs["headers"])
        return result

    def test_documented_success_with_optional_app_id(self):
        self.assertTrue(providers._prembly_identity_live())
        self.assertFalse(providers._prembly_live())  # separate biometric gate
        for kind in ("bvn", "nin"):
            with self.subTest(kind=kind):
                result = self.lookup(self.payload(), kind=kind)
                self.assertTrue(result["success"])
                self.assertEqual(result["phone"], "2348012345678")

    def test_plausible_record_cannot_override_non_success_codes(self):
        for code in ("01", "02", "03", "07", "99", "", None, 0, ["00"]):
            for status in (True, False):
                with self.subTest(code=code, status=status):
                    data = {**self.payload(), "response_code": code, "status": status,
                            "message": "PRIVATE 12345678901 ADA EZE"}
                    result = self.lookup(data)
                    self.assertFalse(result["success"])
                    self.assertEqual(result.get("invalid", False), code in ("01", "07"))
                    self.assertNotIn("PRIVATE", str(result))
                    self.assertNotIn("12345678901", str(result))
                    self.assertNotIn("ADA EZE", str(result))
                    self.assertNotIn("raw", result)

    def test_missing_code_and_truthy_status_are_unavailable(self):
        missing = self.payload()
        missing.pop("response_code")
        for data in (missing, *({**self.payload(), "status": value} for value in (1, "true", None, False))):
            result = self.lookup(data)
            self.assertFalse(result["success"])
            self.assertFalse(result.get("invalid", False))

    def test_malformed_body_or_record_is_unavailable(self):
        for data in (None, [], [self.payload()], "success", 1,
                     *({**self.payload(), "data": value} for value in (None, [], "record", {})),
                     {**self.payload(), "data": {"first_name": {"value": "ADA"}}}):
            with self.subTest(data=data):
                result = self.lookup(data)
                self.assertFalse(result["success"])
                self.assertFalse(result.get("invalid", False))

    def test_malformed_json_and_network_exception_never_echo_identity(self):
        result = self.lookup(None, error=ValueError("PRIVATE 12345678901"))
        self.assertFalse(result["success"])
        self.assertNotIn("12345678901", str(result))
        with patch("utility.providers.requests.post", side_effect=requests.Timeout("PRIVATE 12345678901")) as post:
            result = providers.verify_bvn("12345678901", name="Ada Eze")
        post.assert_called_once()
        self.assertFalse(result["success"])
        self.assertFalse(result.get("invalid", False))
        self.assertNotIn("12345678901", str(result))

    def test_http_failures_cannot_mark_customer_invalid_or_verified(self):
        for http_status in (202, 204, 302, 400, 401, 403, 404, 429, 500):
            for code in ("00", "01"):
                result = self.lookup({**self.payload(), "response_code": code}, http_status=http_status)
                self.assertFalse(result["success"])
                self.assertFalse(result.get("invalid", False))

    def test_secondary_verification_signals_must_agree(self):
        for location in ("root", "record"):
            for field, value in (("verification", {"status": "PENDING"}),
                                 ("verification", {"status": "FAILED"}),
                                 ("verification", None), ("verification", []),
                                 ("verification_status", "unverified"),
                                 ("verificationStatus", "in_progress"), ("is_verified", False)):
                with self.subTest(location=location, field=field, value=value):
                    data = deepcopy(self.payload())
                    container = data if location == "root" else data["data"]
                    container[field] = value
                    result = self.lookup(data)
                    self.assertFalse(result["success"])
                    self.assertFalse(result.get("invalid", False))
        data = {**self.payload(), "verification": {"status": "VERIFIED"}}
        self.assertTrue(self.lookup(data)["success"])
        data = {**self.payload(), "verification": {"reference": "provider-reference"},
                "verification_status": "verified"}
        self.assertTrue(self.lookup(data)["success"])
