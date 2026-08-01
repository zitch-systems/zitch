"""Tests for the TEST-ONLY OTP bypass (settings.TEST_OTP).

When TEST_OTP_PHONE + TEST_OTP_CODE are set, that one number accepts that fixed
code for signup only; password recovery and every other number are unaffected. wema_preflight
HARD-FAILS while it is set, so the bypass can never reach go-live.
"""
import json
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import Client, TestCase, override_settings

from accounts.models import OTP

_TEST = {"PHONE": "08160000001", "CODE": "246813"}
_OFF = {"PHONE": "", "CODE": ""}


class VerifyCodeBypassTests(TestCase):
    @override_settings(TEST_OTP=_TEST)
    def test_whitelisted_phone_accepts_fixed_code(self):
        otp = OTP.issue(phone="08160000001", code="999999")
        self.assertTrue(otp.verify_code("246813"))   # fixed test code accepted
        self.assertTrue(otp.verify_code("999999"))   # the real issued code still works

    @override_settings(TEST_OTP=_TEST)
    def test_whitelisted_phone_rejects_wrong_code(self):
        otp = OTP.issue(phone="08160000001", code="999999")
        self.assertFalse(otp.verify_code("000000"))

    @override_settings(TEST_OTP=_TEST)
    def test_other_phone_not_bypassed(self):
        otp = OTP.issue(phone="08160000002", code="999999")
        self.assertFalse(otp.verify_code("246813"))  # fixed code only for the whitelisted number
        self.assertTrue(otp.verify_code("999999"))

    @override_settings(TEST_OTP=_TEST)
    def test_fixed_code_never_bypasses_password_reset(self):
        otp = OTP.issue(phone="08160000001", code="999999", purpose=OTP.RESET)
        self.assertFalse(otp.verify_code("246813"))
        self.assertTrue(otp.verify_code("999999"))

    @override_settings(TEST_OTP=_OFF)
    def test_inert_when_unset(self):
        otp = OTP.issue(phone="08160000001", code="999999")
        self.assertFalse(otp.verify_code("246813"))
        self.assertTrue(otp.verify_code("999999"))


@override_settings(TEST_OTP=_TEST)
class SignupBypassFlowTests(TestCase):
    def setUp(self):
        self.client = Client()

    def _post(self, path, payload):
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    def test_full_signup_with_fixed_code(self):
        res, _ = self._post("/api/phone_verification/",
                            {"phone": "08160000001", "email": "t@zitch.test"})
        self.assertEqual(res.status_code, 200)
        # Verify with the FIXED test code, not the random one that was issued.
        res2, body = self._post("/api/verify_otp/", {"phone": "08160000001", "otp": "246813"})
        self.assertEqual(res2.status_code, 200)
        self.assertIn("access_token", body)


_LIVE_DIAG = {"base_url": "https://api.alat.ng", "channel_id_set": True,
              "wallet_key_set": True, "security_info_set": True, "wema_live": True,
              "simulation": False, "status": "configured", "hint": ""}
_VTU_OK = {"config": {"live": True, "api_key_set": True},
           "auth": {"ok": True}, "balance": {"ok": True, "balance": "15000.00"}}


class PreflightTestOtpGateTests(TestCase):
    @override_settings(TEST_OTP=_TEST, RESEND={"API_KEY": "re_x", "FROM_EMAIL": "x"},
                       SENDCHAMP={"API_KEY": "sc_x", "BASE_URL": "x", "SENDER_NAME": "Zitch"},
                       CARD_ISSUER={"API_KEY": "ci_x"})
    def test_preflight_hard_fails_while_bypass_on(self):
        out = StringIO()
        code = 0
        with mock.patch("utility.wema.wema_diagnostics", return_value=_LIVE_DIAG), \
             mock.patch("utility.management.commands.wema_preflight.vtu_probe", return_value=_VTU_OK):
            try:
                call_command("wema_preflight", stdout=out)
            except SystemExit as exc:
                code = exc.code
        self.assertIn("NOT READY", out.getvalue())
        self.assertIn("Test-OTP", out.getvalue())
        self.assertEqual(code, 1)
