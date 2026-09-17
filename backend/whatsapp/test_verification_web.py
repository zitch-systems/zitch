"""Real browser CSRF/PIN gates and existing address business logic; no live calls."""
import io
import json
import re
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import JsonResponse
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from PIL import Image

from accounts.models import AccessToken
from common.http import evaluate_transaction_pin
from wallet.services import get_or_create_wallet

from .models import PendingAction, WhatsAppLink
from .verification_web import (ACTION_TYPE, COMPLETE, MAX_REQUEST_BYTES, PIN,
                               PROCESSING, READY, REVIEW, SALT, TTL_SECONDS,
                               start_verification)

User = get_user_model()
MSISDN = "2348011112222"
ORIGIN = "https://testserver"
GOOD_PIN = "582940"


def document(raw=None):
    if raw is None:
        out = io.BytesIO()
        Image.new("RGB", (20, 20), "white").save(out, format="PNG")
        raw = out.getvalue()
    return SimpleUploadedFile("proof.png", raw, content_type="image/png")


@override_settings(
    ZITCH_LINKS={"API_BASE": ORIGIN}, SECURE_SSL_REDIRECT=False,
    CSRF_COOKIE_SECURE=True, RATELIMIT_ENABLE=False,
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
)
class VerificationWebTests(TestCase):
    def setUp(self):
        cache.clear()
        self.enterContext(patch("requests.sessions.Session.request",
                                side_effect=AssertionError("Unexpected network call")))
        self.client = Client(enforce_csrf_checks=True)
        self.user = User.objects.create(
            username="web-verification", phone="08011112222", email="verify@example.test",
            email_verified=True, phone_verified=True, bvn_verified=True,
            nin_verified=True, face_verified=True, tier=2,
        )
        self.user.set_transaction_pin(GOOD_PIN)
        self.user.save()
        wallet = get_or_create_wallet(self.user)
        wallet.account_number = "1234567890"
        wallet.bank_tier = 2
        wallet.save()
        self.link = WhatsAppLink.objects.create(
            user=self.user, wa_msisdn=MSISDN, status=WhatsAppLink.ACTIVE,
            linked_at=timezone.now(),
        )
        # No test can accidentally touch a remote KYC/bank service.
        self.face = self.enterContext(patch("wallet.views.kyc_verify_face"))
        self.tier2 = self.enterContext(patch("wallet.views.wema_provider.upgrade_tier2"))
        self.provider = self.enterContext(patch("accounts.views.kyc_verify_address",
                                                return_value={"success": True}))
        self.enterContext(patch("accounts.views.kyc_provider", return_value="prembly"))
        self.enterContext(patch("accounts.views._sync_wema_tier3"))
        self.bank_address = self.enterContext(patch("accounts.views.wema.upgrade_tier3",
                                                   return_value={"success": True}))
        self.path = self.start()

    def start(self, tier=3):
        url = start_verification(self.user, MSISDN, tier)
        self.pa = PendingAction.objects.get(action_type=ACTION_TYPE, state=PIN)
        return urlsplit(url).path

    def get(self, path=None, client=None):
        return (client or self.client).get(path or self.path, secure=True)

    def post(self, data, *, path=None, client=None, csrf=True, origin=ORIGIN, **extra):
        browser = client or self.client
        headers = {"HTTP_ORIGIN": origin, **extra}
        if csrf and settings.CSRF_COOKIE_NAME in browser.cookies:
            headers["HTTP_X_CSRFTOKEN"] = browser.cookies[settings.CSRF_COOKIE_NAME].value
        return browser.post(path or self.path, data, secure=True, **headers)

    def unlock(self, client=None, path=None):
        self.assertEqual(self.get(path=path, client=client).status_code, 200)
        result = self.post({"action": "pin", "pin": GOOD_PIN}, client=client, path=path)
        self.assertEqual(result.status_code, 303)
        return result

    def address(self, **overrides):
        return {"action": "address", "buildingNumber": "12", "street": "Palm Street",
                "city": "Ikeja", "state": "Lagos", "lga": "Ikeja",
                "landmark": "Town hall", "postalCode": "100001", "document": document(),
                **overrides}

    def assert_security_headers(self, response):
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertEqual(response["X-Frame-Options"], "DENY")
        self.assertIn("frame-ancestors 'none'", response["Content-Security-Policy"])
        self.assertIn("form-action 'self'", response["Content-Security-Policy"])
        self.assertNotIn("unsafe-inline", response["Content-Security-Policy"])

    def test_start_is_signed_opaque_scoped_fifteen_minutes_and_has_no_api_token(self):
        claims = signing.loads(self.path.rsplit("/", 1)[1], salt=SALT)
        self.assertEqual(set(claims), {"p", "b"})
        self.assertNotIn(MSISDN, json.dumps(claims))
        self.assertNotIn(self.user.phone, json.dumps(claims))
        self.assertEqual(self.pa.user_id, self.user.pk)
        self.assertEqual(self.pa.msisdn, MSISDN)
        self.assertEqual(self.pa.payload["tier"], 3)
        self.assertAlmostEqual((self.pa.expires_at - self.pa.created).total_seconds(), TTL_SECONDS, delta=1)
        self.assertFalse(AccessToken.objects.exists())

    def test_start_requires_existing_matching_active_link(self):
        other = User.objects.create(username="other")
        for user, number, tier in ((other, MSISDN, 3), (self.user, "2348022223333", 3),
                                   (self.user, MSISDN, 1), (self.user, MSISDN, True)):
            with self.subTest(user=user.pk, number=number, tier=tier), self.assertRaises(ValueError):
                start_verification(user, number, tier)
        self.link.status = WhatsAppLink.PENDING
        self.link.save()
        with self.assertRaises(ValueError):
            start_verification(self.user, MSISDN, 3)

    def test_new_link_retires_only_previous_web_actions(self):
        unrelated = PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="transfer", state="amount",
            expires_at=timezone.now() + timedelta(minutes=2),
        )
        old = self.path
        self.path = self.start()
        self.assertEqual(self.get(old).status_code, 410)
        self.assertEqual(self.get().status_code, 200)
        unrelated.refresh_from_db()
        self.assertEqual(unrelated.state, "amount")

    def test_router_current_action_excludes_every_portal_state(self):
        from .router import _current_action

        for state in (PIN, READY, PROCESSING, COMPLETE, REVIEW, "web_retired"):
            with self.subTest(state=state):
                PendingAction.objects.filter(pk=self.pa.pk).update(state=state)
                self.assertIsNone(_current_action(MSISDN))

    def test_router_current_action_finds_older_chat_flow_behind_portal(self):
        from .router import _current_action

        chat = PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="transfer", state="amount",
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        PendingAction.objects.filter(pk=chat.pk).update(created=self.pa.created - timedelta(seconds=1))
        self.assertEqual(_current_action(MSISDN).pk, chat.pk)

    def test_router_clear_actions_preserves_every_portal_state(self):
        from .router import _clear_actions

        for state in (PIN, READY, PROCESSING, COMPLETE, REVIEW, "web_retired"):
            with self.subTest(state=state):
                self.path = self.start()
                PendingAction.objects.filter(pk=self.pa.pk).update(state=state)
                chat = PendingAction.objects.create(
                    user=self.user, msisdn=MSISDN, action_type="transfer", state="amount",
                    expires_at=timezone.now() + timedelta(minutes=5),
                )
                _clear_actions(MSISDN)
                self.assertTrue(PendingAction.objects.filter(pk=self.pa.pk).exists())
                self.assertFalse(PendingAction.objects.filter(pk=chat.pk).exists())

    def test_router_menu_does_not_invalidate_an_open_browser_form(self):
        from .router import handle_inbound

        self.unlock()
        with patch("whatsapp.router.send_menu") as menu:
            handle_inbound(MSISDN, "menu")
        menu.assert_called_once_with(MSISDN)
        self.assertContains(self.get(), 'name="document"')
        self.assertEqual(self.post(self.address()).status_code, 200)

    def test_router_explicit_cancel_revokes_unsubmitted_portal(self):
        from .router import handle_inbound

        for command, unlocked in (("cancel", False), ("quit", True)):
            with self.subTest(command=command, unlocked=unlocked):
                self.path = self.start()
                if unlocked:
                    self.unlock()
                with patch("whatsapp.router.reply"):
                    handle_inbound(MSISDN, command)
                self.assertEqual(self.get().status_code, 410)
                self.assertFalse(PendingAction.objects.filter(pk=self.pa.pk).exists())
        self.provider.assert_not_called()

    def test_router_menu_and_cancel_preserve_already_authorized_payment(self):
        from .router import EXECUTING_STATE, handle_inbound

        payment = PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="transfer", state=EXECUTING_STATE,
            expires_at=timezone.now() + timedelta(minutes=15),
        )
        with patch("whatsapp.router.send_menu"), patch("whatsapp.router.reply") as reply:
            handle_inbound(MSISDN, "menu")
            self.assertTrue(PendingAction.objects.filter(pk=payment.pk).exists())
            self.assertEqual(self.get().status_code, 200)
            handle_inbound(MSISDN, "cancel")
        self.assertTrue(PendingAction.objects.filter(pk=payment.pk).exists())
        self.assertIn("processing", reply.call_args.args[1])
        self.assertIn("cannot", reply.call_args.args[1])
        self.assertEqual(self.get().status_code, 410)

    def test_router_cancel_during_provider_call_preserves_inflight_verification(self):
        from .router import handle_inbound

        self.unlock()

        def verifying(*args, **kwargs):
            with patch("whatsapp.router.reply") as reply:
                handle_inbound(MSISDN, "cancel")
            self.pa.refresh_from_db()
            self.assertEqual(self.pa.state, PROCESSING)
            self.assertIn("processing", reply.call_args.args[1])
            self.assertIn("cannot", reply.call_args.args[1])
            return {"success": True}

        self.provider.side_effect = verifying
        self.assertEqual(self.post(self.address()).status_code, 200)
        self.pa.refresh_from_db()
        self.assertEqual(self.pa.state, COMPLETE)
        self.assertEqual(self.post(self.address()).status_code, 410)
        self.provider.assert_called_once()

    def test_router_unrelated_command_reaches_its_handler_without_consuming_portal(self):
        from .router import handle_inbound

        with patch("whatsapp.router._do_support") as support, patch("whatsapp.router.send_menu"):
            handle_inbound(MSISDN, "support")
        support.assert_called_once_with(MSISDN)
        self.assertEqual(self.get().status_code, 200)

    def test_router_timeout_does_not_announce_or_delete_expired_portal(self):
        from .router import _announce_timeout

        PendingAction.objects.filter(pk=self.pa.pk).update(
            state=PROCESSING, expires_at=timezone.now() - timedelta(seconds=1))
        with patch("whatsapp.router.reply") as reply:
            self.assertFalse(_announce_timeout(MSISDN))
        reply.assert_not_called()
        self.assertTrue(PendingAction.objects.filter(pk=self.pa.pk).exists())

    def test_router_chat_timeout_preserves_expired_portal_record(self):
        from .router import _announce_timeout

        PendingAction.objects.filter(pk=self.pa.pk).update(
            state=REVIEW, expires_at=timezone.now() - timedelta(seconds=1))
        chat = PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="transfer", state="amount",
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        with patch("whatsapp.router.reply") as reply:
            self.assertTrue(_announce_timeout(MSISDN))
        reply.assert_called_once()
        self.assertFalse(PendingAction.objects.filter(pk=chat.pk).exists())
        self.assertTrue(PendingAction.objects.filter(pk=self.pa.pk).exists())

    def test_router_tier3_button_opens_bound_portal_without_provider_call_before_pin(self):
        from .router import _offer_tier_upgrade, handle_inbound

        with patch("whatsapp.router.reply_buttons") as buttons:
            _offer_tier_upgrade(self.user, MSISDN)
        self.assertIn(("tier3", "Upgrade to Tier 3"), buttons.call_args.args[2])
        with patch("whatsapp.router.send_cta_url", return_value={"success": True}) as cta, \
                patch("accounts.views.verify_kyc_address") as service:
            handle_inbound(MSISDN, "tier3")
            cta.assert_called_once()
            self.assertEqual(cta.call_args.args[0], MSISDN)
            url = cta.call_args.args[2]
            parsed = urlsplit(url)
            self.assertEqual(f"{parsed.scheme}://{parsed.netloc}", ORIGIN)
            self.assertEqual(parsed.query, "")
            self.assertTrue(parsed.path.startswith("/wa/verify/"))
            claims = signing.loads(parsed.path.rsplit("/", 1)[1], salt=SALT)
            self.pa = PendingAction.objects.get(pk=claims["p"])
            self.assertEqual((self.pa.user_id, self.pa.msisdn, self.pa.payload["tier"]),
                             (self.user.pk, MSISDN, 3))
            self.assertEqual(self.pa.state, PIN)
            self.path = parsed.path
            self.assertContains(self.get(), "Transaction PIN")
            self.assertEqual(self.post(self.address()).status_code, 403)
            service.assert_not_called()
        self.face.assert_not_called()
        self.tier2.assert_not_called()
        self.bank_address.assert_not_called()
        self.provider.assert_not_called()
        self.assertFalse(AccessToken.objects.exists())
        # The real shared service becomes reachable only after the PIN exchange.
        self.unlock()
        self.assertEqual(self.post(self.address()).status_code, 200)
        self.provider.assert_called_once()

    def test_router_tier3_cta_failure_falls_back_to_same_valid_https_link(self):
        from .router import _send_web_verification

        with patch("whatsapp.router.send_cta_url", return_value={"success": False}) as cta, \
                patch("whatsapp.router.reply") as reply:
            _send_web_verification(self.user, MSISDN, 3)
        url = cta.call_args.args[2]
        self.assertIn(url, reply.call_args.args[1])
        self.assertContains(self.get(urlsplit(url).path), "Transaction PIN")
        self.provider.assert_not_called()
        self.bank_address.assert_not_called()

    def test_router_tier3_button_requires_tier2_before_creating_url(self):
        from .router import KYC_UPGRADE_STATE, handle_inbound

        User.objects.filter(pk=self.user.pk).update(tier=1, face_verified=False)
        PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="kyc", state=KYC_UPGRADE_STATE,
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        with patch("whatsapp.verification_web.start_verification") as start, \
                patch("whatsapp.router.reply"), patch("whatsapp.router.send_cta_url") as cta:
            handle_inbound(MSISDN, "tier3")
        start.assert_not_called()
        cta.assert_not_called()
        self.provider.assert_not_called()

    def test_router_tier2_never_offers_unsupported_web_liveness(self):
        from .router import KYC_UPGRADE_STATE, handle_inbound

        PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="kyc", state=KYC_UPGRADE_STATE,
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        with patch("whatsapp.verification_web.start_verification") as start, \
                patch("whatsapp.router.reply") as reply, patch("whatsapp.router.send_cta_url") as cta:
            handle_inbound(MSISDN, "tier2")
        start.assert_not_called()
        cta.assert_not_called()
        self.assertIn("not yet available", reply.call_args.args[1])
        self.face.assert_not_called()
        self.tier2.assert_not_called()

    def test_get_is_safe_for_link_preview_and_does_not_reveal_kyc_form(self):
        response = self.get()
        self.assertContains(response, "Transaction PIN")
        self.assertNotContains(response, 'name="document"')
        self.assertNotContains(response, self.user.phone)
        self.assertNotContains(response, self.user.email)
        self.pa.refresh_from_db()
        self.assertEqual(self.pa.state, PIN)
        self.assert_security_headers(response)

    def test_pin_proof_is_httponly_secure_action_path_bound(self):
        response = self.unlock()
        self.pa.refresh_from_db()
        self.assertEqual(self.pa.state, READY)
        name = f"__Secure-wa_verify_{self.pa.pk}"
        cookie = response.cookies[name]
        self.assertTrue(cookie["secure"])
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Strict")
        self.assertEqual(cookie["path"], self.path)
        self.assertNotEqual(self.pa.payload["browser_proof"], cookie.value)
        self.assertNotIn(GOOD_PIN, json.dumps(self.pa.payload))
        self.assertContains(self.get(), 'name="document"')
        self.assertFalse(AccessToken.objects.exists())

    def test_fetch_pin_redirect_has_refreshed_form_csrf_and_no_referer_required(self):
        response = self.unlock()
        # fetch follows this same-resource 303 and stores the rotated CSRF cookie.
        page = self.get(response["Location"])
        csrf_value = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"',
                               page.content.decode()).group(1)
        body = self.address(csrfmiddlewaretoken=csrf_value)
        response = self.post(body, csrf=False, HTTP_SEC_FETCH_MODE="cors",
                             HTTP_SEC_FETCH_SITE="same-origin")
        self.assertEqual(response.status_code, 200)
        self.assert_security_headers(response)

    def test_second_browser_with_link_needs_its_own_pin_proof(self):
        self.unlock()
        stranger = Client(enforce_csrf_checks=True)
        self.assertContains(self.get(client=stranger), "Transaction PIN")
        result = self.post(self.address(), client=stranger)
        self.assertEqual(result.status_code, 403)
        self.provider.assert_not_called()

    def test_browser_proof_cannot_authorize_a_different_action(self):
        self.unlock()
        old_cookie = self.client.cookies[f"__Secure-wa_verify_{self.pa.pk}"].value
        self.path = self.start()
        self.client.cookies[f"__Secure-wa_verify_{self.pa.pk}"] = old_cookie
        self.assertContains(self.get(), "Transaction PIN")
        self.assertEqual(self.post(self.address()).status_code, 403)
        self.provider.assert_not_called()

    def test_shared_pin_budget_and_lockout_apply_across_channels(self):
        self.get()
        for _ in range(User.PIN_MAX_ATTEMPTS - 1):
            evaluate_transaction_pin(self.user, "000000")
        response = self.post({"action": "pin", "pin": "not a PIN"})
        self.assertEqual(response.status_code, 429)
        self.user.refresh_from_db()
        self.assertTrue(self.user.pin_locked)
        self.assertEqual(self.post({"action": "pin", "pin": GOOD_PIN}).status_code, 429)
        self.assert_security_headers(response)

    def test_lockout_after_unlock_revokes_access(self):
        self.unlock()
        User.objects.filter(pk=self.user.pk).update(pin_locked_until=timezone.now() + timedelta(hours=1))
        self.assertNotContains(self.get(), 'name="document"')
        self.assertEqual(self.post(self.address()).status_code, 403)
        self.provider.assert_not_called()

    def test_missing_pin_and_required_pin_reset_fail_closed(self):
        for fields in ({"transaction_pin": ""}, {"pin_reset_required": True}):
            with self.subTest(fields=fields):
                User.objects.filter(pk=self.user.pk).update(**fields)
                self.path = self.start()
                self.get()
                self.assertEqual(self.post({"action": "pin", "pin": GOOD_PIN}).status_code, 403)

    def test_pin_reset_password_reset_phone_change_or_freeze_invalidates_link(self):
        for field, value in (("transaction_pin", "changed"), ("password", "changed"),
                             ("phone", "08000000000"), ("is_active", False)):
            with self.subTest(field=field):
                self.user.refresh_from_db()
                old_value = getattr(self.user, field)
                User.objects.filter(pk=self.user.pk).update(**{field: value})
                self.assertEqual(self.get().status_code, 410)
                User.objects.filter(pk=self.user.pk).update(**{field: old_value})

    def test_unlink_and_new_link_cannot_revive_existing_url(self):
        self.unlock()
        self.link.delete()
        WhatsAppLink.objects.create(user=self.user, wa_msisdn=MSISDN, status=WhatsAppLink.ACTIVE)
        self.assertEqual(self.get().status_code, 410)
        self.assertEqual(self.post(self.address()).status_code, 410)
        self.provider.assert_not_called()

    def test_changed_link_timestamp_invalidates_url(self):
        WhatsAppLink.objects.filter(pk=self.link.pk).update(linked_at=timezone.now() + timedelta(seconds=1))
        self.assertEqual(self.get().status_code, 410)

    def test_pending_link_and_other_user_link_are_refused(self):
        self.link.status = WhatsAppLink.PENDING
        self.link.save()
        self.assertEqual(self.get().status_code, 410)
        self.link.status = WhatsAppLink.ACTIVE
        self.link.user = User.objects.create(username="new-owner")
        self.link.save()
        self.assertEqual(self.get().status_code, 410)

    def test_tamper_wrong_salt_and_database_rebinding_fail_closed(self):
        token = self.path.rsplit("/", 1)[1]
        wrong_salt = signing.dumps({"p": self.pa.pk, "b": "a" * 64}, salt="another-surface")
        for bad in (token + "x", wrong_salt, "x" * 513):
            self.assertEqual(self.get("/wa/verify/" + bad).status_code, 410)
        payload = dict(self.pa.payload, tier=2)
        PendingAction.objects.filter(pk=self.pa.pk).update(payload=payload)
        self.assertEqual(self.get().status_code, 410)

    def test_timestamp_and_database_expiry_are_independently_enforced(self):
        with patch("django.core.signing.time.time", return_value=timezone.now().timestamp() + TTL_SECONDS + 2):
            self.assertEqual(self.get().status_code, 410)
        PendingAction.objects.filter(pk=self.pa.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.get().status_code, 410)

    def test_csrf_origin_and_secure_transport_are_enforced_with_security_headers(self):
        self.get()
        data = {"action": "pin", "pin": GOOD_PIN}
        cases = (
            {"csrf": False}, {"origin": "https://evil.example"}, {"origin": "null"},
            {"origin": ""}, {"HTTP_SEC_FETCH_SITE": "cross-site"},
            {"HTTP_X_CSRFTOKEN": "bad"},
        )
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                # Supply invalid CSRF separately: the helper adds the valid one last.
                if "HTTP_X_CSRFTOKEN" in kwargs:
                    kwargs["csrf"] = False
                response = self.post(data, **kwargs)
                self.assertEqual(response.status_code, 403)
                self.assert_security_headers(response)
        self.assertEqual(self.client.get(self.path).status_code, 403)
        self.user.refresh_from_db()
        self.assertEqual(self.user.pin_failed_attempts, 0)

    @override_settings(CSRF_TRUSTED_ORIGINS=["https://other.zitch.test"])
    def test_other_trusted_origin_is_still_not_this_portal_origin(self):
        self.get()
        self.assertEqual(self.post({"action": "pin", "pin": GOOD_PIN},
                                   origin="https://other.zitch.test").status_code, 403)

    def test_methods_content_type_and_size_are_bounded(self):
        for method in ("put", "delete", "patch", "head", "options"):
            response = getattr(self.client, method)(self.path, secure=True)
            self.assertEqual(response.status_code, 405)
            self.assert_security_headers(response)
        response = self.client.post(self.path, "{}", content_type="application/json", secure=True,
                                    HTTP_ORIGIN=ORIGIN)
        self.assertEqual(response.status_code, 415)
        response = self.client.post(self.path, b"x" * (MAX_REQUEST_BYTES + 1),
                                    content_type="application/x-www-form-urlencoded",
                                    secure=True, HTTP_ORIGIN=ORIGIN)
        self.assertEqual(response.status_code, 413)
        self.assert_security_headers(response)

    def test_tier2_is_truthful_and_never_calls_still_photo_liveness_or_upgrade(self):
        self.path = self.start(tier=2)
        self.unlock()
        response = self.get()
        self.assertContains(response, "not available in this browser yet")
        self.assertNotContains(response, 'name="bvn"')
        self.assertNotContains(response, 'name="selfie"')
        self.assertEqual(self.post({"action": "tier2", "live_image": "photo",
                                   "bvn": "12345678901", "nin": "12345678902"}).status_code, 409)
        self.face.assert_not_called()
        self.tier2.assert_not_called()
        self.assertEqual(self.post(self.address()).status_code, 400)
        self.provider.assert_not_called()

    def test_tier3_requires_actual_tier2_identity_flags(self):
        User.objects.filter(pk=self.user.pk).update(face_verified=False)
        self.unlock()
        self.assertEqual(self.get().status_code, 409)
        self.assertEqual(self.post(self.address()).status_code, 409)
        self.provider.assert_not_called()

    def test_address_calls_existing_view_and_consumes_once_without_storing_upload(self):
        self.unlock()
        response = self.post(self.address())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Address verified")
        self.assert_security_headers(response)
        self.user.refresh_from_db()
        self.assertTrue(self.user.address_verified)
        self.assertEqual(self.user.tier, 3)
        self.provider.assert_called_once()
        self.assertIn("12 Palm Street", self.provider.call_args.args[0])
        self.assertTrue(self.provider.call_args.kwargs["document"])
        self.pa.refresh_from_db()
        self.assertEqual(self.pa.state, COMPLETE)
        for sensitive in ("Palm Street", "100001", GOOD_PIN, "document", "base64"):
            self.assertNotIn(sensitive, json.dumps(self.pa.payload))
        self.assertEqual(self.get().status_code, 410)
        self.assertEqual(self.post(self.address()).status_code, 410)
        self.provider.assert_called_once()
        self.assertFalse(AccessToken.objects.exists())

    def test_backend_business_validation_is_not_bypassed(self):
        self.unlock()
        self.provider.return_value = {"success": False, "message": "Private provider reply 12345678901"}
        response = self.post(self.address())
        self.assertEqual(response.status_code, 400)
        self.assertNotContains(response, "12345678901", status_code=400)
        self.user.refresh_from_db()
        self.assertFalse(self.user.address_verified)
        self.pa.refresh_from_db()
        self.assertEqual(self.pa.state, READY)
        self.provider.return_value = {"success": True}
        self.assertEqual(self.post(self.address()).status_code, 200)

    def test_explicit_fixed_fields_are_forwarded_in_authenticated_context(self):
        self.unlock()
        observed = {}

        def backend(user, data):
            observed.update(data)
            self.assertEqual(user.pk, self.user.pk)
            User.objects.filter(pk=self.user.pk).update(address_verified=True)
            return JsonResponse({"success": True})

        with patch("accounts.views.verify_kyc_address", side_effect=backend):
            response = self.post(self.address(), HTTP_AUTHORIZATION="Bearer attacker-token")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(observed["buildingNumber"], "12")
        self.assertEqual(observed["street"], "Palm Street")
        self.assertEqual(observed["lga"], "Ikeja")
        self.assertEqual(observed["country"], "Nigeria")
        self.assertNotIn("action", observed)
        self.assertNotIn("access_token", observed)

    def test_unrecognized_operation_or_fields_cannot_form_an_api_proxy(self):
        self.unlock()
        for action in ("transfer", "face", "kyc_face_start", "wema_wallet_upgrade_tier2", "/api/kyc/address/"):
            self.assertEqual(self.post({"action": action}).status_code, 400)
        for field, value in (("user_id", "2"), ("access_token", "secret"),
                             ("address_verified", "true"), ("url", "https://evil.example")):
            self.assertEqual(self.post(self.address(**{field: value})).status_code, 400)
        self.provider.assert_not_called()

    def test_invalid_or_missing_images_fields_and_duplicate_values_never_dispatch(self):
        self.unlock()
        invalid = [self.address(document=document(b"not an image")),
                   self.address(document=document(b"x" * 2_000_001)),
                   self.address(street=""), self.address(lga=""),
                   self.address(city="x" * 61), self.address(street=["one", "two"])]
        missing = self.address()
        missing.pop("document")
        invalid.append(missing)
        for data in invalid:
            response = self.post(data)
            self.assertEqual(response.status_code, 400)
        self.provider.assert_not_called()

    def test_reentrant_duplicate_post_cannot_dispatch_again(self):
        self.unlock()

        def verify(*args, **kwargs):
            self.pa.refresh_from_db()
            self.assertEqual(self.pa.state, PROCESSING)
            self.assertEqual(self.post(self.address()).status_code, 410)
            return {"success": True}

        self.provider.side_effect = verify
        self.assertEqual(self.post(self.address()).status_code, 200)
        self.provider.assert_called_once()

    def test_ambiguous_provider_error_cannot_be_replayed_or_echoed(self):
        self.unlock()
        self.provider.side_effect = RuntimeError("secret-document-PIN-12345678901")
        response = self.post(self.address())
        self.assertEqual(response.status_code, 503)
        self.assertNotContains(response, "12345678901", status_code=503)
        self.assert_security_headers(response)
        self.pa.refresh_from_db()
        self.assertEqual(self.pa.state, REVIEW)
        self.assertEqual(self.post(self.address()).status_code, 410)
        self.provider.assert_called_once()

    def test_backend_200_without_verified_state_is_not_success(self):
        self.unlock()
        with patch("whatsapp.verification_web._run_address_operation",
                   return_value=JsonResponse({"success": True})):
            response = self.post(self.address())
        self.assertEqual(response.status_code, 503)
        self.pa.refresh_from_db()
        self.assertEqual(self.pa.state, REVIEW)
        self.assertNotContains(response, "<h1>Address verified</h1>", status_code=503)

    def test_backend_email_gate_is_preserved(self):
        self.unlock()
        with patch("accounts.views._email_gate", return_value=JsonResponse({"message": "Verify email"}, status=403)):
            self.assertEqual(self.post(self.address()).status_code, 403)
        self.provider.assert_not_called()

    def test_bank_address_receives_structured_fields_and_bank_tier_is_authoritative(self):
        self.unlock()
        with patch("accounts.views.kyc_provider", return_value="wema"), \
                patch("accounts.views.wema.address_verify_live", return_value=True):
            page = self.get()
            self.assertContains(page, "No document upload is needed")
            self.assertNotContains(page, 'name="document"')
            data = self.address()
            data.pop("document")
            response = self.post(data)
        self.assertEqual(response.status_code, 200)
        self.bank_address.assert_called_once()
        account, address = self.bank_address.call_args.args
        self.assertEqual(account, "1234567890")
        for key, value in {"buildingNumber": "12", "street": "Palm Street", "lga": "Ikeja",
                           "city": "Ikeja", "state": "Lagos", "postalCode": "100001"}.items():
            self.assertEqual(address[key], value)
        self.user.refresh_from_db()
        self.assertEqual(self.user.wallet.bank_tier, 3)
        self.provider.assert_not_called()

    def test_document_fallback_requires_safe_image_even_when_bank_provider_selected(self):
        self.unlock()
        with patch("accounts.views.kyc_provider", return_value="wema"), \
                patch("accounts.views.wema.address_verify_live", return_value=False):
            self.assertContains(self.get(), 'name="document"')
            data = self.address()
            data.pop("document")
            self.assertEqual(self.post(data).status_code, 400)
            self.assertEqual(self.post(self.address(document=document(b"invalid"))).status_code, 400)
            self.provider.assert_not_called()
            self.assertEqual(self.post(self.address()).status_code, 200)
        self.provider.assert_called_once()
        self.bank_address.assert_not_called()

    def test_bank_rejection_never_requests_a_document_or_falls_back_to_document_provider(self):
        self.unlock()
        self.bank_address.return_value = {"success": False}
        data = self.address()
        data.pop("document")
        with patch("accounts.views.kyc_provider", return_value="wema"), \
                patch("accounts.views.wema.address_verify_live", return_value=True):
            response = self.post(data)
        self.assertEqual(response.status_code, 400)
        self.assertNotContains(response, 'name="document"', status_code=400)
        self.assertNotContains(response, "utility bill", status_code=400)
        self.bank_address.assert_called_once()
        self.provider.assert_not_called()

    def test_bank_to_document_rail_change_requires_proof_on_submit(self):
        self.unlock()
        with patch("accounts.views.kyc_provider", return_value="wema"), \
                patch("accounts.views.wema.address_verify_live", return_value=True):
            self.assertNotContains(self.get(), 'name="document"')
        data = self.address()
        data.pop("document")
        response = self.post(data)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'name="document"', status_code=400)
        self.provider.assert_not_called()
        self.bank_address.assert_not_called()

    def test_document_to_bank_rail_change_does_not_forward_unnecessary_upload(self):
        from accounts.views import verify_kyc_address

        self.unlock()
        self.assertContains(self.get(), 'name="document"')
        with patch("accounts.views.kyc_provider", return_value="wema"), \
                patch("accounts.views.wema.address_verify_live", return_value=True), \
                patch("accounts.views.verify_kyc_address", wraps=verify_kyc_address) as service:
            response = self.post(self.address(document=document(b"unneeded private photo")))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("document", service.call_args.args[1])
        self.provider.assert_not_called()
        self.bank_address.assert_called_once()

    def test_client_cannot_disable_document_proof(self):
        self.unlock()
        for field in ("proof_required", "bank_rail", "provider"):
            with self.subTest(field=field):
                data = self.address(**{field: "false"})
                data.pop("document")
                self.assertEqual(self.post(data).status_code, 400)
        self.provider.assert_not_called()
        self.bank_address.assert_not_called()

    def test_pending_bank_check_is_not_marked_verified_or_replayed(self):
        self.unlock()
        self.bank_address.return_value = {"success": True, "pending": True}
        with patch("accounts.views.kyc_provider", return_value="wema"), \
                patch("accounts.views.wema.address_verify_live", return_value=True):
            response = self.post(self.address())
        self.assertEqual(response.status_code, 202)
        self.assertContains(response, "Your address is being checked", status_code=202)
        self.user.refresh_from_db()
        self.assertFalse(self.user.address_verified)
        self.assertEqual(self.user.tier, 2)
        self.assertEqual(self.post(self.address()).status_code, 410)
        self.bank_address.assert_called_once()

    def test_router_cancel_preserves_bank_request_already_pending_review(self):
        from .router import handle_inbound

        self.unlock()
        self.bank_address.return_value = {"success": False, "pending": True}
        data = self.address()
        data.pop("document")
        with patch("accounts.views.kyc_provider", return_value="wema"), \
                patch("accounts.views.wema.address_verify_live", return_value=True):
            self.assertEqual(self.post(data).status_code, 202)
        with patch("whatsapp.router.reply") as reply:
            handle_inbound(MSISDN, "cancel")
        self.pa.refresh_from_db()
        self.assertEqual(self.pa.state, REVIEW)
        self.assertNotIn("Okay, cancelled", reply.call_args.args[1])
        self.bank_address.assert_called_once()

    @override_settings(RATELIMIT_ENABLE=True)
    def test_existing_address_operation_rate_limit_is_shared(self):
        from common.ratelimit import opaque_cache_identifier

        self.unlock()
        key = "rl:kyc_address:" + opaque_cache_identifier("kyc_address", "127.0.0.1")
        cache.set(key, 10, 600)
        self.assertEqual(self.post(self.address()).status_code, 429)
        self.provider.assert_not_called()

    def test_non_https_or_non_origin_config_cannot_issue_links(self):
        for base in ("http://testserver", "https://testserver/path", "https://user@host", ""):
            with self.subTest(base=base), override_settings(ZITCH_LINKS={"API_BASE": base}):
                with self.assertRaises(ImproperlyConfigured):
                    start_verification(self.user, MSISDN, 3)
