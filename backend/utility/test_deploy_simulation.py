"""The deploy-wide simulation switch must beat every staged live credential.

Without this invariant an electricity/data path can fall through to VTU.ng, or
KYC/FX/cards/bank-linking can call their live provider, while the UI still says
the deployment is simulated.
"""
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from . import mono, providers, vtung


_STAGED = {
    "PREMBLY": {
        "API_KEY": "prembly-live-key",
        "APP_ID": "prembly-app",
        "BASE_URL": "https://prembly.invalid",
    },
    "VTUNG": {
        "API_KEY": "vtung-live-key",
        "USERNAME": "",
        "PASSWORD": "",
        "BASE_URL": "https://vtung.invalid",
    },
    "FINCRA": {
        "SECRET_KEY": "fincra-live-key",
        "BASE_URL": "https://fincra.invalid",
        "BUSINESS_ID": "business",
    },
    "CARD_ISSUER": {
        "API_KEY": "issuer-live-key",
        "BASE_URL": "https://issuer.invalid",
        "BRAND": "Verve",
    },
    "MONO": {
        "SECRET_KEY": "mono-live-key",
        "BASE_URL": "https://mono.invalid",
        "SIMULATION": False,
    },
}


@override_settings(
    DEBUG=False,
    TESTING=False,
    WEMA={"SIMULATION": True},
    **_STAGED,
)
class DeployWideSimulationTests(SimpleTestCase):
    def test_all_live_selectors_are_off_despite_staged_keys(self):
        self.assertFalse(providers._prembly_live())
        self.assertFalse(providers._card_issuer_live())
        self.assertFalse(providers.fincra_live())
        self.assertFalse(vtung._live())
        self.assertTrue(mono.mono_simulation())
        self.assertFalse(mono.mono_live())

    def test_provider_operations_take_mock_paths_without_network(self):
        with patch("utility.providers.requests.post") as provider_post, \
             patch("utility.vtung.requests.request") as vtung_request, \
             patch("utility.mono.requests.post") as mono_post:
            bvn = providers.verify_bvn("12345678901", name="Ada Test")
            face = providers.kyc_verify_face("fake-image")
            card = providers.issue_card("ADA TEST", "test-user")
            quote = providers.fx_quote("NGN", "USD", "1000")
            airtime = vtung.vt_purchase(
                "mtn-airtime",
                {"amount": "100", "phone": "08012345678"},
                "SIM-AIRTIME-1",
            )
            linked = mono.exchange_token("simulation-code")

        for result in (bvn, face, card, quote, airtime, linked):
            self.assertTrue(result["success"])
            self.assertTrue(result.get("mock"))
        provider_post.assert_not_called()
        vtung_request.assert_not_called()
        mono_post.assert_not_called()


@override_settings(
    WEMA={"SIMULATION": False},
    **_STAGED,
)
class LiveSelectorRegressionTests(SimpleTestCase):
    def test_staged_keys_become_live_again_when_simulation_is_off(self):
        self.assertTrue(providers._prembly_live())
        self.assertTrue(providers._card_issuer_live())
        self.assertTrue(providers.fincra_live())
        self.assertTrue(vtung._live())
        self.assertFalse(mono.mono_simulation())
        self.assertTrue(mono.mono_live())
