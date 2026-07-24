"""Tests for the go-live preflight command.

Hard gates (Wema keys, securityInfo, live host) fail the run with exit 1; a
fully-configured environment reports GO. Soft checks (VTU balance, email, SMS,
cards) only fail the run under --strict.
"""
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings

_LIVE_DIAG = {"base_url": "https://api.alat.ng", "channel_id_set": True,
              "wallet_key_set": True, "security_info_set": True, "wema_live": True,
              "simulation": False, "status": "configured", "hint": ""}
_VTU_OK = {"config": {"live": True, "api_key_set": True},
           "auth": {"ok": True}, "balance": {"ok": True, "balance": "15000.00"}}
_VTU_EMPTY = {"config": {"live": True, "api_key_set": True}, "auth": {"ok": True},
              "balance": {"ok": True, "balance": "0.00", "hint": "empty"}}

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
                   SENDCHAMP={"API_KEY": "sc_x"}, CARD_ISSUER={"API_KEY": "ci_x"})
class PreflightGoTests(TestCase):
    def test_all_pass_is_go(self):
        with mock.patch(_DIAG, return_value=_LIVE_DIAG), mock.patch(_PROBE, return_value=_VTU_OK):
            out, code = _run()
        self.assertIn("RESULT: GO", out)
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


class PreflightGateTests(TestCase):
    def test_missing_security_info_blocks(self):
        diag = dict(_LIVE_DIAG, security_info_set=False)
        with mock.patch(_DIAG, return_value=diag), mock.patch(_PROBE, return_value=_VTU_OK):
            out, code = _run()
        self.assertIn("NOT READY", out)
        self.assertIn("securityInfo", out)
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
