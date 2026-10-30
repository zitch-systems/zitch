"""Tests for the TEST-ONLY simulated-deposit endpoint (/api/dev/simulate-deposit/).

Gated three ways — WEMA_SIMULATION on, a matching SIMULATE_DEPOSIT_TOKEN, and a
bounded amount for an existing user — so it can load mock money for end-to-end app
testing but can NEVER fabricate real money on a live deploy (simulation off => 404).
wema_preflight HARD-FAILS while the token is set.
"""
import json
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, TestCase, override_settings

from wallet.models import Transaction
from wallet.services import get_or_create_wallet, wallet_expected_balance

User = get_user_model()

_SIM_ON = {"SIMULATION": True}
_SIM_OFF = {"SIMULATION": False}
_TOKEN = "sim-secret-token"
_PHONE = "08160000001"




_LIVE_DIAG = {"base_url": "https://api.alat.ng", "channel_id_set": True,
              "wallet_key_set": True, "security_info_set": True, "wema_live": True,
              "simulation": False, "status": "configured", "hint": ""}


class PreflightSimDepositGateTests(TestCase):
    @override_settings(SIMULATE_DEPOSIT_TOKEN=_TOKEN, TEST_OTP={"PHONE": "", "CODE": ""},
                       RESEND={"API_KEY": "re_x", "FROM_EMAIL": "x"},
                       TERMII={"API_KEY": "tk_x", "BASE_URL": "x", "SENDER_ID": "Zitch",
                               "CHANNEL": "dnd"},
                       CARD_ISSUER={"API_KEY": "ci_x"})
    def test_preflight_hard_fails_while_token_set(self):
        out = StringIO()
        code = 0
        with mock.patch("utility.wema.wema_diagnostics", return_value=_LIVE_DIAG):
            try:
                call_command("wema_preflight", stdout=out)
            except SystemExit as exc:
                code = exc.code
        self.assertIn("NOT READY", out.getvalue())
        self.assertIn("Simulated-deposit", out.getvalue())
        self.assertEqual(code, 1)
