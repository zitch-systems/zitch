"""The deploy-wide simulation switch.

WEMA_SIMULATION un-blocks provider MOCK paths across the WHOLE stack (VTU.ng
airtime/data/bills, cards, FX, Mono, Wema, KYC) so the app can be walked end-to-end
with no real money — and wema_preflight HARD-FAILS while it is on, so it can never
reach a real-money deploy.
"""
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

from utility.providers import (
    issue_card,
    kyc_verify_address,
    kyc_verify_face,
    kyc_verify_id_document,
    kyc_verify_nin_document,
    mock_disabled_in_prod,
    simulation_mode,
)

_SIM = {"SIMULATION": True}
_LIVE = {"SIMULATION": False}


class MockGateTests(SimpleTestCase):
    @override_settings(DEBUG=False, TESTING=False, WEMA=_LIVE)
    def test_mocks_fail_closed_in_prod_when_not_simulating(self):
        self.assertFalse(simulation_mode())
        self.assertTrue(mock_disabled_in_prod())

    @override_settings(DEBUG=False, TESTING=False, WEMA=_SIM)
    def test_simulation_unblocks_mocks_even_in_prod(self):
        self.assertTrue(simulation_mode())
        self.assertFalse(mock_disabled_in_prod())

    @override_settings(DEBUG=True, TESTING=False, WEMA=_LIVE)
    def test_debug_allows_mocks(self):
        self.assertFalse(mock_disabled_in_prod())


class ProviderUnblockTests(SimpleTestCase):
    """issue_card stands in for every provider that gates on mock_disabled_in_prod
    (VTU airtime/data/bills, cards, FX): fails closed in live prod, mock-passes in sim."""

    @override_settings(DEBUG=False, TESTING=False, WEMA=_LIVE,
                       CARD_ISSUER={"API_KEY": "", "BRAND": "Verve"})
    def test_card_fails_closed_in_live_prod(self):
        self.assertFalse(issue_card("Ada Test", "cust_1")["success"])

    @override_settings(DEBUG=False, TESTING=False, WEMA=_SIM,
                       CARD_ISSUER={"API_KEY": "", "BRAND": "Verve"})
    def test_card_mock_passes_in_simulation(self):
        res = issue_card("Ada Test", "cust_1")
        self.assertTrue(res["success"] and res.get("mock"))
        self.assertTrue(res["card_token"])


class KycProviderFailClosedTests(SimpleTestCase):
    """Missing biometric/document KYC credentials can never lift a live tier."""

    PREMBLY_OFF = {"BASE_URL": "https://api.prembly.com", "API_KEY": "", "APP_ID": ""}

    @override_settings(DEBUG=False, TESTING=False, WEMA=_LIVE, PREMBLY=PREMBLY_OFF)
    def test_every_kyc_evidence_check_fails_closed_in_live_production(self):
        results = (
            kyc_verify_face("image"),
            kyc_verify_address("1 Main Street", "document"),
            kyc_verify_nin_document("document"),
            kyc_verify_id_document("document", "passport"),
        )
        self.assertTrue(all(not result["success"] for result in results))
        self.assertTrue(all(not result.get("mock") for result in results))

    @override_settings(DEBUG=False, TESTING=False, WEMA=_SIM, PREMBLY=PREMBLY_OFF)
    def test_explicit_simulation_keeps_the_end_to_end_kyc_walkthrough(self):
        results = (
            kyc_verify_face("image"),
            kyc_verify_address("1 Main Street", "document"),
            kyc_verify_nin_document("document"),
            kyc_verify_id_document("document", "passport"),
        )
        self.assertTrue(all(result["success"] and result.get("mock") for result in results))


_LIVE_DIAG = {"base_url": "https://api.alat.ng", "channel_id_set": True,
              "wallet_key_set": True, "security_info_set": True, "wema_live": True,
              "simulation": True, "status": "configured", "hint": ""}
_VTU_OK = {"config": {"live": True, "api_key_set": True},
           "auth": {"ok": True}, "balance": {"ok": True, "balance": "15000.00"}}


class PreflightSimulationGateTests(TestCase):
    @override_settings(WEMA=_SIM, TEST_OTP={"PHONE": "", "CODE": ""}, SIMULATE_DEPOSIT_TOKEN="",
                       RESEND={"API_KEY": "re_x", "FROM_EMAIL": "x"},
                       TERMII={"API_KEY": "tk_x", "BASE_URL": "x", "SENDER_ID": "Zitch",
                               "CHANNEL": "dnd"},
                       CARD_ISSUER={"API_KEY": "ci_x"})
    def test_preflight_hard_fails_while_simulation_on(self):
        out = StringIO()
        code = 0
        with mock.patch("utility.wema.wema_diagnostics", return_value=_LIVE_DIAG), \
             mock.patch("utility.management.commands.wema_preflight.vtu_probe", return_value=_VTU_OK):
            try:
                call_command("wema_preflight", stdout=out)
            except SystemExit as exc:
                code = exc.code
        self.assertIn("NOT READY", out.getvalue())
        self.assertIn("Simulation mode", out.getvalue())
        self.assertEqual(code, 1)


_KEYED_SIM = {"CHANNEL_ID": "chan-1", "KEYS": {"wallet": "subkey", "card": "cardkey",
                                               "airtime": "airkey"},
              "BASE_URL": "https://apiplayground.alat.ng", "SIMULATION": True,
              "SOURCE_ACCOUNT": "0100000001"}
_KEYED_LIVE = {**_KEYED_SIM, "SIMULATION": False}


class SimulationBeatsKeysTests(SimpleTestCase):
    """WEMA_SIMULATION used to be consulted only where an UNKEYED deploy chose between
    a mock and failing closed — so on a keyed deploy it did nothing at all, and every
    call went to WEMA_BASE_URL for real. Pointed at the live host that moves REAL
    MONEY while the operator believes, from the variable's name and the runbook, that
    nothing can."""

    @override_settings(WEMA=_KEYED_SIM)
    def test_keys_present_but_simulating_is_not_live(self):
        from utility import wema

        self.assertTrue(wema.wema_keys_configured())   # the credentials ARE there…
        self.assertFalse(wema.wema_live())             # …and nothing real is sent

    @override_settings(WEMA=_KEYED_LIVE)
    def test_keys_present_without_simulation_is_live(self):
        from utility import wema

        self.assertTrue(wema.wema_live())

    @override_settings(WEMA=_KEYED_SIM)
    def test_a_transfer_is_mocked_rather_than_sent(self):
        # The one that matters: money movement must not reach the gateway.
        from utility import wema

        with mock.patch("utility.wema.requests.post") as post:
            res = wema.transfer(1000, "REF-SIM", "test", source_account="01",
                                destination_account="02", destination_bank_code="035",
                                destination_bank_name="Wema", destination_name="ADA")
        post.assert_not_called()
        self.assertTrue(res["success"])
        self.assertTrue(res["mock"])

    @override_settings(WEMA=_KEYED_SIM)
    def test_vas_and_cards_honour_it_too(self):
        # Airtime spends real money and issuing a card is a real-world side effect;
        # a switch that only covered transfers would be a trap of its own.
        from utility import wema

        self.assertFalse(wema._vas_live("airtime"))
        self.assertFalse(wema._card_live())

    @override_settings(WEMA=_KEYED_SIM)
    def test_diagnostics_distinguish_simulating_from_unconfigured(self):
        # Both report wema_live false and need opposite fixes.
        from utility import wema

        diag = wema.wema_diagnostics()
        self.assertEqual(diag["status"], "simulation")
        self.assertTrue(diag["keys_configured"])
        self.assertIn("WEMA_SIMULATION", diag["hint"])

    @override_settings(WEMA={**_KEYED_SIM, "CHANNEL_ID": "", "KEYS": {}})
    def test_unconfigured_still_says_so(self):
        from utility import wema

        diag = wema.wema_diagnostics()
        self.assertEqual(diag["status"], "simulation")
        self.assertFalse(diag["keys_configured"])
