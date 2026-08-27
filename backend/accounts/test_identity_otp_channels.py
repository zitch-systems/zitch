"""Where the BVN/NIN ownership code is allowed to go.

The lookup proves someone knows a name. The code proves they hold the identity —
but only because it goes to contacts on the RECORD. The Zitch account's own email
would prove nothing: whoever is signed in reads that inbox, so mailing the code
there would let any logged-in user claim any identity whose holder's name matches
theirs, and then open a NUBAN against it. The first test here is that rule; the
rest cover the second channel actually earning its place when SMS cannot deliver.
"""
import json
from unittest.mock import patch

from django.test import TestCase, override_settings

from accounts.models import User
from accounts.views import _start_identity_ownership_challenge

RECORD_PHONE = "2348031234567"
RECORD_EMAIL = "holder@record.example"
ACCOUNT_EMAIL = "whoever-is-logged-in@example.com"

# A live provider result: not mock, so the record's own contacts are the only
# ones in play.
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

    def _run(self, result=None, sms=True, email=True, sms_rail=True, email_rail=True):
        with patch(SMS_LIVE, return_value=sms_rail), \
             patch(EMAIL_LIVE, return_value=email_rail), \
             patch(SEND_SMS, return_value={"success": sms}) as send_sms, \
             patch(SEND_EMAIL, return_value={"success": email}) as send_email:
            response = _start_identity_ownership_challenge(
                self.user, "bvn", "22222222222", result or LIVE)
        return response, send_sms, send_email

    def test_the_code_goes_to_the_record_email_never_the_account_email(self):
        # The property the whole challenge rests on. If this ever flips, a
        # logged-in user can verify a stranger's BVN from their own inbox.
        _, send_sms, send_email = self._run()
        self.assertEqual(send_email.call_args[0][0], RECORD_EMAIL)
        self.assertNotEqual(send_email.call_args[0][0], ACCOUNT_EMAIL)
        self.assertEqual(send_sms.call_args[0][0], RECORD_PHONE)

    def test_the_same_code_goes_to_both_channels(self):
        # Two deliveries of ONE code, not two codes: the confirm step checks a
        # single hash, so a second code would silently invalidate the first.
        _, send_sms, send_email = self._run()
        self.assertIn(send_sms.call_args[0][1][:40], send_email.call_args[0][2])

    def test_a_record_with_no_email_is_sms_only_and_does_not_fall_back(self):
        # The dangerous fallback is the tempting one. No email on the record must
        # mean no email at all — never the account's address.
        _, send_sms, send_email = self._run({"success": True, "phone": RECORD_PHONE})
        self.assertTrue(send_sms.called)
        self.assertFalse(send_email.called)

    def test_a_dead_sms_rail_no_longer_blocks_a_record_that_has_an_email(self):
        # The reason for the second channel: Nigerian SMS routing drops OTPs for
        # reasons no retry can fix, and the code still reached the holder.
        response, send_sms, send_email = self._run(sms_rail=False)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(send_sms.called)
        self.assertTrue(send_email.called)

    def test_a_rejected_sms_still_succeeds_when_the_email_landed(self):
        response, _, send_email = self._run(sms=False)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(send_email.called)

    def test_it_fails_only_when_no_channel_delivered(self):
        response, _, _ = self._run(sms=False, email=False)
        self.assertEqual(response.status_code, 503)

    def test_a_dead_sms_rail_with_no_record_email_still_refuses(self):
        response, _, _ = self._run({"success": True, "phone": RECORD_PHONE},
                                   sms_rail=False)
        self.assertEqual(response.status_code, 503)

    def test_the_reply_names_only_the_channels_that_took_the_code(self):
        # Sending a customer to watch a handset that will never buzz is its own
        # small failure.
        response, _, _ = self._run(sms=False)
        delivery = json.loads(response.content)["delivery"]
        self.assertNotIn("registered phone", delivery)
        self.assertIn("BVN record", delivery)

    def test_the_record_email_is_never_echoed_back_to_the_caller(self):
        # The address belongs to the identity's owner, who may not be the person
        # asking — so naming the channel is fine, showing it is not.
        response, _, _ = self._run()
        self.assertNotIn(RECORD_EMAIL, response.content.decode())
