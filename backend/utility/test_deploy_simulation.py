"""The deploy-wide simulation switch must beat every staged live credential.

Without this invariant KYC/FX/cards/bank-linking can call their live provider,
while the UI still says the deployment is simulated.
"""
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings

from . import mono, providers


_STAGED = {
    "PREMBLY": {
        "API_KEY": "prembly-live-key",
        "APP_ID": "prembly-app",
        "BASE_URL": "https://prembly.invalid",
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
        "LIVE_ENABLED": True,
    },
    "MONO": {
        "SECRET_KEY": "mono-live-key",
        "BASE_URL": "https://mono.invalid",
        "SIMULATION": False,
    },
}




@override_settings(DEBUG=False, TESTING=False, WEMA={"SIMULATION": False})
class MonoOwnSimulationSwitchTests(SimpleTestCase):
    """MONO_SIMULATION must beat a staged MONO_SECRET_KEY on its own.

    mono_live() used to read only the key, so a deploy with the key present and
    MONO_SIMULATION on made LIVE calls — DirectPay among them, which PULLS REAL MONEY
    out of a customer's own bank — while production_checks was told the deploy was
    simulated and the operator was reading, from the variable's name, that nothing
    could move money.
    """

    _KEYED = {"SECRET_KEY": "mono-live-key", "BASE_URL": "https://mono.invalid"}

    @override_settings(MONO={**_KEYED, "SIMULATION": True})
    def test_a_staged_key_does_not_go_live_while_simulating(self):
        self.assertTrue(mono.mono_simulation())
        self.assertFalse(mono.mono_live())

    @override_settings(MONO={**_KEYED, "SIMULATION": False})
    def test_the_same_key_is_live_with_simulation_off(self):
        self.assertFalse(mono.mono_simulation())
        self.assertTrue(mono.mono_live())

    @override_settings(MONO={**_KEYED, "SIMULATION": True})
    def test_directpay_reaches_no_network_while_simulating(self):
        # The one that matters: a real DirectPay debits the customer's own bank.
        with patch("utility.mono.requests.post") as post:
            mono.initiate_directpay(5000, "ZMONO-SIM-1", email="a@b.com")
        post.assert_not_called()


@override_settings(
    WEMA={"SIMULATION": False},
    **_STAGED,
)
class LiveSelectorRegressionTests(SimpleTestCase):
    def test_staged_keys_become_live_again_when_simulation_is_off(self):
        self.assertTrue(providers._prembly_live())
        self.assertTrue(providers._card_issuer_live())
        self.assertTrue(providers.fincra_live())
        self.assertFalse(mono.mono_simulation())
        self.assertTrue(mono.mono_live())
