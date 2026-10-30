"""The deploy-wide simulation switch.

WEMA_SIMULATION un-blocks provider MOCK paths across the WHOLE stack (partner-bank
airtime/data/bills, cards, FX, Mono, Wema, KYC) so the app can be walked end-to-end
with no real money — and wema_preflight HARD-FAILS while it is on, so it can never
reach a real-money deploy.
"""
import json
from io import StringIO
from unittest import mock

from django.core.management import CommandError, call_command
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
    def test_retired_simulation_cannot_unblock_mocks_in_prod(self):
        self.assertFalse(simulation_mode())
        self.assertTrue(mock_disabled_in_prod())

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
    def test_retired_simulation_cannot_enable_card_mock(self):
        res = issue_card("Ada Test", "cust_1")
        self.assertFalse(res["success"])
        self.assertNotIn("card_token", res)




class PreflightSimulationGateTests(TestCase):
    @override_settings(MONO={"SIMULATION": True})
    def test_preflight_blocks_go_live_while_simulation_on(self):
        out = StringIO()
        with self.assertRaises(CommandError):
            call_command("bank_preflight", stdout=out)
        self.assertIn("simulation_mode_enabled", json.loads(out.getvalue())["blockers"])
