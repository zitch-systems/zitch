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
                    {"status": True, "message": "PND lifted"}, http)), \
                    patch.object(wema, "get_kyc_status", return_value={"success": False}):
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


# The answers the live gateway gave for one ACTIVE, unrestricted face-opened
# account on 2026-11-13: only the BVN product serves the operation, and it
# refuses a lift there is no hold for; the others are the gateway's own 404.
NOT_PERMITTED = {"message": "Request to lift pnd on this account is not permitted.",
                 "status": False, "code": "0", "statusCode": "BadRequest"}
GATEWAY_404 = {"statusCode": 404, "message": "Resource not found"}
KYC_OK = {"message": "Operation Successful", "status": True, "code": "0", "statusCode": "OK"}
KYC_DATA = {"accountNumber": "0123456789", "accountName": "ADA EZE",
            "accountTier": "Partnership Accounts – Tier 1", "accountStatus": "ACTIVE",
            "restrictionStatus": " "}


def _lift_answers(product, path, body, params=None):
    return (response(NOT_PERMITTED, 400) if product == "wallet_bvn"
            else response(GATEWAY_404, 404))


@override_settings(WEMA=LIVE)
class UnrestrictedAccountReadbackTests(SimpleTestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()

    def _lift(self, kyc_data, kyc_envelope=KYC_OK):
        with patch.object(wema, "_post", side_effect=_lift_answers) as post, \
                patch.object(wema, "_get", return_value=response(
                    {**kyc_envelope, "data": kyc_data})) as get:
            return wema.lift_debit_restriction("0123456789"), post, get

    def test_blank_restriction_on_an_active_account_means_no_hold(self):
        result, post, get = self._lift(KYC_DATA)
        self.assertTrue(result["success"])
        self.assertTrue(result["already_clear"])
        self.assertEqual(result["evidence"]["account_status"], "ACTIVE")
        self.assertEqual([a["product"] for a in result["attempts"]], ["wallet_bvn", "wallet_nin"])
        self.assertTrue(get.call_args[0][1].endswith("partner-account-kyc-status"))

    def test_a_missing_restriction_field_is_not_evidence(self):
        data = {k: v for k, v in KYC_DATA.items() if k != "restrictionStatus"}
        self.assertFalse(self._lift(data)[0]["success"])
        self.assertFalse(self._lift({**KYC_DATA, "restrictionStatus": None})[0]["success"])

    def test_a_hold_or_an_inactive_account_is_refused(self):
        for change in ({"restrictionStatus": "D"}, {"restrictionStatus": "PND"},
                       {"restrictionStatus": "Post No Debit"},
                       {"accountStatus": "DORMANT"}, {"accountStatus": " "}):
            with self.subTest(change=change):
                self.assertFalse(self._lift({**KYC_DATA, **change})[0]["success"])

    def test_an_unauthenticated_status_read_is_refused(self):
        refused = {"message": "Failed", "status": False, "code": "1", "statusCode": "BadRequest"}
        self.assertFalse(self._lift(KYC_DATA, kyc_envelope=refused)[0]["success"])

    def test_the_banks_own_reason_is_reported_not_the_gateway_404(self):
        result, _post, _get = self._lift({**KYC_DATA, "restrictionStatus": "D"})
        self.assertEqual(result["message"], NOT_PERMITTED["message"])
        self.assertEqual(result["product"], "wallet_bvn")
        line = wema.describe_pnd_attempts(result["attempts"])
        self.assertIn("wallet_bvn:400:Request to lift pnd", line)
        self.assertIn("wallet_nin:404:Resource not found", line)

    def test_an_unreachable_gateway_does_not_ask_for_the_status(self):
        import requests
        with patch.object(wema, "_post", side_effect=requests.ConnectionError("down")), \
                patch.object(wema, "get_kyc_status") as status:
            self.assertFalse(wema.lift_debit_restriction("0123456789")["success"])
        status.assert_not_called()

    def test_a_recorded_bank_refusal_outranks_the_status_read_until_a_real_lift(self):
        wema.record_debit_refusal("0123456789")
        result, _post, get = self._lift(KYC_DATA)
        self.assertFalse(result["success"])
        self.assertTrue(result["debit_refused_recently"])
        get.assert_not_called()
        # Another account is unaffected.
        with patch.object(wema, "_post", side_effect=_lift_answers), \
                patch.object(wema, "_get", return_value=response({**KYC_OK, "data": KYC_DATA})):
            self.assertTrue(wema.lift_debit_restriction("0123456780")["success"])
        # A genuine lift clears the refusal.
        with patch.object(wema, "_post", return_value=response({"status": True, "message": "PND lifted"})):
            self.assertTrue(wema.lift_debit_restriction("0123456789")["success"])
        self.assertTrue(self._lift(KYC_DATA)[0]["success"])

    def test_a_cache_outage_never_fails_the_check(self):
        from django.core.cache import cache
        down = RuntimeError("redis down")
        with patch.object(cache, "get", side_effect=down), \
                patch.object(cache, "set", side_effect=down), \
                patch.object(cache, "delete", side_effect=down):
            wema.record_debit_refusal("0123456789")
            self.assertTrue(self._lift(KYC_DATA)[0]["success"])
            with patch.object(wema, "_post", return_value=response(
                    {"status": True, "message": "PND lifted"})):
                self.assertTrue(wema.lift_debit_restriction("0123456789")["success"])

    def test_attempt_summary_masks_numbers_and_stays_on_one_line(self):
        line = wema.describe_pnd_attempts([
            {"product": "wallet_bvn", "http_status": 400, "message": "Account 0123456789\nbad"},
            {"product": "face_account", "unreachable": True, "message": "down"}])
        self.assertNotIn("0123456789", line)
        self.assertNotIn("\n", line)
        self.assertIn("face_account:unreachable:down", line)
        self.assertEqual(wema.describe_pnd_attempts(None), "none")

    def test_restriction_messages_are_recognised(self):
        for message in ("Account has Post No Debit restriction", "PND on account",
                        "Debit restriction on source account", "Source account is frozen",
                        "post-no-debit"):
            with self.subTest(message=message):
                self.assertTrue(wema.is_debit_restriction_message(message))
        for message in ("Insufficient funds", "Invalid beneficiary account", "Request failed",
                        "", None, "Daily limit exceeded", "Beneficiary account is restricted",
                        "Recipient account frozen"):
            with self.subTest(message=message):
                self.assertFalse(wema.is_debit_restriction_message(message))


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
