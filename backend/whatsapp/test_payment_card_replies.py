"""Replies under an open payment card, and payment screenshots (17 Nov 2026).

1. A checkout screenshot became a confirm card. The customer replied "Send the
   money" - and the card was dropped ("leaving that payment unconfirmed") and
   they were asked for the details they had just sent. Asking for THIS payment
   to go ahead must keep it; only a cancel or a correction, or a different
   payment, replaces it.
2. The customer then forwarded a screenshot of that chat (the checkout page and
   the confirm card inside it). The photo reader described a chat, the model
   answered "you've shared a screenshot description rather than a request", and
   nothing happened twice. A picture of payment details is the request.
"""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, SimpleTestCase
from django.utils import timezone

from . import media, router
from .flows import FLOW_PIN_STATE
from .models import PendingAction
from .test_audit_regressions import _out
from .test_flows import MSISDN, _make_user


class PendingReplyKindTests(SimpleTestCase):

    def test_asking_for_the_payment_to_go_ahead_is_proceed(self):
        for said in ("Send the money", "send it", "please send", "go ahead", "proceed",
                     "process it", "pay now", "Continue", "send the money to them",
                     "confirm the transfer", "send the money to the account", "do it"):
            with self.subTest(said=said):
                self.assertEqual(router._pending_reply_kind(said), "proceed")

    def test_stopping_or_a_correction_is_cancel(self):
        for said in ("cancel", "stop", "don't send it", "do not process", "wrong account",
                     "that's a mistake", "no", "not this one", "never mind", "abort"):
            with self.subTest(said=said):
                self.assertEqual(router._pending_reply_kind(said), "cancel")

    def test_someone_else_is_a_new_payment(self):
        self.assertEqual(router._pending_reply_kind("send money to Ada"), "new")

    def test_new_details_and_other_requests_are_left_to_the_caller(self):
        for said in ("send 3k to 7066737466", "9727326728 opay 2800", "balance",
                     "what is my balance", "menu"):
            with self.subTest(said=said):
                self.assertEqual(router._pending_reply_kind(said), "")


class OpenCardReplyTests(TestCase):

    def setUp(self):
        self.user = _make_user()

    def _armed(self):
        return PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="transfer", state=FLOW_PIN_STATE,
            payload={"amount": "2800", "account": "9727326728", "bank_code": "100039",
                     "bank_name": "Paystack-Titan", "name": "PAYSTACK CHECKOUT",
                     "pin_attempts": 0},
            expires_at=timezone.now() + timedelta(minutes=5))

    def test_send_the_money_keeps_the_payment_and_sends_the_card_again(self):
        pa = self._armed()
        with patch("whatsapp.router._send_pin_flow", return_value=True) as card:
            router.handle_inbound(MSISDN, "Send the money")
        card.assert_called_once()
        self.assertEqual(card.call_args.args[0].pk, pa.pk)
        self.assertTrue(PendingAction.objects.filter(pk=pa.pk).exists())
        self.assertFalse(any("leaving that payment" in t for t in _out()))

    def test_send_the_money_without_a_card_still_keeps_the_payment(self):
        pa = self._armed()
        with patch("whatsapp.router._send_pin_flow", return_value=False):
            router.handle_inbound(MSISDN, "go ahead")
        self.assertTrue(PendingAction.objects.filter(pk=pa.pk).exists())
        self.assertIn("Use PIN", _out()[-1])

    def test_a_correction_cancels_and_says_nothing_was_sent(self):
        pa = self._armed()
        router.handle_inbound(MSISDN, "wrong account, don't send")
        self.assertFalse(PendingAction.objects.filter(pk=pa.pk).exists())
        self.assertIn("Nothing was sent", _out()[-1])

    def test_new_account_details_replace_the_card(self):
        pa = self._armed()
        with patch("whatsapp.router.handle_inbound", wraps=router.handle_inbound) as again:
            router.handle_inbound(MSISDN, "send 3k to 7066737466 opay")
        self.assertFalse(PendingAction.objects.filter(pk=pa.pk).exists())
        self.assertGreaterEqual(again.call_count, 2)       # re-routed as the new payment

    def test_a_stray_code_keeps_the_card(self):
        pa = self._armed()
        router.handle_inbound(MSISDN, "123456")
        self.assertTrue(PendingAction.objects.filter(pk=pa.pk).exists())
        self.assertIn("still waiting", _out()[-1])


class ScreenshotIsTheRequestTests(SimpleTestCase):

    READ = ("PAYMENT: account=9727326728 bank=Paystack-Titan amount=2800 name=PAYSTACK CHECKOUT\n"
            "A screenshot of a WhatsApp chat showing a Paystack checkout page and a "
            "Confirm payment card.")

    def _interpret(self, described, caption=""):
        with patch("whatsapp.llm.configured", return_value=True), \
             patch("whatsapp.providers.download_media", return_value=(b"jpg", "image/jpeg")), \
             patch("utility.qr.decode_image", return_value=""), \
             patch("whatsapp.llm.describe_image", return_value=described):
            return media.interpret("image", "mid-1", caption=caption)

    def test_a_forwarded_chat_screenshot_becomes_the_transfer(self):
        said, instead, scanned = self._interpret(self.READ)
        self.assertEqual(said, "send 2800 to 9727326728 Paystack-Titan")
        self.assertEqual(instead, "")
        self.assertIsNone(scanned)

    def test_the_caption_still_leads(self):
        said, _instead, _scanned = self._interpret(self.READ, caption="Send the money")
        self.assertEqual(said, "Send the money\nsend 2800 to 9727326728 Paystack-Titan")

    def test_amount_formats_and_a_missing_amount(self):
        cases = {
            "PAYMENT: account=9727326728 bank=Opay amount=NGN 2,800.00 name=none": "send 2800 to 9727326728 Opay",
            "**PAYMENT:** account=9727326728 bank=none amount=none name=none": "send to 9727326728",
        }
        for line, expected in cases.items():
            with self.subTest(line=line):
                self.assertEqual(media.payment_request(line)[0], expected)

    def test_no_account_leaves_the_description(self):
        said, _instead, _scanned = self._interpret(
            "PAYMENT: none\nA photo of a cat on a sofa.")
        self.assertEqual(said, "A photo of a cat on a sofa.")

    def test_an_older_reply_without_the_line_is_unchanged(self):
        said, _instead, _scanned = self._interpret("A slip: GTBank 0123456789")
        self.assertEqual(said, "A slip: GTBank 0123456789")
