"""Independent product destinations and redacted authentication diagnostics.

All hosts and credentials are synthetic. No test contacts the bank.
"""
import json
from io import StringIO
from unittest.mock import Mock, patch

from django.core.cache import cache
from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

from . import client as transport
from .bills import purchase_topup
from .client import Client, ProviderError, Reply, configured
from .readiness import endpoint_report, report
from .test_client import CONFIG
from .uat import sandbox_checks

SPLIT_CONFIG = {**CONFIG,
    "BASE_URL": "https://wallet.example.test",
    "VAS_BASE_URL": "https://bills.example.test/vas/api/v1",
    "VAS_AUTH_URL": "https://identity.example.test/identity/api/v1/authenticate"}


def auth_reply(token="vas-only-token"):
    return Reply({"status": "success", "responseCode": "200", "data": {
        "accessToken": token, "expiresIn": 1800}}, 200)


@override_settings(NINEPSB=SPLIT_CONFIG)
class AuthRoutingTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_real_transport_uses_separate_hosts_bodies_and_bearer_tokens(self):
        replies = [auth_reply().body, {"status": "success", "responseCode": "200", "data": []},
                   {"accessToken": "wallet-only-token", "expiresIn": 1800},
                   {"status": "SUCCESS", "data": {"bankList": []}}]
        session = Mock()
        session.request.side_effect = [Mock(status_code=200, json=Mock(return_value=body)) for body in replies]
        with patch.object(transport._local, "session", session, create=True):
            Client("vas").categories()
            Client().banks()
        calls = session.request.call_args_list
        self.assertEqual([call.args for call in calls], [
            ("POST", "https://identity.example.test/identity/api/v1/authenticate"),
            ("GET", "https://bills.example.test/vas/api/v1/billspayment/categories"),
            ("POST", "https://wallet.example.test/waas/api/v1/authenticate"),
            ("GET", "https://wallet.example.test/waas/api/v1/get_banks")])
        self.assertEqual(calls[0].kwargs["json"], {"username": "vas", "password": "vas"})
        self.assertEqual(calls[2].kwargs["json"], {
            "username": "test", "password": "test", "clientId": "test", "clientSecret": "test"})
        for index in (0, 2):
            self.assertNotIn("Authorization", calls[index].kwargs["headers"])
        self.assertEqual(calls[1].kwargs["headers"]["Authorization"], "Bearer vas-only-token")
        self.assertEqual(calls[3].kwargs["headers"]["Authorization"], "Bearer wallet-only-token")
        for call in calls:
            self.assertEqual(call.kwargs["headers"]["Content-Type"], "application/json")
            self.assertFalse(call.kwargs["allow_redirects"])
            self.assertEqual(call.kwargs["timeout"], (5, 20))

    def test_vas_does_not_require_or_inherit_waas_host_or_credentials(self):
        conf = {k: v for k, v in SPLIT_CONFIG.items() if not k.startswith("WAAS_") and k != "BASE_URL"}
        with override_settings(NINEPSB=conf), patch.object(Client, "_send", return_value=auth_reply()) as send:
            self.assertTrue(configured("vas"))
            self.assertFalse(configured("waas"))
            self.assertEqual(Client("vas").token(), "vas-only-token")
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args.args[1], SPLIT_CONFIG["VAS_AUTH_URL"])

    def test_missing_vas_endpoint_never_falls_back_to_waas_or_a_document_default(self):
        for field, error in (("VAS_AUTH_URL", "vas_auth_endpoint_missing"),
                             ("VAS_BASE_URL", "vas_api_endpoint_missing")):
            with self.subTest(field=field), override_settings(NINEPSB={**SPLIT_CONFIG, field: ""}), \
                    patch.object(Client, "_send") as send:
                self.assertFalse(configured("vas"))
                self.assertTrue(configured("waas"))
                with self.assertRaisesRegex(ProviderError, error):
                    Client("vas").categories()
                send.assert_not_called()

    def test_waas_auth_or_business_url_cannot_be_configured_as_vas_identity(self):
        for path in ("/waas/api/v1/authenticate", "/vas/api/v1/authenticate",
                     "/identity/api/v1", "/identity/api/v1/../authenticate"):
            with self.subTest(path=path), override_settings(NINEPSB={**SPLIT_CONFIG,
                    "VAS_AUTH_URL": "https://identity.example.test" + path}), \
                    patch.object(Client, "_send") as send, self.assertRaises(ProviderError):
                Client("vas").token()
            send.assert_not_called()

    def test_vas_api_cannot_be_a_waas_root_or_unqualified_shared_host(self):
        for path in ("", "/waas/api/v1", "/identity/api/v1"):
            with self.subTest(path=path), override_settings(NINEPSB={**SPLIT_CONFIG,
                    "VAS_BASE_URL": "https://bills.example.test" + path}), \
                    patch.object(Client, "_send") as send, self.assertRaises(ProviderError):
                Client("vas").categories()
            send.assert_not_called()

    def test_bad_credentials_or_endpoint_response_never_tries_another_login(self):
        for status in (400, 401, 403, 404, 500):
            with self.subTest(status=status), patch.object(Client, "_send", return_value=Reply({
                    "status": "failed", "responseCode": str(status), "message": "Bad credentials"}, status)) as send:
                with self.assertRaisesRegex(ProviderError, "authentication_failed") as raised:
                    Client("vas").categories()
                self.assertEqual(raised.exception.http_status, status)
                self.assertEqual(send.call_count, 1)
                self.assertEqual(send.call_args.args[1], SPLIT_CONFIG["VAS_AUTH_URL"])
                self.assertEqual(send.call_args.kwargs["payload"], {"username": "vas", "password": "vas"})

    def test_endpoint_changes_invalidate_the_token_and_catalog_namespace(self):
        with patch.object(Client, "_send", return_value=auth_reply()) as send:
            key = Client("vas")._cache_key()
            Client("vas").token()
            for change in ({"VAS_AUTH_URL": "https://new-identity.example.test/identity/api/v2/authenticate"},
                           {"VAS_BASE_URL": "https://new-bills.example.test/vas/api/v1"}):
                with override_settings(NINEPSB={**SPLIT_CONFIG, **change}):
                    self.assertNotEqual(Client("vas")._cache_key(), key)
                    Client("vas").token()
            self.assertEqual(send.call_count, 3)

    def test_waas_changes_do_not_replace_or_refresh_vas_tokens(self):
        with patch.object(Client, "_send", return_value=auth_reply()) as send:
            key = Client("vas")._cache_key()
            Client("vas").token()
            with override_settings(NINEPSB={**SPLIT_CONFIG, "BASE_URL": "https://new-wallet.example.test",
                                          "WAAS_USERNAME": "different-wallet-user"}):
                self.assertEqual(Client("vas")._cache_key(), key)
                self.assertEqual(Client("vas").token(), "vas-only-token")
            self.assertEqual(send.call_count, 1)

    def test_unsafe_config_urls_are_rejected_without_reflecting_secrets(self):
        urls = ["https://user:secret-value@identity.example.test/identity/api/v1/authenticate",
                "https://identity.example.test/identity/api/v1/authenticate?key=secret-value",
                "https://identity.example.test/identity/api/v1/authenticate#secret-value",
                "https://identity.example.test:99999/identity/api/v1/authenticate",
                "https://identity.example.test/identity/api/v1/authen\nticate",
                "https://identity.example.test/identity/api/v1/%61uthenticate",
                " https://identity.example.test/identity/api/v1/authenticate"]
        for url in urls:
            with self.subTest(url=url), override_settings(NINEPSB={**SPLIT_CONFIG, "VAS_AUTH_URL": url}), \
                    patch.object(Client, "_send") as send:
                details = endpoint_report("vas")
                self.assertFalse(details["configured"])
                self.assertIsNone(details["authentication_url"])
                self.assertNotIn("secret-value", json.dumps(details))
                with self.assertRaises(ProviderError):
                    Client("vas").token()
                send.assert_not_called()

    def test_insecure_transport_exception_is_sandbox_only_and_origin_specific(self):
        for environment, host, allowed in (("sandbox", "http://102.216.128.75:9090", True),
                                           ("production", "http://102.216.128.75:9090", False),
                                           ("sandbox", "http://102.216.128.75:9091", False),
                                           ("sandbox", "http://another-bank.example.test", False)):
            conf = {**SPLIT_CONFIG, "ENVIRONMENT": environment, "VAS_BASE_URL": host + "/vas/api/v1",
                    "VAS_AUTH_URL": host + "/identity/api/v1/authenticate"}
            with self.subTest(environment=environment, host=host), override_settings(NINEPSB=conf):
                self.assertEqual(configured("vas"), allowed)

    def test_malformed_configuration_is_reported_without_crashing_preflight(self):
        for field in ("BASE_URL", "VAS_BASE_URL", "VAS_AUTH_URL"):
            with self.subTest(field=field), override_settings(NINEPSB={**SPLIT_CONFIG,
                    field: "https://[invalid-ipv6/secret-value"}), patch.object(Client, "_send") as send:
                result = report()
                self.assertFalse(result["production_ready"])
                self.assertIn("invalid_base_url", result["blockers"])
                self.assertNotIn("secret-value", json.dumps(result))
                send.assert_not_called()

    def test_bad_vas_config_stops_checkout_before_wallet_lookup_or_ledger_reservation(self):
        with override_settings(NINEPSB={**SPLIT_CONFIG, "VAS_AUTH_URL": ""}), \
                patch("banking.bills.require_enabled"), patch("banking.bills.spending_account") as wallet, \
                patch("banking.bills.debit") as debit, patch.object(Client, "_send") as send:
            with self.assertRaisesRegex(ValueError, "temporarily unavailable"):
                purchase_topup(Mock(), "airtime", "07041267880", "100", idempotency_key="test-checkout")
        wallet.assert_not_called()
        debit.assert_not_called()
        send.assert_not_called()

    def test_preflight_reports_missing_endpoint_without_blame_or_network_calls(self):
        with override_settings(NINEPSB={**SPLIT_CONFIG, "VAS_AUTH_URL": ""}), \
                patch.object(Client, "_send") as send:
            result = report()
        self.assertIn("vas_auth_endpoint_missing", result["blockers"])
        self.assertNotIn("vas_credentials_missing", result["blockers"])
        self.assertTrue(result["endpoints"]["vas"]["credentials_present"])
        self.assertFalse(result["vas_configured"])
        send.assert_not_called()

    def test_vas_only_sandbox_check_never_authenticates_waas(self):
        with override_settings(NINEPSB={**SPLIT_CONFIG, "ENVIRONMENT": "sandbox"}), \
                patch.object(Client, "_send", side_effect=[auth_reply(), Reply({"status": "success",
                    "responseCode": "200", "data": [{"id": "1", "name": "Electricity"}]}, 200)]) as send:
            result = sandbox_checks(product="vas")
        self.assertEqual(set(result["endpoints"]), {"vas"})
        self.assertFalse(any(row["check"].startswith("waas") for row in result["checks"]))
        self.assertEqual(send.call_count, 2)
        self.assertEqual(send.call_args_list[0].args[1], SPLIT_CONFIG["VAS_AUTH_URL"])
        self.assertFalse(result["uat_complete"])
        self.assertFalse(result["activation_ready"])
        self.assertNotIn("vas-only-token", json.dumps(result))

    def test_sandbox_command_forwards_product_without_printing_tokens(self):
        output = StringIO()
        with patch("banking.management.commands.bank_sandbox_check.sandbox_checks", return_value={
                "connectivity_passed": True, "uat_complete": False}) as checks:
            call_command("bank_sandbox_check", product="vas", stdout=output)
        checks.assert_called_once_with({}, product="vas")
        self.assertFalse(json.loads(output.getvalue())["uat_complete"])

    def test_unknown_product_is_rejected_before_network(self):
        with patch.object(Client, "_send") as send, self.assertRaises(ValueError):
            sandbox_checks(product="something-else")
        send.assert_not_called()
