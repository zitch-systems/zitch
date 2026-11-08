"""Only a completed, well-formed, correlated bank response grants authority."""
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from .accounts import same_name
from .adapter import resolve_account
from .client import Reply, ProviderError, bank_directory, wallet_number
from .test_client import CONFIG


@override_settings(NINEPSB=CONFIG)
class ResponseEvidenceTests(SimpleTestCase):
    def test_observed_nuban_takes_precedence_over_internal_wallet_number(self):
        self.assertEqual(wallet_number({"number": "INTERNAL-WALLET", "nuban": "1100000001"}), "1100000001")
        for invalid in (None, "", "INTERNAL", True, 1100000001):
            with self.subTest(invalid=invalid):
                self.assertEqual(wallet_number({"number": "1100000001", "nuban": invalid}), "")
        self.assertEqual(wallet_number({"accountNumber": "1100000001"}), "1100000001")

    def test_bank_directory_normalizes_observed_trailing_newlines(self):
        result = bank_directory(Reply({"status": "SUCCESS", "data": {"bankList": [
            {"bankName": " Test Bank ", "bankCode": "090483\n"},
            {"bankName": "Invalid Bank", "bankCode": "invalid"},
        ]}}, 200))
        self.assertEqual(result, [{"bank_name": "Test Bank", "bank_code": "090483"}])

    def test_bank_directory_accepts_success_null_or_blank_response_code(self):
        # The 30 October sandbox build log: HTTP 200, status SUCCESS, responseCode SUCCESS.
        for body, data in (({"responseCode": "SUCCESS"}, {}), ({}, {"responseCode": "success"}),
                           ({"responseCode": None}, {}), ({}, {"responseCode": None}),
                           ({"responseCode": ""}, {"responseCode": " "}), ({"responseCode": "00"}, {})):
            with self.subTest(body=body, data=data):
                reply = Reply({"status": "SUCCESS", **body, "data": {**data, "bankList": [
                    {"bankName": "Test Bank", "bankCode": "090483"}]}}, 200)
                self.assertEqual(bank_directory(reply), [{"bank_name": "Test Bank", "bank_code": "090483"}])

    def test_bank_directory_failure_code_cannot_refresh_routing(self):
        with self.assertRaises(ProviderError) as raised:
            bank_directory(Reply({"status": "SUCCESS", "data": {"responseCode": "51", "bankList": [
                {"bankName": "Test Bank", "bankCode": "090483"}]}}, 200))
        self.assertEqual((raised.exception.code, raised.exception.http_status,
                          raised.exception.response_code), ("bank_directory_unconfirmed", 200, "51"))

    def test_bank_directory_rejection_names_only_status_and_numeric_code(self):
        cases = (
            (Reply({"status": "FAILED", "responseCode": "99", "message": "secret detail"}, 200),
             ("bank_directory_unconfirmed", 200, "99", "FAILED")),
            (Reply({"message": "Unauthorized"}, 401), ("bank_directory_unconfirmed", 401, "", "absent")),
            (Reply({"status": "FAILED", "responseCode": "Bad credentials"}, 200),
             ("bank_directory_unconfirmed", 200, "withheld", "FAILED")),
            (Reply({"status": "SUCCESS", "responseCode": "0", "data": {"bankList": []}}, 200),
             ("bank_directory_unconfirmed", 200, "0", "SUCCESS")),
            (Reply({"status": "SUCCESS", "responseCode": "SUCCESSFUL", "data": {"bankList": []}}, 200),
             ("bank_directory_unconfirmed", 200, "SUCCESSFUL", "SUCCESS")),
            (Reply({"status": "Successful", "data": {"bankList": []}}, 200),
             ("bank_directory_unconfirmed", 200, "", "Successful")),
            (Reply({"status": "secret: 0123456789", "data": {"bankList": []}}, 200),
             ("bank_directory_unconfirmed", 200, "", "unrecognized")),
            (Reply({"status": "SUCCESS", "data": {"banks": []}}, 200), ("bank_directory_malformed", 200, "", "")),
            (Reply({"status": "SUCCESS", "data": [{"bankName": "Test Bank"}]}, 200),
             ("bank_directory_malformed", 200, "", "")),
        )
        for reply, expected in cases:
            with self.subTest(expected=expected), self.assertRaises(ProviderError) as raised:
                bank_directory(reply)
            error = raised.exception
            self.assertEqual((error.code, error.http_status, error.response_code, error.provider_status), expected)

    def test_conflicting_identity_fields_cannot_grant_ownership(self):
        for extra in ({"status": "FAILED"}, {"responseCode": "51"}, {"responseCode": "200"}):
            with self.subTest(extra=extra):
                self.assertEqual(Reply({"message": "SUCCESS", **extra}, 200).identity_state, "PENDING")
        self.assertEqual(Reply({"status": "SUCCESS", "message": "FAILED", "responseCode": "00"}, 200).identity_state, "FAILED")

    def test_accepted_empty_partial_and_redirects_cannot_complete_identity_or_money(self):
        for http in (201, 202, 204, 206, 301, 302, 404, 408, 409, 429, 500):
            with self.subTest(http=http):
                reply = Reply({"status": "SUCCESS", "message": "SUCCESS", "responseCode": "00"}, http)
                self.assertFalse(reply.ok)
                self.assertEqual(reply.identity_state, "PENDING")
                self.assertEqual(reply.payment_state(), "pending")
                self.assertEqual(Reply({"status": "FAILED", "responseCode": "51"}, http).payment_state(), "pending")
                self.assertEqual(Reply({"status": "success", "responseCode": "200",
                    "data": {"transactionStatus": "success"}}, http).payment_state(product="vas", query=True), "pending")

    def test_name_enquiry_binds_account_bank_http_status_and_name_type(self):
        account = {"bank": "120001", "number": "1100000002", "name": "Jane Doe"}
        variants = [(200, account, True), (202, account, False)]
        variants += [(200, {**account, "name": value}, False)
            for value in (None, True, 1, [], ["Jane", "Doe"], {"name": "Jane Doe"}, " ", "J"*161)]
        variants += [(200, {**account, key: value}, False)
            for key, value in (("bank", "120002"), ("number", "1100000099"))]
        for http, data, expected in variants:
            with self.subTest(http=http, data=data), patch("banking.client.Client.resolve",
                    return_value=Reply({"code": "00", "customer": {"account": data}}, http)):
                self.assertIs(resolve_account("1100000002", "120001")["success"], expected)

    def test_malformed_name_enquiry_envelopes_are_refused(self):
        for body in ({}, {"code": "00", "customer": []},
                     {"code": "00", "customer": {"account": []}},
                     {"code": "51", "customer": {"account": {"name": "Jane Doe"}}}):
            with self.subTest(body=body), patch("banking.client.Client.resolve", return_value=Reply(body, 200)):
                self.assertFalse(resolve_account("1100000002", "120001")["success"])

    def test_identity_names_require_strings_not_stringified_arrays(self):
        for name in (["Jane", "Doe"], {"Jane": "Doe"}, True, None):
            with self.subTest(name=name):
                self.assertFalse(same_name("Jane Doe", name))
        self.assertTrue(same_name("Jane Doe", "Mrs Doe Jane"))
