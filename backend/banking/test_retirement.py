"""Retired configuration and routes cannot reopen the former bank integration."""
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from django.urls import Resolver404, resolve

from utility import providers
from .test_client import CONFIG


@override_settings(NINEPSB=CONFIG, PAYMENT_PROVIDER="wema", PAYOUT_PROVIDER="wema",
                   KYC_PROVIDER="prembly", WEMA_PARTNERSHIP_MODE="active",
                   WEMA={"BASE_URL": "https://retired.example.test", "SIMULATION": True,
                         "CHANNEL_ID": "test", "KEYS": {"wallet": "test", "airtime": "test"}})
class RetiredProviderBoundaryTests(SimpleTestCase):
    def test_legacy_provider_settings_cannot_override_bank_selection(self):
        for choose in (providers.payment_provider, providers.payout_provider,
                       providers.bank_account_provider, providers.vas_provider, providers.kyc_provider):
            self.assertEqual(choose(), "9psb")

    def test_retired_bank_routes_are_not_registered(self):
        for path in ("/api/wallet/wema/create/", "/api/wallet/wema/verify-otp/",
                     "/api/wallet/wema/resend-otp/", "/webhooks/wema/account-creation/test",
                     "/webhooks/wema/notification/test", "/wema-diagnose", "/wema-callbacks-diagnose",
                     "/api/dev/simulate-kyc/", "/api/wallet/account/create/",
                     "/api/kyc/bvn/start/", "/api/kyc/bvn/confirm/", "/api/kyc/nin/start/",
                     "/api/kyc/nin/confirm/", "/api/kyc/face/", "/api/kyc/address/"):
            with self.subTest(path=path), self.assertRaises(Resolver404):
                resolve(path)

    def test_retired_provider_client_and_operator_commands_are_gone(self):
        from importlib.util import find_spec

        from django.core.management import get_commands
        for module in ("utility.wema", "wallet.wema_callbacks", "wallet.wema_urls",
                       "wallet.identity", "wallet.account_recovery", "wallet.reconciliation"):
            with self.subTest(module=module):
                self.assertIsNone(find_spec(module))
        commands = get_commands()
        for name in ("wema_preflight", "wema_banks_sync", "seed_wema_plans", "partnership_diagnostics",
                     "prepare_account_recovery", "repair_partner_bank_accounts",
                     "reconciliation_evidence"):
            with self.subTest(command=name):
                self.assertNotIn(name, commands)
        # The live reconcile cron still invokes this name; it runs the 9PSB sweep.
        self.assertEqual(commands["reconcile_wema"], "utility")

    def test_retired_card_remita_and_bnpl_rails_never_confirm_success(self):
        with patch("requests.sessions.Session.request") as http:
            for result in (providers.card_set_status("token", True, provider="wema"),
                           providers.card_fund("token", 1, provider="wema"),
                           providers.card_reveal("token", provider="wema"),
                           providers.remita_validate("1234"), providers.bnpl_offers()):
                with self.subTest(result=result):
                    self.assertFalse(result["success"])
                    self.assertTrue(result["retired"])
            http.assert_not_called()

    def test_standalone_identity_and_face_lookups_cannot_bypass_9psb(self):
        with patch("requests.sessions.Session.request") as http:
            for name in ("verify_bvn", "verify_nin", "verify_vnin", "prembly_verify_bvn",
                         "prembly_verify_nin", "kyc_verify_face", "kyc_verify_nin_document"):
                with self.subTest(method=name):
                    result = getattr(providers, name)("11111111111")
                    self.assertFalse(result["success"])
                    self.assertTrue(result["unavailable"])
            http.assert_not_called()
