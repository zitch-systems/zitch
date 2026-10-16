"""Provider failures must never invent recipient, debit permission or settlement."""
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from utility import wema


LIVE = {"BASE_URL": "https://bank.example", "CHANNEL_ID": "test-channel",
        "KEYS": {"wallet": "test-wallet", "upgrade": "test-upgrade"},
        "SIMULATION": False, "SECURITY_INFO": "test-security"}


def response(body, status=200):
    result = Mock(status_code=status)
    result.json.return_value = body
    return result


@override_settings(WEMA=LIVE)
class NameEnquiryEvidenceTests(SimpleTestCase):
    def enquire(self, body, status=200):
        with patch.object(wema, "_get", return_value=response(body, status)):
            return wema.resolve_account("0123456789", "035")

    def test_negative_unknown_or_contradictory_envelope_never_verifies_a_name(self):
        for envelope in ({"successful": False}, {"success": False}, {},
                         {"hasError": "false"}, {"status": "failed"},
                         {"hasError": False, "successful": False}):
            with self.subTest(envelope=envelope):
                result = self.enquire({**envelope, "result": {"accountName": "ADA EZE"}})
                self.assertFalse(result["success"])

    def test_http_refusal_or_accepted_request_never_verifies_a_name(self):
        for http in (202, 301, 400, 401, 403, 404, 422, 500):
            with self.subTest(http=http):
                self.assertFalse(self.enquire(
                    {"hasError": False, "result": {"accountName": "ADA EZE"}}, http)["success"])

    def test_malformed_enquiry_payload_returns_failure_without_raising(self):
        for body in (None, [], "gateway failure", {"hasError": False, "result": []},
                     {"hasError": False, "result": {"accountName": ["ADA"]}},
                     {"hasError": False, "result": {"accountName": "  "}}):
            with self.subTest(body=body):
                self.assertFalse(self.enquire(body)["success"])
        invalid = Mock(status_code=200)
        invalid.json.side_effect = ValueError("invalid JSON")
        with patch.object(wema, "_get", return_value=invalid):
            self.assertFalse(wema.resolve_account("0123456789", "035")["success"])

    def test_echoed_bank_or_account_must_match_the_requested_recipient(self):
        for extra in ({"bankCode": "058"}, {"accountNumber": "9999999999"}):
            with self.subTest(extra=extra):
                self.assertFalse(self.enquire({"hasError": False,
                    "result": {"accountName": "ADA EZE", **extra}})["success"])
        self.assertTrue(self.enquire({"hasError": False, "result": {
            "accountName": " ADA EZE ", "bankCode": "035", "accountNumber": "0123456789"}})["success"])


@override_settings(WEMA=LIVE)
class DebitPermissionEvidenceTests(SimpleTestCase):
    def test_http_failure_or_pending_acceptance_cannot_mark_pnd_lifted(self):
        for http in (202, 301, 400, 401, 403, 404, 422):
            with self.subTest(http=http), patch.object(wema, "_post", return_value=response(
                    {"status": True, "message": "PND lifted"}, http)):
                self.assertFalse(wema.lift_debit_restriction("0123456789", bvn=True)["success"])

    def test_malformed_response_does_not_abort_the_account_activation_request(self):
        invalid = Mock(status_code=200)
        invalid.json.side_effect = ValueError("invalid JSON")
        with patch.object(wema, "_post", return_value=invalid):
            self.assertFalse(wema.lift_debit_restriction("0123456789", bvn=True)["success"])

    def test_missing_record_requires_explicit_active_unrestricted_readback(self):
        for bank in ({"success": False}, {"success": True},
                     {"success": True, "account_status": "Active", "restriction_status": "PND"},
                     {"success": True, "account_status": "Inactive", "restriction_status": "None"},
                     {"success": True, "account_status": "Active", "restriction_status": ""},
                     {"success": True, "mock": True, "account_status": "Active", "restriction_status": "None"}):
            with self.subTest(bank=bank), patch.object(wema, "_post", return_value=response(
                    {"status": False, "message": "Resource not found"})), \
                    patch.object(wema, "get_kyc_status", return_value=bank), \
                    patch.object(wema, "get_balance", return_value={"success": True}) as balance:
                self.assertFalse(wema.lift_debit_restriction("0123456789")["success"])
                balance.assert_not_called()

    def test_explicit_active_unrestricted_account_repairs_missing_pnd_record(self):
        with patch.object(wema, "_post", return_value=response(
                {"status": False, "message": "Resource not found"})), \
                patch.object(wema, "get_kyc_status", return_value={"success": True,
                    "account_status": "Active", "restriction_status": "None"}):
            result = wema.lift_debit_restriction("0123456789")
        self.assertTrue(result["success"])
        self.assertTrue(result["already_clear"])


@override_settings(WEMA=LIVE)
class PaymentEvidenceBindingTests(SimpleTestCase):
    def test_processor_timeout_status_never_authorizes_a_refund(self):
        for status in ("TIMEOUT", "TIMEDOUT", "TIMED_OUT"):
            for envelope in (True, False):
                with self.subTest(status=status, envelope=envelope):
                    self.assertEqual(wema.classify_transfer_status(status, envelope_ok=envelope), "pending")
                    result = wema._parse_transfer({"successful": envelope, "result": {
                        "status": status, "transactionReference": "REF1"}}, "REF1")
                    self.assertTrue(result["pending"])
                    self.assertFalse(result["success"])
                    refused = wema._parse_transfer({"successful": envelope, "result": {
                        "status": status, "transactionReference": "REF1"}}, "REF1", http_status=400)
                    self.assertTrue(refused["pending"])
                    self.assertFalse(refused["success"])

    def test_conflicting_status_fields_cannot_settle_or_refund(self):
        for statuses in ({"status": "SUCCESS", "transactionStatus": "FAILED"},
                         {"status": "FAILED", "transferStatus": "PENDING"},
                         {"status": "SUCCESS", "Status": "FAILED"},
                         {"status": "FAILED", "transactionStatus": True},
                         {"status": "SUCCESS", "transactionStatus": [200]}):
            with self.subTest(statuses=statuses):
                result = wema._parse_transfer({"hasError": False, "result": {
                    **statuses, "transactionReference": "REF1"}}, "REF1")
                self.assertTrue(result["pending"])
                self.assertFalse(result["success"])

    def test_nested_boolean_envelope_does_not_hide_transaction_status(self):
        for payload in ({"status": True, "data": {"transactionStatus": 200}},
                        {"status": True, "transactionStatus": 200}):
            with self.subTest(payload=payload):
                result = wema._parse_transfer({"hasError": False, "result": payload}, "REF1")
                self.assertTrue(result["success"])

    def test_nested_negative_envelope_cannot_be_overridden_by_success_status(self):
        for payload in (
            {"status": False, "transactionStatus": 200, "transactionReference": "REF1"},
            {"status": False, "data": {"transactionStatus": "SUCCESS", "transactionReference": "REF1"}},
        ):
            with self.subTest(payload=payload):
                result = wema._parse_transfer({"hasError": False, "result": payload}, "REF1")
                self.assertFalse(result["success"])
                self.assertTrue(result["pending"])
                with patch.object(wema, "_get", return_value=response({"hasError": False, "result": payload})):
                    result = wema.confirm_transfer_status("REF1")
                    self.assertFalse(result["success"])
                    self.assertTrue(result["pending"])

    def test_transfer_post_response_for_another_reference_stays_pending(self):
        for status in ("SUCCESS", "FAILED"):
            with self.subTest(status=status):
                result = wema._parse_transfer({"hasError": False, "result": {
                    "status": status, "transactionReference": "ANOTHER"}}, "REF1")
                self.assertTrue(result["pending"])
                self.assertFalse(result["success"])
                self.assertTrue(result["reference_mismatch"])

    def test_http_failure_contradicting_success_envelope_remains_pending(self):
        for http in (400, 401, 403, 404, 422):
            with self.subTest(http=http), patch.object(wema, "_post", return_value=response(
                    {"hasError": False, "result": {"status": "SUCCESS", "transactionReference": "REF1"}}, http)):
                transfer = wema.transfer(100, "REF1", "test", source_account="0123456789",
                    destination_account="9999999999", destination_bank_code="035",
                    destination_bank_name="Test Bank", destination_name="ADA EZE")
                credit = wema.credit_wallet(100, "REF1", "test", destination_account="0123456789")
                for result in (transfer, credit):
                    self.assertFalse(result["success"])
                    self.assertTrue(result["pending"])

    def test_plain_http_refusal_is_terminal(self):
        for http in (400, 401, 403, 404, 422):
            with self.subTest(http=http):
                result = wema._parse_transfer({"message": "Request refused"}, "REF1", http_status=http)
                self.assertFalse(result["pending"])
                self.assertFalse(result["success"])

    def test_credit_status_lookup_requires_successful_http_and_exact_reference(self):
        for status in ("SUCCESS", "FAILED"):
            for returned in (None, "ANOTHER"):
                with self.subTest(status=status, returned=returned), patch.object(
                        wema, "_get", return_value=response({"hasError": False, "result": {
                            "status": status, "transactionReference": returned}})):
                    result = wema.confirm_credit_status("REF1")
                    self.assertTrue(result["pending"])
                    self.assertFalse(result["success"])
            with self.subTest(status=status, http=403), patch.object(
                    wema, "_get", return_value=response({"hasError": False, "result": {
                        "status": status, "transactionReference": "REF1"}}, 403)):
                result = wema.confirm_credit_status("REF1")
                self.assertTrue(result["pending"])
                self.assertFalse(result["success"])

    def test_transfer_lookup_does_not_use_conflicting_status_fields(self):
        with patch.object(wema, "_get", return_value=response({"hasError": False, "result": {
                "status": "SUCCESS", "transactionStatus": "FAILED", "transactionReference": "REF1"}})):
            result = wema.confirm_transfer_status("REF1")
        self.assertTrue(result["pending"])
        self.assertFalse(result["success"])
