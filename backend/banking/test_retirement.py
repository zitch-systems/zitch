"""Retired configuration and routes cannot reopen the former bank integration."""
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from django.urls import Resolver404, resolve

from utility import providers, wema
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

    def test_retired_wire_methods_cannot_contact_the_bank_or_confirm_success(self):
        with patch("requests.sessions.Session.request") as http:
            for name in ("create_wallet_request", "validate_wallet_otp", "resend_wallet_otp",
                         "create_wallet_with_face", "get_account_details", "lift_debit_restriction",
                         "get_kyc_status", "upgrade_tier2", "upgrade_tier3", "get_balance",
                         "get_transactions", "get_banks", "get_nip_charges", "resolve_account",
                         "transfer", "confirm_transfer_status", "credit_wallet", "confirm_credit_status",
                         "purchase_airtime", "get_data_plans", "purchase_data", "get_bills",
                         "validate_bill_customer", "pay_bill", "vas_status", "vas_status_entitlement",
                         "validate_rrr", "pay_remita", "remita_receipt", "bnpl_offers", "bnpl_consent",
                         "bnpl_accept_terms", "bnpl_status", "bnpl_liquidate", "card_issue", "card_fund",
                         "card_reveal", "card_set_status", "verify_bvn", "verify_nin", "verify_vnin"):
                with self.subTest(method=name):
                    result = getattr(wema, name)()
                    self.assertFalse(result["success"])
                    self.assertTrue(result["retired"])
                    self.assertTrue(result["pending"])
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
