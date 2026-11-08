"""The retired account integration has no active customer or bank surface."""
from io import StringIO
import json
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import Resolver404, resolve

from utility.providers import bank_account_provider, kyc_provider


class PartnershipRoutingTests(SimpleTestCase):
    def test_retired_customer_bank_and_operator_routes_are_absent(self):
        paths = ["/api/wallet/vas/enroll/", "/api/wallet/vas/status/",
                 "/vas-preflight", "/vas-transaction-query"]
        for suffix in ("account-lookup", "transaction-notification", "mini-statement",
                       "kyc-details", "block-account"):
            paths += [f"/vas/{suffix}", f"/vas/{suffix}/"]
        for path in paths:
            with self.subTest(path=path), self.assertRaises(Resolver404):
                resolve(path)

    @override_settings(BANK_ACCOUNT_PROVIDER="wema_vas", KYC_PROVIDER="prembly")
    def test_stale_environment_selectors_cannot_enable_retired_integration(self):
        self.assertEqual(bank_account_provider(), "partnership")
        self.assertEqual(kyc_provider(), "wema")


class PartnershipDiagnosticsTests(TestCase):
    def test_retired_operator_page_is_unavailable_to_admin(self):
        from accounts.models import User

        user = User.objects.create_user(username="partnership-operator",
                                        is_staff=True, is_superuser=True)
        self.client.force_login(user)
        # Django's generic admin catch-all resolves arbitrary paths; the removed
        # operator page must still be unavailable to an authenticated operator.
        response = self.client.get("/admin/vas-transaction-query/")
        self.assertEqual(response.status_code, 404)

    def test_report_is_read_only_and_does_not_claim_provider_acceptance(self):
        from django.db import connection

        def only_reads(execute, sql, params, many, context):
            self.assertTrue(sql.lstrip().upper().startswith("SELECT"), sql)
            return execute(sql, params, many, context)

        output = StringIO()
        with connection.execute_wrapper(only_reads), patch("requests.sessions.Session.request") as network:
            call_command("partnership_diagnostics", stdout=output)
        network.assert_not_called()
        report = json.loads(output.getvalue())
        self.assertEqual(report["account_provider"], "partnership")
        self.assertTrue(report["read_only"])
        self.assertFalse(report["release_authorization"])
        self.assertIn("financial_controls", report)
