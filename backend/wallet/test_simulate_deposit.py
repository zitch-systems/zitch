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


class SimulateDepositTests(TestCase):
    def setUp(self):
        cache.clear()  # isolate the endpoint's rate-limit counter between tests
        self.client = Client()
        self.user = User.objects.create(username=_PHONE, phone=_PHONE)
        get_or_create_wallet(self.user)

    def _post(self, payload):
        res = self.client.post("/api/dev/simulate-deposit/",
                               data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    @override_settings(WEMA=_SIM_ON, SIMULATE_DEPOSIT_TOKEN="")
    def test_forbidden_when_token_unset(self):
        res, _ = self._post({"token": "anything", "phone": _PHONE, "amount": 5000})
        self.assertEqual(res.status_code, 403)

    @override_settings(WEMA=_SIM_ON, SIMULATE_DEPOSIT_TOKEN=_TOKEN)
    def test_forbidden_on_wrong_token(self):
        res, _ = self._post({"token": "nope", "phone": _PHONE, "amount": 5000})
        self.assertEqual(res.status_code, 403)

    @override_settings(WEMA=_SIM_OFF, SIMULATE_DEPOSIT_TOKEN=_TOKEN)
    def test_not_found_when_simulation_off(self):
        # Even with the right token, a live-money deploy (simulation off) 404s —
        # the endpoint can never fabricate real money.
        res, _ = self._post({"token": _TOKEN, "phone": _PHONE, "amount": 5000})
        self.assertEqual(res.status_code, 404)
        self.assertEqual(get_or_create_wallet(self.user).balance, Decimal("0"))

    @override_settings(WEMA=_SIM_ON, SIMULATE_DEPOSIT_TOKEN=_TOKEN)
    def test_amount_bounds(self):
        res, _ = self._post({"token": _TOKEN, "phone": _PHONE, "amount": 50})
        self.assertEqual(res.status_code, 400)
        res, _ = self._post({"token": _TOKEN, "phone": _PHONE, "amount": 2_000_000})
        self.assertEqual(res.status_code, 400)

    @override_settings(WEMA=_SIM_ON, SIMULATE_DEPOSIT_TOKEN=_TOKEN)
    def test_unknown_phone(self):
        res, _ = self._post({"token": _TOKEN, "phone": "08169999999", "amount": 5000})
        self.assertEqual(res.status_code, 404)

    @override_settings(WEMA=_SIM_ON, SIMULATE_DEPOSIT_TOKEN=_TOKEN)
    def test_happy_path_credits_like_a_real_deposit(self):
        res, body = self._post({"token": _TOKEN, "phone": _PHONE, "amount": 50000})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(Decimal(body["wallet"]), Decimal("50000"))
        wallet = get_or_create_wallet(self.user)
        self.assertEqual(wallet.balance, Decimal("50000"))
        # Ledger row is written exactly like a reconciled Wema deposit: IN /
        # Successful, under the WEMA-CR- reference namespace, and the append-only
        # ledger reconciles to the stored balance.
        txn = Transaction.objects.get(user=self.user, direction=Transaction.IN)
        self.assertEqual(txn.transaction_status, Transaction.SUCCESS)
        self.assertTrue(txn.reference.startswith("WEMA-CR-SIM-"))
        self.assertEqual(wallet_expected_balance(self.user.id), wallet.balance)

    @override_settings(WEMA=_SIM_ON, SIMULATE_DEPOSIT_TOKEN=_TOKEN)
    def test_each_call_is_a_fresh_deposit(self):
        # Distinct references => two credits accumulate (idempotency is per-deposit,
        # matching how real reconciled deposits behave).
        self._post({"token": _TOKEN, "phone": _PHONE, "amount": 1000})
        self._post({"token": _TOKEN, "phone": _PHONE, "amount": 1000})
        self.assertEqual(get_or_create_wallet(self.user).balance, Decimal("2000"))


_LIVE_DIAG = {"base_url": "https://api.alat.ng", "channel_id_set": True,
              "wallet_key_set": True, "security_info_set": True, "wema_live": True,
              "simulation": False, "status": "configured", "hint": ""}
_VTU_OK = {"config": {"live": True, "api_key_set": True},
           "auth": {"ok": True}, "balance": {"ok": True, "balance": "15000.00"}}


class PreflightSimDepositGateTests(TestCase):
    @override_settings(SIMULATE_DEPOSIT_TOKEN=_TOKEN, TEST_OTP={"PHONE": "", "CODE": ""},
                       RESEND={"API_KEY": "re_x", "FROM_EMAIL": "x"},
                       TERMII={"API_KEY": "tk_x", "BASE_URL": "x", "SENDER_ID": "Zitch",
                               "CHANNEL": "dnd"},
                       CARD_ISSUER={"API_KEY": "ci_x"})
    def test_preflight_hard_fails_while_token_set(self):
        out = StringIO()
        code = 0
        with mock.patch("utility.wema.wema_diagnostics", return_value=_LIVE_DIAG), \
             mock.patch("utility.management.commands.wema_preflight.vtu_probe", return_value=_VTU_OK):
            try:
                call_command("wema_preflight", stdout=out)
            except SystemExit as exc:
                code = exc.code
        self.assertIn("NOT READY", out.getvalue())
        self.assertIn("Simulated-deposit", out.getvalue())
        self.assertEqual(code, 1)
