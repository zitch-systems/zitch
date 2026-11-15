"""Tier 2 from WhatsApp: one tap from the chat link into the Prembly camera.

Reported: the old path was link -> Zitch page -> PIN -> Zitch page -> Prembly
link -> camera, and inside WhatsApp's Android in-app browser the camera never
opened. The direct link skips the Zitch page and the PIN (the face match against
the already-verified BVN is the proof), never starts a session for a link
preview, and hands Android in-app browsing to Chrome where the camera works.
"""
from unittest.mock import patch
from urllib.parse import urlsplit

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from wallet.services import get_or_create_wallet

from .models import PendingAction, WhatsAppLink
from .verification_web import ACTION_TYPE, face_link

User = get_user_model()
MSISDN = "2348011112233"
ORIGIN = "https://testserver"
PREMBLY_URL = "https://sdk-live.prembly.com/?session=abc12345&has_callback=false"
CHROME_UA = ("Mozilla/5.0 (Linux; Android 14; SM-A146P) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36")
WEBVIEW_UA = ("Mozilla/5.0 (Linux; Android 14; SM-A146P; wv) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Version/4.0 Chrome/130.0 Mobile Safari/537.36")


@override_settings(ZITCH_LINKS={"API_BASE": ORIGIN}, SECURE_SSL_REDIRECT=False,
                   RATELIMIT_ENABLE=False, BANK_ACCOUNT_PROVIDER="partnership",
                   WEMA_PARTNERSHIP_MODE="active")
class FaceLinkTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            username="face-link", phone="08011112233", email="face@example.test",
            email_verified=True, phone_verified=True, bvn_verified=True, tier=1)
        wallet = get_or_create_wallet(self.user)
        wallet.account_number = "1234567890"
        wallet.save()
        WhatsAppLink.objects.create(user=self.user, wa_msisdn=MSISDN,
                                    status=WhatsAppLink.ACTIVE, linked_at=timezone.now())
        self.enterContext(patch("utility.liveness.tier2_face_available", return_value=True))
        self.start = self.enterContext(patch(
            "utility.liveness.start_tier2_liveness",
            return_value={"success": True, "session": "s-1", "status": "pending",
                          "url": PREMBLY_URL}))
        self.path = urlsplit(face_link(self.user, MSISDN)).path

    def tap(self, agent=CHROME_UA):
        return Client().get(self.path, secure=True, HTTP_USER_AGENT=agent)

    def test_link_points_at_the_face_route_not_the_pin_page(self):
        self.assertTrue(self.path.startswith("/wa/face/"))

    def test_a_tap_goes_straight_to_prembly_without_a_pin(self):
        response = self.tap()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], PREMBLY_URL)
        self.start.assert_called_once()
        pa = PendingAction.objects.get(action_type=ACTION_TYPE)
        self.assertEqual(pa.payload["face_session"], "s-1")

    def test_link_previews_never_start_a_session(self):
        for agent in ("facebookexternalhit/1.1", "WhatsApp/2.24.1 A", ""):
            with self.subTest(agent=agent):
                response = self.tap(agent)
                self.assertNotEqual(response.status_code, 302)
        self.start.assert_not_called()

    def test_android_in_app_browser_is_handed_to_chrome(self):
        response = self.tap(WEBVIEW_UA)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("intent://sdk-live.prembly.com/", body)
        self.assertIn("package=com.android.chrome", body)
        self.assertIn(PREMBLY_URL.replace("&", "&amp;"), body)   # visible fallback link

    def test_expired_or_retired_link_is_refused(self):
        PendingAction.objects.filter(action_type=ACTION_TYPE).update(expires_at=timezone.now())
        self.assertEqual(self.tap().status_code, 410)
        self.start.assert_not_called()

    def test_post_is_not_allowed(self):
        response = Client().post(self.path, secure=True, HTTP_USER_AGENT=CHROME_UA)
        self.assertEqual(response.status_code, 405)
        self.start.assert_not_called()
