"""WhatsApp: the sign-up address goes to the bank for Tier 3 when Tier 2 is
confirmed (17 Nov 2026).

The customer is not told it was sent, nor when the bank verifies it. Only a
refusal is reported. Tier 3 applies when they tap it.
"""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from .models import PendingAction, WhatsAppLink
from .verification_web import (ACTION_TYPE, FACE_DONE_TEXT, FACE_FAILED_TEXT, FACE_PROCESSING,
                               TIER3_REFUSED_TEXT, _finish_face, send_address_with_tier2)

User = get_user_model()
MSISDN = "2348011113333"


class Tier3WithTier2Tests(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            username="t3", phone="08011113333", phone_verified=True, bvn_verified=True,
            face_verified=True, tier=2, address="12 Allen Avenue, Ikeja, Lagos")
        WhatsAppLink.objects.create(user=self.user, wa_msisdn=MSISDN,
                                    status=WhatsAppLink.ACTIVE, linked_at=timezone.now())
        self.pa = PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type=ACTION_TYPE, state=FACE_PROCESSING,
            payload={}, expires_at=timezone.now() + timedelta(minutes=30))

    def finish(self, outcome, result=(True, "processing", "")):
        with patch("accounts.views.send_tier3_address", return_value=result) as sent, \
             patch("whatsapp.router.reply") as reply:
            _finish_face(self.pa, outcome)
        return sent, [c.args[1] for c in reply.call_args_list]

    def test_a_confirmed_tier_2_sends_the_saved_address_without_a_word(self):
        sent, messages = self.finish("verified")
        sent.assert_called_once()
        _user, house, street, state, lga = sent.call_args.args
        self.assertEqual((house, street, lga, state), ("12", "Allen Avenue", "Ikeja", "Lagos"))
        self.assertEqual(messages, [FACE_DONE_TEXT])        # nothing about the address
        self.assertNotIn("address", FACE_DONE_TEXT.lower())

    def test_a_refusal_on_sending_is_the_one_thing_reported(self):
        _sent, messages = self.finish("verified", result=(True, "not_confirmed", ""))
        self.assertEqual(messages, [FACE_DONE_TEXT, TIER3_REFUSED_TEXT])

    def test_a_failed_face_check_sends_no_address(self):
        sent, messages = self.finish("failed")
        sent.assert_not_called()
        self.assertEqual(messages, [FACE_FAILED_TEXT])

    def test_finishing_twice_sends_once(self):
        self.finish("verified")
        sent, messages = self.finish("verified")
        sent.assert_not_called()
        self.assertEqual(messages, [])

    def test_no_saved_address_or_already_verified_is_skipped(self):
        User.objects.filter(pk=self.user.pk).update(address="")
        with patch("accounts.views.send_tier3_address") as sent:
            self.assertEqual(send_address_with_tier2(self.user, MSISDN), "skipped")
        sent.assert_not_called()
        User.objects.filter(pk=self.user.pk).update(
            address="12 Allen Avenue, Ikeja, Lagos", address_verified=True)
        with patch("accounts.views.send_tier3_address") as sent:
            self.assertEqual(send_address_with_tier2(self.user, MSISDN), "skipped")
        sent.assert_not_called()

    def test_a_bank_error_never_undoes_tier_2(self):
        with patch("accounts.views.send_tier3_address", side_effect=RuntimeError("down")), \
             patch("whatsapp.router.reply") as reply:
            self.assertTrue(_finish_face(self.pa, "verified"))
        self.assertEqual([c.args[1] for c in reply.call_args_list], [FACE_DONE_TEXT])
        self.user.refresh_from_db()
        self.assertEqual(self.user.tier, 2)


class SignupCardButtonTests(TestCase):
    """The chat line under the sign-up card names the button the card shows."""

    def start(self, gated):
        from . import router

        with patch.object(router, "flows_live", return_value=True), \
             patch.object(router, "privacy_screen_live", return_value=gated), \
             patch.object(router, "send_flow", return_value={"success": True}) as flow, \
             patch.object(router, "reply") as reply:
            router._start_onboarding("2348066667777")
        return flow.call_args.kwargs["cta"], reply.call_args.args[1]

    def test_the_privacy_card_says_review_and_continue(self):
        cta, text = self.start(gated=True)
        self.assertEqual(cta, "Review and continue")
        self.assertIn("Tap *Review and continue*", text)
        self.assertNotIn("Create account", text)

    def test_the_plain_card_says_create_account(self):
        cta, text = self.start(gated=False)
        self.assertEqual(cta, "Create account")
        self.assertIn("Tap *Create account*", text)
