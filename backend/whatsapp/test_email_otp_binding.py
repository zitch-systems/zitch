"""Email OTP proof stays bound to the inbox that received the challenge."""
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from whatsapp.flows import FLOW_ID_STATE, _email_code_screen
from whatsapp.models import PendingAction
from whatsapp.router import FLOW_TTL, _flow_deadline, _kyc_mail_code, kyc_flow_email_code
from whatsapp.tests import make_user


MSISDN = "2348011117711"


class EmailOtpBindingTests(TestCase):
    def setUp(self):
        self.user, _ = make_user(phone="08010007711", email="old@example.com")
        self.user.email_verified = False
        self.user.phone_verified = True
        self.user.bvn_verified = True
        self.user.save(update_fields=["email_verified", "phone_verified", "bvn_verified"])
        self.pa = PendingAction.objects.create(
            user=self.user,
            msisdn=MSISDN,
            action_type="kyc",
            state=FLOW_ID_STATE,
            payload={"id_kind": "email", "id_step": "code"},
            expires_at=timezone.now() + timezone.timedelta(minutes=10),
        )
        with patch("whatsapp.router.secrets.randbelow", return_value=123456), \
             patch("whatsapp.router.send_email", return_value={"success": True}):
            self.assertTrue(_kyc_mail_code(self.pa, self.user))
        self.pa.save(update_fields=["payload"])

    def test_code_cannot_verify_an_address_it_was_not_sent_to(self):
        self.user.email = "new@example.com"
        self.user.save(update_fields=["email"])

        with patch("whatsapp.router.reply") as reply:
            status, message = kyc_flow_email_code(self.pa, "123456")

        self.user.refresh_from_db()
        self.assertEqual(status, "stop")
        self.assertIn("changed", message.lower())
        self.assertFalse(self.user.email_verified)
        self.assertFalse(PendingAction.objects.filter(pk=self.pa.pk).exists())
        self.assertIn("verify the current address", reply.call_args.args[1])

    def test_attempts_use_fresh_locked_state_instead_of_a_stale_flow_object(self):
        first = PendingAction.objects.get(pk=self.pa.pk)
        stale_second = PendingAction.objects.get(pk=self.pa.pk)

        self.assertEqual(kyc_flow_email_code(first, "000000")[0], "retry")
        self.assertEqual(kyc_flow_email_code(stale_second, "000000")[0], "retry")

        self.pa.refresh_from_db()
        self.assertEqual(self.pa.payload["code_attempts"], 2)

    def test_success_consumes_the_bound_code_and_verifies_the_address(self):
        with patch("whatsapp.router.reply"):
            status, _ = kyc_flow_email_code(self.pa, "123456")

        self.user.refresh_from_db()
        self.assertEqual(status, "ok")
        self.assertTrue(self.user.email_verified)
        self.assertFalse(PendingAction.objects.filter(pk=self.pa.pk).exists())

    def test_code_screen_uses_the_challenge_destination_not_a_later_address(self):
        self.user.email = "new@example.com"
        self.user.save(update_fields=["email"])
        self.pa.refresh_from_db()

        screen = _email_code_screen(self.pa)

        self.assertIn("o•••@example.com", screen["data"]["summary"])
        self.assertNotIn("new@example.com", screen["data"]["summary"])
        self.assertNotIn("old@example.com", str(self.pa.payload))

    def test_action_outlives_the_ten_minute_code_it_promises(self):
        code_exp = timezone.datetime.fromisoformat(self.pa.payload["code_exp"])

        deadline = _flow_deadline(FLOW_ID_STATE, self.pa.payload)

        self.assertGreater(deadline, code_exp)
        self.assertGreater(deadline, timezone.now() + FLOW_TTL)
