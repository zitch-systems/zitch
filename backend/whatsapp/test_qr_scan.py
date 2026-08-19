"""Scanning a payment QR in the chat.

The channel cannot open a camera — WhatsApp exposes no API for it, and a Flow has
no camera component at this Flow-JSON version — so the feature asks for a photo.
These cover the part that matters: what the chat DOES with what it read, and in
particular that a scanned code is a faster way to type an account number rather
than a way around name enquiry, the confirm card or the PIN.
"""
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings

from utility.test_qr import emv
from wallet.services import get_or_create_wallet

from . import router
from .models import PendingAction, WhatsAppLink
from .tests import make_user

MSISDN = "2348011114444"


def _linked():
    user, _ = make_user(phone="08010000021", email="qr@zitch.test")
    WhatsAppLink.objects.create(user=user, wa_msisdn=MSISDN, status=WhatsAppLink.ACTIVE)
    get_or_create_wallet(user)
    return user


class ScanEntryTests(TestCase):
    def setUp(self):
        self.user = _linked()

    def test_the_menu_offers_it(self):
        self.assertIn("Scan a QR code", router.menu_text())

    def test_starting_a_scan_asks_for_a_photo_and_says_how(self):
        # "Tap the camera" is a real instruction; a button that silently does
        # nothing would not be.
        with patch.object(router, "reply") as rep:
            router._start_qr_scan(self.user, MSISDN)
        body = str(rep.call_args.args[1])
        self.assertIn("Camera", body)
        self.assertTrue(PendingAction.objects.filter(
            msisdn=MSISDN, action_type="qr", state=router.QR_WAIT_STATE).exists())

    def test_typing_something_that_is_not_a_command_re_asks_for_the_photo(self):
        pa = PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="qr",
            state=router.QR_WAIT_STATE, payload={},
            expires_at=router._flow_deadline("idle"))
        with patch.object(router, "reply") as rep:
            router._advance_qr(pa, self.user, MSISDN, "0123456789")
        self.assertIn("photo", str(rep.call_args.args[1]).lower())

    def test_a_new_command_escapes_the_scan_rather_than_trapping_them(self):
        pa = PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="qr",
            state=router.QR_WAIT_STATE, payload={},
            expires_at=router._flow_deadline("idle"))
        with patch.object(router, "handle_inbound") as inbound, patch.object(router, "reply"):
            router._advance_qr(pa, self.user, MSISDN, "balance")
        inbound.assert_called_once()


class ScannedCodeTests(TestCase):
    def setUp(self):
        self.user = _linked()

    def _scan(self, payload):
        from utility.qr import parse_payment
        return parse_payment(payload)

    def test_a_dynamic_code_goes_straight_into_the_ordinary_transfer(self):
        with patch.object(router, "_begin_bank_transfer", return_value=True) as begin, \
             patch.object(router, "reply"):
            router.handle_scanned_qr(MSISDN, self._scan(emv()))
        begin.assert_called_once()
        self.assertEqual(begin.call_args.args[2], Decimal("1500.00"))
        self.assertEqual(begin.call_args.args[3], "0123456789")

    def test_a_static_poster_asks_for_the_amount_first(self):
        with patch.object(router, "_begin_bank_transfer") as begin, \
             patch.object(router, "reply") as rep:
            router.handle_scanned_qr(MSISDN, self._scan(emv(amount="", dynamic=False)))
        begin.assert_not_called()
        self.assertIn("how much", str(rep.call_args.args[1]).lower())
        pa = PendingAction.objects.get(msisdn=MSISDN, action_type="qr")
        self.assertEqual(pa.state, router.QR_AMOUNT_STATE)
        self.assertEqual(pa.payload["account"], "0123456789")

    def test_the_amount_reply_then_starts_the_transfer(self):
        PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="qr",
            state=router.QR_AMOUNT_STATE, payload={"account": "0123456789"},
            expires_at=router._flow_deadline("idle"))
        pa = PendingAction.objects.get(msisdn=MSISDN, action_type="qr")
        with patch.object(router, "_begin_bank_transfer", return_value=True) as begin, \
             patch.object(router, "reply"):
            router._advance_qr(pa, self.user, MSISDN, "2500")
        begin.assert_called_once()
        self.assertEqual(begin.call_args.args[2], Decimal("2500"))

    def test_a_corrupt_scan_never_shows_an_account_number(self):
        # The checksum failed, so every digit is suspect — and a customer reads a
        # number off the screen and believes it.
        bad = emv().replace("0123456789", "0123456780", 1)
        with patch.object(router, "reply") as rep, \
             patch.object(router, "_begin_bank_transfer") as begin:
            router.handle_scanned_qr(MSISDN, self._scan(bad))
        begin.assert_not_called()
        body = str(rep.call_args.args[1])
        self.assertNotIn("012345678", body)
        self.assertIn("another photo", body.lower())

    def test_a_merchant_scheme_code_says_so_instead_of_failing_vaguely(self):
        with patch.object(router, "reply") as rep, \
             patch.object(router, "_begin_bank_transfer") as begin:
            router.handle_scanned_qr(MSISDN, self._scan(emv(account="MERCHANT00099")))
        begin.assert_not_called()
        body = str(rep.call_args.args[1])
        self.assertIn("ADA STORE", body)
        self.assertIn("account number", body.lower())

    def test_a_non_payment_code_is_named_as_such(self):
        with patch.object(router, "reply") as rep, \
             patch.object(router, "_begin_bank_transfer") as begin:
            router.handle_scanned_qr(MSISDN, self._scan("WIFI:S:Net;T:WPA;P:pw;;"))
        begin.assert_not_called()
        self.assertIn("isn't a payment code", str(rep.call_args.args[1]))

    def test_an_unlinked_number_cannot_scan(self):
        with patch.object(router, "reply") as rep, \
             patch.object(router, "_begin_bank_transfer") as begin:
            router.handle_scanned_qr("2348000000000", self._scan(emv()))
        begin.assert_not_called()
        self.assertIn("Link your Zitch account", str(rep.call_args.args[1]))


class MediaPipelineTests(TestCase):
    """A photo of a code must reach the scanner, not the image describer."""

    def test_a_qr_photo_is_read_arithmetically_not_described(self):
        import io

        import qrcode

        from . import media

        buf = io.BytesIO()
        qrcode.make(emv()).convert("L").save(buf, format="PNG")
        with patch("whatsapp.providers.download_media",
                   return_value=(buf.getvalue(), "image/png")), \
             patch("whatsapp.llm.configured", return_value=True), \
             patch("whatsapp.llm.describe_image") as describe:
            said, instead, scanned = media.interpret("image", "mid-qr")
        describe.assert_not_called()
        self.assertEqual(said, "")
        self.assertEqual(instead, "")
        self.assertEqual(scanned["account"], "0123456789")

    def test_a_qr_still_reads_with_no_model_configured(self):
        # Gating this behind the LLM would disable the scanner on exactly the
        # deploys most likely to want a deterministic reader.
        import io

        import qrcode

        from . import media

        buf = io.BytesIO()
        qrcode.make(emv()).convert("L").save(buf, format="PNG")
        with patch("whatsapp.providers.download_media",
                   return_value=(buf.getvalue(), "image/png")), \
             patch("whatsapp.llm.configured", return_value=False):
            _said, _instead, scanned = media.interpret("image", "mid-qr")
        self.assertEqual(scanned["account"], "0123456789")

    def test_an_ordinary_photo_still_goes_to_the_describer(self):
        from . import media

        with patch("whatsapp.providers.download_media", return_value=(b"jpegbytes", "image/jpeg")), \
             patch("whatsapp.llm.configured", return_value=True), \
             patch("whatsapp.llm.describe_image", return_value="a receipt"):
            said, instead, scanned = media.interpret("image", "mid-photo")
        self.assertIsNone(scanned)
        self.assertEqual(instead, "")
        self.assertIn("receipt", said)
