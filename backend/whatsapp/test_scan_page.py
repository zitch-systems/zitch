"""The hosted scanner page.

WhatsApp cannot open a camera from a message, but it can open a URL — and a page
can open a camera. These cover the seam that creates: a link that is single-use and
short-lived, a decoder that stays on the SERVER so a page nobody can trust never
gets to assert an account number, and a handoff that raises exactly one confirm card.
"""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from utility.test_qr import emv
from wallet.services import get_or_create_wallet

from . import router
from .models import ScanSession, WhatsAppLink
from .scan_views import new_scan_session, scan_url
from .tests import make_user

MSISDN = "2348011115555"


def _linked():
    user, _ = make_user(phone="08010000031", email="scan@zitch.test")
    WhatsAppLink.objects.create(user=user, wa_msisdn=MSISDN, status=WhatsAppLink.ACTIVE)
    get_or_create_wallet(user)
    return user


def _frame_bytes(payload):
    import io

    import qrcode
    buf = io.BytesIO()
    qrcode.make(payload).convert("L").save(buf, format="PNG")
    return buf.getvalue()


class ScannerLinkTests(TestCase):
    def setUp(self):
        self.user = _linked()

    def test_starting_a_scan_sends_a_button_that_opens_the_camera(self):
        with patch.object(router, "send_cta_url",
                          return_value={"success": True}) as cta:
            router._start_qr_scan(self.user, MSISDN)
        cta.assert_called_once()
        self.assertIn("/scan/", cta.call_args.args[2])
        self.assertIn("camera", cta.call_args.kwargs["cta"].lower())

    def test_a_new_scan_retires_the_previous_link(self):
        # A stale link left in the chat must not still work after they start again.
        first = new_scan_session(self.user, MSISDN)
        new_scan_session(self.user, MSISDN)
        first.refresh_from_db()
        self.assertFalse(first.usable)

    def test_the_link_may_be_sent_as_text_when_the_button_is_refused(self):
        # Unlike the face link, this URL carries no secret and no identity — only a
        # single-use session id — so a plain link is a safe fallback.
        with patch.object(router, "send_cta_url", return_value={"success": False}), \
             patch.object(router, "reply") as rep:
            router._start_qr_scan(self.user, MSISDN)
        self.assertIn("/scan/", str(rep.call_args.args[1]))


class ScannerPageTests(TestCase):
    def setUp(self):
        self.user = _linked()
        self.session = new_scan_session(self.user, MSISDN)

    def test_the_page_renders_for_a_live_link(self):
        res = self.client.get(f"/scan/{self.session.token}")
        self.assertEqual(res.status_code, 200)
        body = res.content.decode()
        self.assertIn("getUserMedia", body)
        # The native-camera fallback must always be present: WhatsApp's iOS browser
        # does not reliably allow a live preview.
        self.assertIn('capture="environment"', body)

    def test_the_page_ships_no_decoder(self):
        # Decoding stays server-side, so a client cannot lie about what it read.
        body = self.client.get(f"/scan/{self.session.token}").content.decode().lower()
        for lib in ("jsqr", "barcodedetector", "zxing"):
            self.assertNotIn(lib, body)

    def test_an_expired_link_says_how_to_get_a_new_one(self):
        self.session.expires_at = timezone.now() - timedelta(seconds=1)
        self.session.save(update_fields=["expires_at"])
        res = self.client.get(f"/scan/{self.session.token}")
        self.assertEqual(res.status_code, 410)
        self.assertIn("11", res.content.decode())

    def test_an_unknown_token_is_not_an_oracle(self):
        self.assertEqual(self.client.get("/scan/nope").status_code, 410)


class FrameDecodingTests(TestCase):
    def setUp(self):
        self.user = _linked()
        self.session = new_scan_session(self.user, MSISDN)

    def _post(self, data, token=None):
        return self.client.post(f"/scan/{token or self.session.token}/frame",
                                data=data, content_type="image/jpeg")

    def test_a_frame_with_no_code_says_keep_looking(self):
        res = self._post(b"not-an-image")
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()["found"])

    def test_a_payment_code_reaches_the_chat(self):
        with patch("whatsapp.router.handle_scanned_qr") as handoff:
            res = self._post(_frame_bytes(emv()))
        self.assertTrue(res.json()["found"])
        handoff.assert_called_once()
        self.assertEqual(handoff.call_args.args[0], MSISDN)
        self.assertEqual(handoff.call_args.args[1]["account"], "0123456789")

    def test_one_link_raises_exactly_one_confirm_card(self):
        # The page fires frames continuously; a second success must not produce a
        # second confirm card for the same scan.
        frame = _frame_bytes(emv())
        with patch("whatsapp.router.handle_scanned_qr") as handoff:
            self._post(frame)
            second = self._post(frame)
        self.assertEqual(handoff.call_count, 1)
        self.assertIn("Already scanned", second.json()["message"])

    def test_a_non_payment_code_does_not_touch_the_chat(self):
        import io

        import qrcode
        buf = io.BytesIO()
        qrcode.make("WIFI:S:Net;T:WPA;P:pw;;").convert("L").save(buf, format="PNG")
        with patch("whatsapp.router.handle_scanned_qr") as handoff:
            res = self._post(buf.getvalue())
        handoff.assert_not_called()
        self.assertFalse(res.json()["payable"])
        self.assertTrue(self.session.usable)

    def test_an_expired_link_cannot_post_frames(self):
        self.session.expires_at = timezone.now() - timedelta(seconds=1)
        self.session.save(update_fields=["expires_at"])
        with patch("whatsapp.router.handle_scanned_qr") as handoff:
            res = self._post(_frame_bytes(emv()))
        self.assertEqual(res.status_code, 410)
        handoff.assert_not_called()

    def test_an_oversized_upload_is_refused_before_decoding(self):
        res = self._post(b"x" * (3 * 1024 * 1024 + 1))
        self.assertEqual(res.status_code, 413)

    def test_get_is_refused(self):
        self.assertEqual(
            self.client.get(f"/scan/{self.session.token}/frame").status_code, 405)

    def test_a_chat_handoff_failure_still_answers_the_page(self):
        # The customer is standing there holding a camera; a broken chat send must
        # not leave the page spinning forever.
        with patch("whatsapp.router.handle_scanned_qr", side_effect=RuntimeError("wa")):
            res = self._post(_frame_bytes(emv()))
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["found"])
        self.assertIn("couldn't reach your chat", res.json()["message"])
