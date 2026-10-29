"""Tests for the go-live preflight command.

Hard gates (Wema keys, securityInfo, live host) fail the run with exit 1; a
fully-configured environment reports GO. Soft checks (email, SMS,
cards) only fail the run under --strict.
"""
import os
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import Client, TestCase, override_settings

from utility import wema

_LIVE_DIAG = {"base_url": "https://api.alat.ng", "channel_id_set": True,
              "wallet_key_set": True, "security_info_set": True, "wema_live": True,
              "simulation": False, "status": "configured", "hint": "",
              "product_keys_set": {"wallet": True, "airtime": True}}
_EMAIL_OK = {"ok": True, "config": {"sender_domain": "zitch.ng"}}
_EMAIL_WARN = {"ok": False, "hint": "sender domain not verified"}
# Legacy local names retained so the individual test contexts below stay compact;
# they now represent the email soft-check, not the retired VTU provider.
_VTU_OK = _EMAIL_OK
_VTU_EMPTY = _EMAIL_WARN
_SAFE_CALLBACKS = {
    "CALLBACK_TOKEN": "x" * 40, "CALLBACK_TOKEN_PREV": "",
    "CALLBACK_ENFORCE_IPS": True, "CALLBACK_IPS": ["135.236.18.76"],
    "AUTH_REQUIRE_SECURITY_INFO": True,
}

_PROBE = "utility.providers.email_probe"
_DIAG = "utility.wema.wema_diagnostics"


def _run(*args):
    out = StringIO()
    code = 0
    try:
        call_command("wema_preflight", *args, stdout=out)
    except SystemExit as exc:
        code = exc.code
    return out.getvalue(), code








@override_settings(
    CARD_PROVIDER="issuer",
    RESEND={"API_KEY": "re_x", "FROM_EMAIL": "x"},
    TERMII={"API_KEY": "tk_x"},
    WEMA=_SAFE_CALLBACKS,
)
class CardIssuerPreflightTests(TestCase):
    def run_with_issuer(self, issuer):
        with override_settings(CARD_ISSUER=issuer), \
                mock.patch(_DIAG, return_value=_LIVE_DIAG), \
                mock.patch(_PROBE, return_value=_VTU_OK):
            return _run()

    def test_staged_key_with_disabled_gate_warns(self):
        out, _code = self.run_with_issuer({
            "API_KEY": "issuer-key",
            "BASE_URL": "https://issuer.invalid",
            "LIVE_ENABLED": False,
        })
        self.assertIn("Card issuer", out)
        self.assertIn("not live", out)

    def test_fully_enabled_issuer_passes(self):
        out, _code = self.run_with_issuer({
            "API_KEY": "issuer-key",
            "BASE_URL": "https://issuer.invalid",
            "LIVE_ENABLED": True,
        })
        self.assertIn("Card issuer", out)
        self.assertIn("live gate, key and base URL configured", out)






@override_settings(RESEND={"API_KEY": "re_x", "FROM_EMAIL": "x"},
                   TERMII={"API_KEY": "tk_x"}, CARD_ISSUER={"API_KEY": "ci_x"},
                   WEMA=_SAFE_CALLBACKS)
class PreflightTotpKeyTests(TestCase):
    """Operator MFA secrets fall back to DJANGO_SECRET_KEY when no dedicated key
    is set — the same fallback the securityInfo check refuses, with a worse
    failure (every enrolled operator locked out of the portal, unrecoverably).
    Reported as a WARN, not a gate: the fix is to set a key before enrolment, and
    failing an existing deployment would not undo the binding."""

    @override_settings(TOTP_ENCRYPTION_KEYS=[])
    def test_unset_totp_key_is_reported(self):
        with mock.patch(_DIAG, return_value=dict(_LIVE_DIAG)), \
             mock.patch(_PROBE, return_value=_VTU_OK):
            out, _ = _run()
        self.assertIn("TOTP_ENCRYPTION_KEYS is unset", out)

    @override_settings(TOTP_ENCRYPTION_KEYS=["a-dedicated-operator-totp-key-value"])
    def test_a_dedicated_key_passes(self):
        with mock.patch(_DIAG, return_value=dict(_LIVE_DIAG)), \
             mock.patch(_PROBE, return_value=_VTU_OK):
            out, _ = _run()
        self.assertNotIn("TOTP_ENCRYPTION_KEYS is unset", out)
        self.assertIn("independent of DJANGO_SECRET_KEY", out)

    @override_settings(TOTP_ENCRYPTION_KEYS=[])
    def test_it_does_not_block_go_live_on_its_own(self):
        with mock.patch(_DIAG, return_value=dict(_LIVE_DIAG)), \
             mock.patch(_PROBE, return_value=_VTU_OK):
            _, code = _run("--launch-scope", "core")
        self.assertEqual(code, 0)
