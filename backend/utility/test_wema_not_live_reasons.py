"""The scheduled controls must say WHY the bank rail is not live.

Production showed the API reporting the rail as configured while every
scheduled control logged "Wema not live (simulation/mock)". Simulation, a
missing channel id and a missing wallet key all produced that one line, so a
cron whose environment had drifted from the API's could not be told apart from
a deliberate simulation deploy. These tests pin that each cause is named, and
that no configured value ever reaches the output.
"""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.conf import settings

from utility import wema

CHANNEL = "chan-value-should-never-print"
WALLET = "wallet-value-should-never-print"


def _wema(**over):
    keys = {**(settings.WEMA.get("KEYS") or {}), "wallet": over.pop("wallet", WALLET)}
    return {**settings.WEMA, "CHANNEL_ID": over.pop("channel", CHANNEL),
            "SIMULATION": over.pop("simulation", False), "KEYS": keys, **over}


class NotLiveReasonsTests(TestCase):

    def test_live_configuration_has_no_reasons(self):
        with override_settings(WEMA=_wema()):
            self.assertTrue(wema.wema_live())
            self.assertEqual(wema.not_live_reasons(), [])

    def test_each_cause_is_named(self):
        cases = [
            ({"channel": ""}, ["channel_id_missing"]),
            ({"wallet": ""}, ["wallet_key_missing"]),
            ({"channel": "", "wallet": ""}, ["channel_id_missing", "wallet_key_missing"]),
        ]
        for over, expected in cases:
            with self.subTest(**over), override_settings(WEMA=_wema(**over)):
                self.assertFalse(wema.wema_live())
                self.assertEqual(wema.not_live_reasons(), expected)

    def test_simulation_is_named(self):
        with override_settings(WEMA=_wema(simulation=True)):
            self.assertFalse(wema.wema_live())
            self.assertIn("simulation_on", wema.not_live_reasons())

    def test_both_controls_print_the_cause_and_no_value(self):
        for command in ("reconcile_balances", "settlement_report"):
            with self.subTest(command=command), override_settings(WEMA=_wema(wallet="")):
                out = StringIO()
                try:
                    call_command(command, stdout=out, stderr=StringIO())
                except SystemExit:
                    pass
                text = out.getvalue()
                self.assertIn("wallet_key_missing", text)
                self.assertNotIn("simulation/mock", text)
                self.assertNotIn(CHANNEL, text)
