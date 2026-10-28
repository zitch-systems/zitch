from unittest.mock import Mock, patch

import requests
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from .client import Client, ProviderError, Reply, money

CONFIG = {"BASE_URL": "https://bank.example.test", "ENVIRONMENT": "production",
    "ENABLED": True, "UAT_APPROVED": True, "VAS_ENABLED": True, "WAAS_USERNAME": "test", "WAAS_PASSWORD": "test",
    "WAAS_CLIENT_ID": "test", "WAAS_CLIENT_SECRET": "test", "VAS_USERNAME": "vas", "VAS_PASSWORD": "vas"}


@override_settings(NINEPSB=CONFIG)
class ClientTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_identity_success_requires_identity_message(self):
        for body, expected in [
            ({"status": "SUCCESS", "message": "PENDING"}, "PENDING"),
            ({"status": "SUCCESS", "message": "FAILED"}, "FAILED"),
            ({"status": "SUCCESS", "message": "SUCCESS"}, "SUCCESS"),
            ({"status": "SUCCESS", "message": "Something new"}, "PENDING"),
        ]:
            with self.subTest(body=body):
                self.assertEqual(Reply(body, 200).identity_state, expected)

    def test_query_envelope_is_not_payment_success(self):
        for body in [
            {"status": "SUCCESS", "responseCode": "200"},
            {"status": "SUCCESS", "data": {"transactionStatus": "PENDING"}},
            {"status": "SUCCESS", "data": {"transactionStatus": "new-status"}},
            {"status": "FAILED", "data": {"transactionStatus": "SUCCESS"}},
        ]:
            with self.subTest(body=body):
                self.assertEqual(Reply(body, 200).payment_state(query=True), "pending")

    def test_vas_query_requires_product_success_code_and_terminal_state(self):
        for status, result in [("success", "success"), ("failed", "failed"), ("pending", "pending")]:
            with self.subTest(status=status):
                reply = Reply({"status": "success", "responseCode": "200", "data": {"transactionStatus": status}}, 200)
                self.assertEqual(reply.payment_state(product="vas", query=True), result)

    def test_waas_requery_uses_supplied_postman_contract(self):
        data = {"isSuccessful": True, "responseCode": "00", "reference": "Z9EXAMPLE0000000001", "status": "SUCCESS"}
        self.assertEqual(Reply({"status": "SUCCESS", "data": data}, 200).payment_state(query=True), "success")
        for change in ({"isSuccessful": False}, {"responseCode": "51"}, {"status": "PENDING"}):
            self.assertEqual(Reply({"status": "SUCCESS", "data": {**data, **change}}, 200).payment_state(query=True), "pending")

    def test_contradictory_payment_evidence_never_settles(self):
        for body in [
            {"status": "SUCCESS", "responseCode": "00", "data": {"isSuccessful": False}},
            {"status": "SUCCESS", "responseCode": "00", "data": {"responseCode": "51"}},
            {"status": "SUCCESS", "responseCode": "00", "data": {"status": "PENDING"}},
            {"status": "FAILED", "responseCode": "51", "data": {"isSuccessful": True}},
            {"status": "SUCCESS"},
        ]:
            self.assertEqual(Reply(body, 200).payment_state(), "pending")

    def test_ambiguous_errors_never_refund(self):
        for status in [408, 409, 429, 500, 502, 504]:
            self.assertEqual(Reply({"status": "FAILED", "responseCode": "51"}, status).payment_state(), "pending")
        for code in ["09", "42", "96", "97", "99", "not-a-code"]:
            self.assertEqual(Reply({"status": "FAILED", "responseCode": code}, 200).payment_state(), "pending")

    def test_vas_not_found_does_not_refund(self):
        self.assertEqual(Reply({"status": "failed", "responseCode": "404"}, 404).payment_state(product="vas"), "pending")

    def test_waas_and_vas_have_separate_authentication_and_cache(self):
        with patch.object(Client, "_send", side_effect=[
            Reply({"accessToken": "wallet-token", "expiresIn": 1800}, 200),
            Reply({"status": "success", "data": {"accessToken": "vas-token", "expiresIn": 1800}}, 200),
        ]) as send:
            self.assertEqual(Client().token(), "wallet-token")
            self.assertEqual(Client("vas").token(), "vas-token")
            self.assertEqual(Client().token(), "wallet-token")
            self.assertEqual(send.call_count, 2)
            self.assertTrue(send.call_args_list[0].args[1].endswith("/waas/api/v1/authenticate"))
            self.assertTrue(send.call_args_list[1].args[1].endswith("/identity/api/v1/authenticate"))

    def test_auth_timeout_is_before_instruction(self):
        with patch.object(Client, "_send", side_effect=ProviderError("network_unavailable", uncertain=True)) as send:
            with self.assertRaises(ProviderError) as raised:
                Client().request("wallet_other_banks", {})
            self.assertFalse(raised.exception.uncertain)
            self.assertEqual(send.call_count, 1)

    def test_failed_auth_is_not_cached(self):
        with patch.object(Client, "_send", return_value=Reply({"status": "failed", "responseCode": "400"}, 400)):
            with self.assertRaises(ProviderError) as raised:
                Client("vas").token()
        self.assertEqual(raised.exception.http_status, 400)
        self.assertEqual(raised.exception.response_code, "400")
        self.assertIsNone(cache.get(Client("vas")._cache_key()))

    def test_accepted_authentication_does_not_cache_an_unconfirmed_token(self):
        with patch.object(Client, "_send", return_value=Reply({"accessToken": "unconfirmed-token"}, 202)), \
                self.assertRaises(ProviderError):
            Client().token()
        self.assertIsNone(cache.get(Client()._cache_key()))

    def test_expired_auth_does_not_replay_money_instruction(self):
        with patch.object(Client, "token", return_value="t"), patch.object(Client, "_send", return_value=Reply({}, 401)) as send:
            Client().request("wallet_other_banks", {})
            self.assertEqual(send.call_count, 1)

    def test_no_redirect_or_network_retry(self):
        from . import client
        session = Mock()
        session.request.side_effect = requests.Timeout()
        with patch.object(client._local, "session", session, create=True):
            with self.assertRaises(ProviderError) as raised:
                Client._send("POST", "https://bank.example.test/pay", payload={})
        self.assertTrue(raised.exception.uncertain)
        self.assertEqual(session.request.call_count, 1)
        self.assertFalse(session.request.call_args.kwargs["allow_redirects"])

    def test_http_only_allowed_for_exact_documented_sandbox(self):
        for environment, url, permitted in [
            ("production", "http://102.216.128.75:9090", False),
            ("sandbox", "http://102.216.128.75:9090", True),
            ("sandbox", "http://another-host.test", False),
            ("production", "https://user:password@bank.example.test", False),
        ]:
            with self.subTest(url=url, environment=environment), override_settings(NINEPSB={**CONFIG, "BASE_URL": url, "ENVIRONMENT": environment}):
                if permitted:
                    self.assertEqual(Client()._base(), url)
                else:
                    with self.assertRaises(ProviderError):
                        Client()._base()

    def test_money_rejects_invalid_values_without_rounding(self):
        for value in [True, "nan", "Infinity", "-1", "0", "1.001", None, "1000000000000", "1e500", {}, []]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                money(value)
        self.assertEqual(money("10.05"), "10.05")

    def test_transfer_wire_contract(self):
        with patch.object(Client, "request", return_value=Reply({}, 200)) as request:
            Client().transfer("Z91234567890ABCDEF", "20.50", "1100000001", "1100000002", "120001", "John Doe", "Jane Doe", "Test")
        path, body = request.call_args.args
        self.assertEqual(path, "wallet_other_banks")
        self.assertEqual(body["order"]["amount"], "20.50")
        self.assertEqual(body["customer"]["account"]["senderaccountnumber"], "1100000001")
        self.assertEqual(body["transactionType"], "OTHER_BANKS")
        self.assertEqual(body["merchant"], {"isFee": False})

    def test_vas_reference_must_be_exactly_eighteen_characters(self):
        with patch.object(Client, "request") as request:
            with self.assertRaises(ValueError):
                Client("vas").topup("airtime", "too-short", "100", "1100000001", "07041267880", "MTN")
            request.assert_not_called()
