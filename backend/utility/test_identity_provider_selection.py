"""The selected identity provider cannot silently become account creation or a mock."""
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from utility import providers


@override_settings(PREMBLY={"API_KEY": "", "APP_ID": ""},
                   WEMA_PARTNERSHIP_MODE="active", BANK_ACCOUNT_PROVIDER="partnership",
                   KYC_PROVIDER="prembly")
class SelectedPremblyTests(SimpleTestCase):
    def assert_no_fallback(self):
        with patch("utility.providers.requests.post") as lookup, \
                patch("utility.wema.verify_bvn") as bank_bvn, \
                patch("utility.wema.verify_nin") as bank_nin:
            for verify in (providers.verify_bvn, providers.verify_nin):
                result = verify("12345678901", name="Ada Eze")
                self.assertFalse(result["success"])
                self.assertEqual(result["code"], "identity_provider_unavailable")
                self.assertFalse(result.get("mock", False))
                self.assertFalse(result.get("otp_required", False))
        lookup.assert_not_called()
        bank_bvn.assert_not_called()
        bank_nin.assert_not_called()

    def test_explicit_prembly_without_credentials_never_falls_back(self):
        self.assert_no_fallback()

    @override_settings(KYC_PROVIDER="wema", BANK_ACCOUNT_PROVIDER="wema_vas")
    def test_vas_selection_cannot_reenter_the_legacy_identity_rail(self):
        self.assert_no_fallback()

    @override_settings(WEMA={"SIMULATION": True}, PREMBLY={"API_KEY": "key", "APP_ID": "app"})
    def test_selected_prembly_simulation_does_not_mock_verify_raw_identities(self):
        self.assert_no_fallback()
