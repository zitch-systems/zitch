"""Tests for the Sendchamp SMS rail: Nigerian number normalization (so OTPs
actually deliver on the DND route) and the sms_probe delivery self-test."""
from unittest import mock

from django.test import TestCase, override_settings

from utility.providers import _ng_msisdn, send_sms, sms_probe

_SC = {"BASE_URL": "https://api.sendchamp.com/api/v1", "API_KEY": "sc_live_x", "SENDER_NAME": "Zitch"}
_SC_NOKEY = {"BASE_URL": "https://api.sendchamp.com/api/v1", "API_KEY": "", "SENDER_NAME": "Zitch"}


def _ok_response():
    resp = mock.Mock(ok=True)
    resp.json.return_value = {"status": "success", "message": "ok"}
    return resp


class MsisdnTests(TestCase):
    def test_local_zero_prefixed_to_intl(self):
        self.assertEqual(_ng_msisdn("08031110001"), "2348031110001")

    def test_already_intl_unchanged(self):
        self.assertEqual(_ng_msisdn("2348031110001"), "2348031110001")

    def test_plus_and_spaces_stripped(self):
        self.assertEqual(_ng_msisdn("+234 803 111 0001"), "2348031110001")

    def test_ten_digit_without_leading_zero(self):
        self.assertEqual(_ng_msisdn("8031110001"), "2348031110001")


@override_settings(SENDCHAMP=_SC)
class SendSmsTests(TestCase):
    def test_sends_international_number(self):
        with mock.patch("utility.providers.requests.post", return_value=_ok_response()) as post:
            out = send_sms("08031110001", "Your Zitch verification code is 123456")
        self.assertTrue(out["success"])
        # The number reaching Sendchamp must be normalised to 234… for DND delivery.
        self.assertEqual(post.call_args.kwargs["json"]["to"], ["2348031110001"])
        self.assertEqual(post.call_args.kwargs["json"]["route"], "dnd")


class SmsProbeTests(TestCase):
    @override_settings(SENDCHAMP=_SC_NOKEY)
    def test_no_key_reports_unset(self):
        out = sms_probe("08031110001")
        self.assertFalse(out["config"]["api_key_set"])
        self.assertIn("hint", out)
        self.assertNotIn("send", out)

    @override_settings(SENDCHAMP=_SC)
    def test_key_but_no_phone_prompts_for_number(self):
        out = sms_probe("")
        self.assertTrue(out["config"]["api_key_set"])
        self.assertIn("phone", out["hint"].lower())
        self.assertNotIn("send", out)

    @override_settings(SENDCHAMP=_SC)
    def test_key_and_phone_sends_and_reports(self):
        with mock.patch("utility.providers.requests.post", return_value=_ok_response()):
            out = sms_probe("08031110001")
        self.assertTrue(out["send"]["ok"])
        self.assertEqual(out["send"]["to_normalised"], "2348031110001")
