"""Tests for the go-live preflight command.

Hard gates (Wema keys, securityInfo, live host) fail the run with exit 1; a
fully-configured environment reports GO. Soft checks (VTU balance, email, SMS,
cards) only fail the run under --strict.
"""
import os
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import Client, TestCase, override_settings

_LIVE_DIAG = {"base_url": "https://api.alat.ng", "channel_id_set": True,
              "wallet_key_set": True, "security_info_set": True, "wema_live": True,
              "simulation": False, "status": "configured", "hint": ""}
_VTU_OK = {"config": {"live": True, "api_key_set": True},
           "auth": {"ok": True}, "balance": {"ok": True, "balance": "15000.00"}}
_VTU_EMPTY = {"config": {"live": True, "api_key_set": True}, "auth": {"ok": True},
              "balance": {"ok": True, "balance": "0.00", "hint": "empty"}}
_SAFE_CALLBACKS = {
    "CALLBACK_TOKEN": "x" * 40, "CALLBACK_TOKEN_PREV": "",
    "CALLBACK_ENFORCE_IPS": True, "CALLBACK_IPS": ["135.236.18.76"],
    "AUTH_REQUIRE_SECURITY_INFO": True,
}

_PROBE = "utility.management.commands.wema_preflight.vtu_probe"
_DIAG = "utility.wema.wema_diagnostics"


def _run(*args):
    out = StringIO()
    code = 0
    try:
        call_command("wema_preflight", *args, stdout=out)
    except SystemExit as exc:
        code = exc.code
    return out.getvalue(), code


@override_settings(RESEND={"API_KEY": "re_x", "FROM_EMAIL": "x"},
                   TERMII={"API_KEY": "tk_x"}, CARD_ISSUER={"API_KEY": "ci_x"},
                   WEMA=_SAFE_CALLBACKS)
class PreflightGoTests(TestCase):
    def test_all_pass_is_go(self):
        with mock.patch(_DIAG, return_value=_LIVE_DIAG), mock.patch(_PROBE, return_value=_VTU_OK):
            out, code = _run()
        self.assertIn("RESULT: GO", out)
        # Asserted line by line because "RESULT: GO" is also a prefix of the degraded
        # "GO for money rails — N soft warning(s)" summary: a soft check silently
        # flipping to WARN would not move it. The SMS rail is named because the class
        # keys it above, and an override for a setting that no longer exists is a
        # no-op Django does not complain about.
        self.assertIn("SMS (termii): keyed", out)
        self.assertNotIn("NOT READY", out)
        self.assertEqual(code, 0)

    def test_soft_warn_alone_is_go_without_strict(self):
        with mock.patch(_DIAG, return_value=_LIVE_DIAG), mock.patch(_PROBE, return_value=_VTU_EMPTY):
            out, code = _run()
        self.assertIn("GO for money rails", out)
        self.assertEqual(code, 0)

    def test_strict_fails_on_soft_warn(self):
        with mock.patch(_DIAG, return_value=_LIVE_DIAG), mock.patch(_PROBE, return_value=_VTU_EMPTY):
            out, code = _run("--strict")
        self.assertIn("NOT READY (strict)", out)
        self.assertEqual(code, 1)


@override_settings(WEMA=_SAFE_CALLBACKS)
class PreflightGateTests(TestCase):
    def test_missing_security_info_blocks_go_live(self):
        diag = dict(_LIVE_DIAG, security_info_set=False)
        with mock.patch(_DIAG, return_value=diag), mock.patch(_PROBE, return_value=_VTU_OK):
            out, code = _run()
        self.assertIn("NOT READY", out)
        self.assertIn("securityInfo", out)
        self.assertEqual(code, 1)

    def test_short_security_info_blocks_go_live(self):
        diag = dict(_LIVE_DIAG, security_info_strong=False)
        with mock.patch(_DIAG, return_value=diag), mock.patch(_PROBE, return_value=_VTU_OK):
            out, code = _run()
        self.assertIn("at least 32 characters", out)
        self.assertEqual(code, 1)

    @override_settings(VAS_PROVIDER="wema")
    def test_explicit_wema_vas_requires_airtime_product_key(self):
        diag = dict(_LIVE_DIAG, product_keys_set={"wallet": True, "airtime": False})
        with mock.patch(_DIAG, return_value=diag), mock.patch(_PROBE, return_value=_VTU_OK):
            out, code = _run()
        self.assertIn("WEMA_AIRTIME_KEY", out)
        self.assertEqual(code, 1)

    @override_settings(CARD_PROVIDER="wema", WEMA={**_SAFE_CALLBACKS, "CARD_PRODUCT_KEY": ""})
    def test_wema_cards_require_subscription_and_product_id(self):
        diag = dict(_LIVE_DIAG, product_keys_set={"wallet": True, "card": True})
        with mock.patch(_DIAG, return_value=diag), mock.patch(_PROBE, return_value=_VTU_OK):
            out, code = _run()
        self.assertIn("WEMA_CARD_PRODUCT_KEY", out)
        self.assertEqual(code, 1)

    @override_settings(WEMA={**_SAFE_CALLBACKS, "CALLBACK_ENFORCE_IPS": False})
    def test_callback_ip_allowlist_is_a_hard_gate(self):
        with mock.patch(_DIAG, return_value=_LIVE_DIAG), mock.patch(_PROBE, return_value=_VTU_OK):
            out, code = _run()
        self.assertIn("Callback source IP allowlist", out)
        self.assertEqual(code, 1)

    def test_sandbox_host_blocks(self):
        diag = dict(_LIVE_DIAG, base_url="https://apiplayground.alat.ng")
        with mock.patch(_DIAG, return_value=diag), mock.patch(_PROBE, return_value=_VTU_OK):
            out, code = _run()
        self.assertIn("NOT READY", out)
        self.assertIn("sandbox", out)
        self.assertEqual(code, 1)

    def test_unkeyed_env_blocks(self):
        diag = {"base_url": "https://apiplayground.alat.ng", "channel_id_set": False,
                "wallet_key_set": False, "security_info_set": False, "wema_live": False,
                "simulation": False, "status": "keys_incomplete", "hint": "set keys"}
        with mock.patch(_DIAG, return_value=diag), \
             mock.patch(_PROBE, return_value={"config": {"live": False}}):
            out, code = _run()
        self.assertIn("NOT READY", out)
        self.assertEqual(code, 1)


class PreflightOverHttpTests(TestCase):
    """The go-live gate was reachable only from a shell, and the deploys that most
    need it are the ones without one (Render's free tier has none). Same command,
    same wording, same exit status — over the diagnostic bearer token."""

    def setUp(self):
        self.client = Client()
        self._patch = mock.patch.dict(os.environ, {"DIAG_TOKEN": "diag-token"})
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def _get(self, qs="", token="diag-token"):
        return self.client.get(f"/preflight{qs}", HTTP_AUTHORIZATION=f"Bearer {token}")

    def test_requires_the_diagnostic_token(self):
        self.assertEqual(self.client.get("/preflight").status_code, 403)
        self.assertEqual(self._get(token="wrong").status_code, 403)

    def test_a_failing_gate_answers_503_with_the_report(self):
        # SystemExit(1) is how the command says NOT READY; it must not read as a crash.
        with mock.patch(_DIAG, return_value=dict(_LIVE_DIAG, wema_live=False)), \
             mock.patch(_PROBE, return_value=_VTU_OK):
            res = self._get()
        self.assertEqual(res.status_code, 503)
        body = res.json()["preflight"]
        self.assertFalse(body["ready"])
        self.assertTrue(any("NOT READY" in line for line in body["report"]))
        self.assertNotIn("error", body)          # a failing gate is not an error

    def test_strict_is_passed_through(self):
        with mock.patch(_DIAG, return_value=_LIVE_DIAG), \
             mock.patch(_PROBE, return_value=_VTU_EMPTY):
            strict = self._get("?strict=1")
        self.assertTrue(strict.json()["preflight"]["strict"])
        self.assertFalse(strict.json()["preflight"]["ready"])

    def test_a_broken_preflight_is_not_a_passing_preflight(self):
        with mock.patch(_DIAG, side_effect=RuntimeError("boom")):
            res = self._get()
        self.assertEqual(res.status_code, 503)
        body = res.json()["preflight"]
        self.assertFalse(body["ready"])
        self.assertIn("RuntimeError", body["error"])
