"""Where the BVN/NIN ownership code is allowed to go.

Wema Wallet Service does not support email delivery for BVN/NIN OTP. The code
must go only to the phone on the identity record. Resend remains for Zitch-owned
email verification and statements, not as a parallel bank OTP channel.
"""
import json
from unittest.mock import patch

from django.test import TestCase, override_settings

from accounts.models import User
from accounts.views import _start_identity_ownership_challenge

RECORD_PHONE = "2348031234567"
RECORD_EMAIL = "holder@record.example"
ACCOUNT_EMAIL = "whoever-is-logged-in@example.com"

LIVE = {"success": True, "phone": RECORD_PHONE, "email": RECORD_EMAIL}

SEND_SMS = "accounts.views.send_sms"
SEND_EMAIL = "accounts.views.send_email"
SMS_LIVE = "accounts.views.sms_live"
EMAIL_LIVE = "accounts.views.email_live"


@override_settings(DEBUG=False, TESTING=False)
class IdentityOtpDestinationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="u1", phone="08019999999", email=ACCOUNT_EMAIL,
            password="Str0ng!pass1")

    def _run(self, result=None, sms=True, sms_rail=True, email_rail=True):
        with patch(SMS_LIVE, return_value=sms_rail), \
             patch(EMAIL_LIVE, return_value=email_rail), \
             patch(SEND_SMS, return_value={"success": sms}) as send_sms, \
             patch(SEND_EMAIL, return_value={"success": True}) as send_email:
            response = _start_identity_ownership_challenge(
                self.user, "bvn", "22222222222", result or LIVE)
        return response, send_sms, send_email

    def test_the_code_goes_to_the_record_phone_only(self):
        response, send_sms, send_email = self._run()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(send_sms.call_args[0][0], RECORD_PHONE)
        send_email.assert_not_called()

    def test_record_email_is_ignored_for_wema_identity_otp(self):
        response, _, send_email = self._run()
        body = response.content.decode()
        self.assertEqual(response.status_code, 200)
        send_email.assert_not_called()
        self.assertNotIn(RECORD_EMAIL, body)
        self.assertNotIn("record.example", body)
        self.assertNotIn("email", body.lower())

    def test_account_email_is_never_a_fallback(self):
        response, _, send_email = self._run({"success": True, "phone": RECORD_PHONE})
        self.assertEqual(response.status_code, 200)
        send_email.assert_not_called()
        self.assertNotIn(ACCOUNT_EMAIL, response.content.decode())

    def test_a_record_with_no_phone_is_sent_for_review(self):
        response, send_sms, send_email = self._run({"success": True, "email": RECORD_EMAIL})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(json.loads(response.content)["code"], "identity_phone_unavailable")
        send_sms.assert_not_called()
        send_email.assert_not_called()

    def test_a_dead_sms_rail_refuses_even_when_record_has_email(self):
        response, send_sms, send_email = self._run(sms_rail=False, email_rail=True)
        self.assertEqual(response.status_code, 503)
        send_sms.assert_not_called()
        send_email.assert_not_called()

    def test_a_rejected_sms_refuses_even_when_record_has_email(self):
        response, send_sms, send_email = self._run(sms=False)
        self.assertEqual(response.status_code, 503)
        self.assertTrue(send_sms.called)
        send_email.assert_not_called()

    def test_the_reply_names_the_masked_phone_only(self):
        response, _, _ = self._run()
        delivery = json.loads(response.content)["delivery"]
        self.assertEqual(delivery, "registered phone •••••4567")
        self.assertNotIn(RECORD_PHONE, response.content.decode())

    def test_mock_provider_results_are_not_treated_as_live_delivery(self):
        result = {"success": True, "mock": True, "email": RECORD_EMAIL}
        response, send_sms, send_email = self._run(result)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(json.loads(response.content)["code"], "identity_phone_unavailable")
        send_sms.assert_not_called()
        send_email.assert_not_called()
