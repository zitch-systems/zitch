"""Recipient evidence across NIP and WaaS envelopes; no provider requests."""
from copy import deepcopy
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from .adapter import resolve_account
from .client import Client, ProviderError, Reply, confirmed_recipient
from .test_client import CONFIG


@override_settings(NINEPSB=CONFIG)
class RecipientEnquiryTests(SimpleTestCase):
    number = "0123456789"
    route = "000014"

    def setUp(self):
        self.customer = {"account": {"number": self.number, "bank": self.route, "name": "Jane Doe"}}
        self.expected = {"name": "Jane Doe", "account_number": self.number, "bank_code": self.route}

    def check(self, body, http=200):
        return confirmed_recipient(Reply(body, http), self.number, self.route)

    def test_observed_nip_envelope_remains_supported(self):
        self.assertEqual(self.check({"code": "00", "customer": self.customer}), self.expected)

    def test_customer_payload_can_be_enclosed_in_the_documented_waas_envelope(self):
        for payload in ({"customer": self.customer}, {"code": "00", "customer": self.customer}):
            with self.subTest(payload=payload):
                body = {"status": "SUCCESS", "data": payload}
                self.assertEqual(self.check(body), self.expected)
                with patch.object(Client, "resolve", return_value=Reply(body, 200)):
                    self.assertEqual(resolve_account(self.number, self.route), {"success": True, **self.expected})

    def test_wrapped_lookup_must_echo_both_the_exact_account_and_route(self):
        for field, bad in (("number", "0123456788"), ("bank", "000017"), ("bank", "014"),
                           ("name", ""), ("name", None), ("number", None)):
            customer = deepcopy(self.customer)
            customer["account"][field] = bad
            with self.subTest(field=field, bad=bad):
                self.assertIsNone(self.check({"status": "SUCCESS", "data": {"customer": customer}}))

    def test_accepted_error_and_partial_http_responses_cannot_verify_a_recipient(self):
        body = {"status": "SUCCESS", "code": "00", "data": {"customer": self.customer}}
        for http in (202, 204, 206, 400, 401, 409, 429, 500, 503):
            with self.subTest(http=http):
                self.assertIsNone(self.check(body, http))

    def test_every_explicit_outcome_must_agree(self):
        bad_outcomes = ({"status": "FAILED"}, {"status": "PENDING"}, {"code": "05"},
                        {"responseCode": "51"}, {"statusCode": "99"}, {"isSuccessful": False})
        for location in ("outer", "inner"):
            for outcome in bad_outcomes:
                body = {"status": "SUCCESS", "code": "00", "data": {"code": "00", "customer": self.customer}}
                (body if location == "outer" else body["data"]).update(outcome)
                with self.subTest(location=location, outcome=outcome):
                    self.assertIsNone(self.check(body))

    def test_conflicting_top_level_and_wrapped_account_evidence_is_not_ignored(self):
        for field, bad in (("number", "0123456788"), ("bank", "000017"), ("name", "Another Holder")):
            second = deepcopy(self.customer)
            second["account"][field] = bad
            body = {"status": "SUCCESS", "code": "00", "customer": self.customer,
                    "data": {"customer": second}}
            with self.subTest(field=field):
                self.assertIsNone(self.check(body))
        same = deepcopy(self.customer)
        same["account"]["name"] = " JANE  DOE "
        self.assertEqual(self.check({"status": "SUCCESS", "customer": self.customer,
                                   "data": {"customer": same}}), self.expected)

    def test_malformed_or_absent_recipient_evidence_never_produces_a_name(self):
        for body in ({"status": "SUCCESS"}, {"status": "SUCCESS", "data": []},
                     {"status": "SUCCESS", "data": {"customer": None}},
                     {"status": "SUCCESS", "data": {"customer": {"account": []}}},
                     {"data": {"customer": self.customer}},
                     {"status": "SUCCESS", "customer": None, "data": {"customer": self.customer}}):
            with self.subTest(body=body):
                self.assertIsNone(self.check(body))
                with patch.object(Client, "resolve", return_value=Reply(body, 200)):
                    result = resolve_account(self.number, self.route)
                self.assertFalse(result["success"])
                self.assertNotIn("name", result)

    def test_transport_timeout_remains_retryable_without_inventing_a_name(self):
        with patch.object(Client, "resolve", side_effect=ProviderError("network_unavailable")):
            result = resolve_account(self.number, self.route)
        self.assertTrue(result["retryable"])
        self.assertNotIn("name", result)

    def test_observed_http_200_system_failure_keeps_the_lookup_retryable(self):
        account = {"bank": self.route, "number": self.number, "name": None,
                   "kyc": None, "senderaccountnumber": None, "sendername": None}
        body = {"code": "96", "customer": {"account": account}}
        with patch.object(Client, "resolve", return_value=Reply(body, 200)):
            result = resolve_account(self.number, self.route)
        self.assertFalse(result["success"])
        self.assertTrue(result["retryable"])
        self.assertEqual(result["code"], "bank_lookup_unavailable")
        self.assertNotIn("name", result)

    def test_wrapped_provider_outage_never_verifies_an_echoed_name(self):
        body = {"status": "SUCCESS", "data": {"responseCode": "96", "customer": self.customer}}
        with patch.object(Client, "resolve", return_value=Reply(body, 200)):
            result = resolve_account(self.number, self.route)
        self.assertFalse(result["success"])
        self.assertTrue(result["retryable"])
        self.assertNotIn("name", result)

    def test_invalid_account_response_does_not_become_a_retryable_outage(self):
        with patch.object(Client, "resolve", return_value=Reply({"code": "14"}, 200)):
            result = resolve_account(self.number, self.route)
        self.assertFalse(result["success"])
        self.assertFalse(result.get("retryable", False))
