"""Tier 2 from WhatsApp: one tap from the chat link to a Zitch camera page.

Tier 2 follows Wema's partner-account-upgrade-tier2 contract: the account
number, the customer's BVN and NIN (typed on the page, never stored raw) and a
live face photo, in one call. No third-party liveness vendor and no PIN on the
page. Only the bank's read-back raises the tier.
A link preview never reaches the camera or the bank, Android's in-app WebView is
handed to Chrome where the camera works, and the result is sent to the chat
automatically.
"""
import base64
from unittest.mock import patch
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from wallet.services import get_or_create_wallet

from .models import PendingAction, WhatsAppLink
from .verification_web import (ACTION_TYPE, COMPLETE, FACE_PENDING, PIN, face_link,
                               notify_face_results)

User = get_user_model()
MSISDN = "2348011112233"
ORIGIN = "https://testserver"
CHROME_UA = ("Mozilla/5.0 (Linux; Android 14; SM-A146P) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36")
WEBVIEW_UA = ("Mozilla/5.0 (Linux; Android 14; SM-A146P; wv) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Version/4.0 Chrome/130.0 Mobile Safari/537.36")
SELFIE = base64.b64encode(b"\xff\xd8\xff\xe0 a jpeg selfie").decode()
BANK_TIER2 = {"success": True, "tier": "2", "name": "Ada Face"}
BANK_TIER1 = {"success": True, "tier": "1", "name": "Ada Face"}


TEST_BVN, TEST_NIN = "22222222222", "33333333333"

@override_settings(ZITCH_LINKS={"API_BASE": ORIGIN}, SECURE_SSL_REDIRECT=False,
                   RATELIMIT_ENABLE=False, BANK_ACCOUNT_PROVIDER="partnership",
                   WEMA_PARTNERSHIP_MODE="active")
class FaceLinkTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create(
            username="face-link", first_name="Ada", last_name="Face", phone="08011112233",
            email="face@example.test", email_verified=True, phone_verified=True,
            bvn_verified=True, tier=1)
        self.user.set_bvn(TEST_BVN)
        self.user.save(update_fields=["bvn_hash", "bvn_last4"])
        wallet = get_or_create_wallet(self.user)
        wallet.account_number = "1234567890"
        wallet.save()
        WhatsAppLink.objects.create(user=self.user, wa_msisdn=MSISDN,
                                    status=WhatsAppLink.ACTIVE, linked_at=timezone.now())
        self.enterContext(patch("wallet.views.tier2_face_available", return_value=True))
        self.enterContext(patch("wallet.views._wema_funding_enabled", return_value=True))
        self.enterContext(patch("wallet.views.partnership_new_business_allowed", return_value=True))
        self.enterContext(patch("wallet.views.wema_provider.holder_name_mismatch", return_value=False))
        self.upgrade = self.enterContext(patch(
            "wallet.views.wema_provider.upgrade_tier2", return_value={"success": True, "raw": {}}))
        self.bank = self.enterContext(patch(
            "wallet.views.wema_provider.get_kyc_status", return_value=BANK_TIER2))
        self.reply = self.enterContext(patch("whatsapp.router.reply"))
        self.path = urlsplit(face_link(self.user, MSISDN)).path
        self.browser = Client(enforce_csrf_checks=True)

    def tap(self, agent=CHROME_UA, path=None, client=None):
        return (client or self.browser).get(path or self.path, secure=True, HTTP_USER_AGENT=agent)

    def send(self, image=SELFIE, *, origin=ORIGIN, csrf=True, bvn=None, nin=None):
        bvn = TEST_BVN if bvn is None else bvn
        nin = TEST_NIN if nin is None else nin
        if settings.CSRF_COOKIE_NAME not in self.browser.cookies:
            self.tap()
        headers = {"HTTP_ORIGIN": origin, "HTTP_USER_AGENT": CHROME_UA}
        if csrf:
            headers["HTTP_X_CSRFTOKEN"] = self.browser.cookies[settings.CSRF_COOKIE_NAME].value
        return self.browser.post(self.path, {"image": image, "bvn": bvn, "nin": nin}, secure=True, **headers)

    def action(self):
        return PendingAction.objects.get(action_type=ACTION_TYPE)

    # --- the page --------------------------------------------------------- #
    def test_link_points_at_the_face_route_not_the_pin_page(self):
        self.assertTrue(self.path.startswith("/wa/face/"))

    def test_missing_bvn_or_nin_is_refused_before_the_bank_and_link_stays_open(self):
        for bvn, nin in (("", TEST_NIN), (TEST_BVN, ""), ("123", TEST_NIN)):
            with self.subTest(bvn=bvn, nin=nin):
                response = self.send(bvn=bvn, nin=nin)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["status"], "retry")
                self.assertIn("BVN and NIN", response.json()["message"])
        self.upgrade.assert_not_called()
        self.assertEqual(self.send().status_code, 200)

    def test_a_tap_opens_our_camera_page_without_a_pin_or_vendor(self):
        response = self.tap()
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("getUserMedia", body)
        self.assertIn('capture="user"', body)
        for gone in ("Prembly", "prembly", 'name="pin"'):
            self.assertNotIn(gone, body)
        self.assertIn('name="bvn"', body)
        self.assertIn('name="nin"', body)
        self.assertIn("camera=(self)", response["Permissions-Policy"])
        self.assertNotIn("unsafe-inline", response["Content-Security-Policy"])
        self.upgrade.assert_not_called()

    def test_link_previews_never_reach_the_camera_or_the_bank(self):
        for agent in ("facebookexternalhit/1.1", "WhatsApp/2.24.1 A", ""):
            with self.subTest(agent=agent):
                self.assertNotIn(b"getUserMedia", self.tap(agent).content)
        self.upgrade.assert_not_called()

    def test_android_in_app_browser_is_handed_to_chrome(self):
        response = self.tap(WEBVIEW_UA)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn(f"intent://testserver{self.path}", body)
        self.assertIn("package=com.android.chrome", body)
        self.assertIn(f"{self.path}?stay=1", body)            # visible fallback link
        # The fallback stays in the WebView and uses the photo picker.
        stay = self.tap(WEBVIEW_UA, path=self.path + "?stay=1")
        self.assertContains(stay, "var pickerOnly = true")

    def test_expired_or_retired_link_is_refused(self):
        PendingAction.objects.filter(action_type=ACTION_TYPE).update(expires_at=timezone.now())
        self.assertEqual(self.tap().status_code, 410)
        self.assertEqual(self.send().status_code, 410)
        self.upgrade.assert_not_called()

    def test_unavailable_bank_says_so(self):
        with patch("wallet.views.tier2_face_available", return_value=False):
            self.assertContains(self.tap(), "temporarily unavailable", status_code=503)

    # --- sending the selfie ------------------------------------------------ #
    def test_selfie_alone_upgrades_after_the_bank_confirms(self):
        response = self.send()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "verified")
        self.upgrade.assert_called_once_with("1234567890", bvn=TEST_BVN, nin=TEST_NIN, live_image=SELFIE)
        self.user.refresh_from_db()
        self.assertTrue(self.user.face_verified)
        self.assertEqual(self.user.tier, 2)
        self.assertEqual(self.action().state, COMPLETE)
        self.reply.assert_called_once()
        self.assertIn("Tier 2 verification complete", self.reply.call_args.args[1])
        # A second tap never sends the photo again.
        self.assertEqual(self.send().json()["status"], "verified")
        self.upgrade.assert_called_once()
        self.assertContains(self.tap(), "Tier 2 verification complete")

    def test_bank_acknowledgement_without_tier_two_does_not_upgrade(self):
        self.bank.return_value = BANK_TIER1
        response = self.send()
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "pending")
        self.user.refresh_from_db()
        self.assertFalse(self.user.face_verified)
        self.assertEqual(self.user.tier, 1)
        self.assertEqual(self.action().state, FACE_PENDING)
        self.assertContains(self.tap(), "checking your photo", status_code=202)
        # The pending link cannot send a second photo.
        self.assertEqual(self.send().status_code, 202)
        self.upgrade.assert_called_once()
        self.reply.assert_not_called()

    def test_bank_refusal_lets_the_customer_retake(self):
        self.upgrade.return_value = {"success": False, "message": "Face mismatch for 1234567890"}
        response = self.send()
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["status"], "retry")
        self.assertNotIn("1234567890", response.json()["message"])     # bank text never echoed
        self.assertEqual(self.action().state, PIN)
        self.user.refresh_from_db()
        self.assertFalse(self.user.face_verified)
        self.upgrade.return_value = {"success": True, "raw": {}}
        self.assertEqual(self.send().json()["status"], "verified")

    def test_unreadable_photo_never_reaches_the_bank(self):
        response = self.send(image="not base64!")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "retry")
        self.upgrade.assert_not_called()
        self.assertEqual(self.action().state, PIN)

    def test_cross_site_or_csrf_less_posts_are_refused(self):
        self.assertEqual(self.send(origin="https://evil.example").status_code, 403)
        self.assertEqual(self.send(csrf=False).status_code, 403)
        self.upgrade.assert_not_called()

    def test_a_forwarded_link_still_needs_the_banks_face_match(self):
        """No PIN on the page: the bank matching the face to this account's
        identity is what stops someone else using a forwarded link."""
        self.upgrade.return_value = {"success": False}
        self.assertEqual(self.send().status_code, 422)
        self.user.refresh_from_db()
        self.assertEqual(self.user.tier, 1)


@override_settings(ZITCH_LINKS={"API_BASE": ORIGIN}, SECURE_SSL_REDIRECT=False,
                   RATELIMIT_ENABLE=False, BANK_ACCOUNT_PROVIDER="partnership",
                   WEMA_PARTNERSHIP_MODE="active")
class FaceResultNotificationTests(TestCase):
    """A photo the bank was still checking is finished by the worker with a
    read-back only (the photo is never sent again), and the customer is told
    the result in the chat once, unprompted."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create(
            username="face-result", first_name="Ada", last_name="Face", phone="08011112244",
            email="result@example.test", email_verified=True, phone_verified=True,
            bvn_verified=True, tier=1)
        wallet = get_or_create_wallet(self.user)
        wallet.account_number = "1234567891"
        wallet.save()
        WhatsAppLink.objects.create(user=self.user, wa_msisdn=MSISDN,
                                    status=WhatsAppLink.ACTIVE, linked_at=timezone.now())
        self.enterContext(patch("wallet.views.wema_provider.holder_name_mismatch", return_value=False))
        self.upgrade = self.enterContext(patch("wallet.views.wema_provider.upgrade_tier2"))
        self.bank = self.enterContext(patch(
            "wallet.views.wema_provider.get_kyc_status", return_value=BANK_TIER1))
        face_link(self.user, MSISDN)
        self.pa = PendingAction.objects.get(action_type=ACTION_TYPE)
        PendingAction.objects.filter(pk=self.pa.pk).update(state=FACE_PENDING)
        from wallet.views import _tier2_pending

        _tier2_pending(self.user, "", "")          # what a 202 from the bank leaves behind

    def _sweep(self):
        with patch("whatsapp.router.reply") as reply:
            count = notify_face_results()
        return count, reply

    def test_a_passed_check_is_announced_once(self):
        self.bank.return_value = BANK_TIER2
        count, reply = self._sweep()
        self.assertEqual(count, 1)
        self.assertIn("Tier 2 verification complete", reply.call_args.args[1])
        self.user.refresh_from_db()
        self.assertTrue(self.user.face_verified)
        self.assertEqual(self.user.tier, 2)
        count, reply = self._sweep()
        self.assertEqual(count, 0)
        reply.assert_not_called()
        self.upgrade.assert_not_called()

    def test_a_still_pending_check_sends_nothing(self):
        count, reply = self._sweep()
        self.assertEqual(count, 0)
        reply.assert_not_called()
        self.upgrade.assert_not_called()

    def test_a_refused_check_says_so(self):
        self.bank.return_value = {"success": True, "tier": "2", "name": "Someone Else"}
        with patch("wallet.views.wema_provider.holder_name_mismatch", return_value=True):
            count, reply = self._sweep()
        self.assertEqual(count, 1)
        self.assertIn("wasn't confirmed", reply.call_args.args[1])
        self.user.refresh_from_db()
        self.assertFalse(self.user.face_verified)

    def test_the_chat_prompt_asks_for_bvn_nin_and_no_reply(self):
        from . import router

        with patch("whatsapp.router.send_cta_url", return_value={"success": True}) as send, \
             patch("wallet.views.tier2_face_available", return_value=True):
            router._send_web_verification(self.user, MSISDN, 2)
        body = send.call_args.args[1]
        for gone in ("Prembly", "reply *8*", "on-screen moves"):
            self.assertNotIn(gone, body)
        self.assertIn("enter your BVN and NIN", body)
        self.assertIn("message you here with the result", body)
