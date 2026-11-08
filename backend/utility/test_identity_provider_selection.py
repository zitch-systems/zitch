"""Obsolete selectors cannot replace Partnership ownership verification."""
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from utility import providers


@override_settings(PREMBLY={"API_KEY": "", "APP_ID": ""},
                   WEMA_PARTNERSHIP_MODE="active", BANK_ACCOUNT_PROVIDER="partnership",
                   KYC_PROVIDER="prembly", DEBUG=False, TESTING=False,
                   WEMA={"SIMULATION": False})
class PartnershipIdentitySelectionTests(SimpleTestCase):
    def assert_bank_ownership_required(self):
        with patch("utility.providers.requests.post") as lookup, \
                patch("utility.providers.prembly_verify_bvn") as prembly_bvn, \
                patch("utility.providers.prembly_verify_nin") as prembly_nin:
            self.assertEqual(providers.kyc_provider(), "wema")
            for verify in (providers.verify_bvn, providers.verify_nin):
                result = verify("12345678901", name="Ada Eze")
                self.assertFalse(result["success"])
                self.assertTrue(result["otp_required"])
                self.assertFalse(result.get("mock", False))
        lookup.assert_not_called()
        prembly_bvn.assert_not_called()
        prembly_nin.assert_not_called()

    def test_obsolete_prembly_selection_keeps_bank_ownership_requirement(self):
        self.assert_bank_ownership_required()

    @override_settings(KYC_PROVIDER="wema", BANK_ACCOUNT_PROVIDER="wema_vas")
    def test_retired_provider_selection_cannot_reactivate_identity_lookup(self):
        self.assert_bank_ownership_required()

    @override_settings(PREMBLY={"API_KEY": "key", "APP_ID": "app"})
    def test_prembly_credentials_cannot_verify_raw_identity_in_live_mode(self):
        self.assert_bank_ownership_required()
