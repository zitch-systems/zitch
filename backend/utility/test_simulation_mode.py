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

from utility.providers import issue_card, mock_disabled_in_prod, simulation_mode

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
