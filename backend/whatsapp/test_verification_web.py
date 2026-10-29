"""Real browser CSRF/PIN gates with a test-only capable service; no live calls."""
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
from banking.test_client import CONFIG as BANK_CONFIG
from banking.test_fixtures import bind_test_bank
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
    NINEPSB=BANK_CONFIG, ZITCH_LINKS={"API_BASE": ORIGIN}, SECURE_SSL_REDIRECT=False,
    CSRF_COOKIE_SECURE=True, RATELIMIT_ENABLE=False,
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    BANK_ACCOUNT_PROVIDER="partnership", WEMA_PARTNERSHIP_MODE="active",
)
class VerificationWebTests(TestCase):
    def setUp(self):
        cache.clear()
        # Exercise the portal security contract with a capable adapter fixture.
        # Explicit unavailable tests below prove that the production gate blocks
        # collection and dispatch while the provider contract is incomplete.
        self.capability = self.enterContext(patch(
            "wallet.address_verification.tier3_address_capability", return_value={
                "tier3_address_available": True, "tier3_address_unavailable_reason": ""}))
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
        self.face = self.enterContext(patch("wallet.views.verify_tier2_liveness"))
        self.tier2 = self.enterContext(patch("wallet.views.wema_provider.upgrade_tier2"))
        self.provider = self.enterContext(patch("accounts.views.kyc_verify_address",
                                                return_value={"success": True}))
        self.enterContext(patch("accounts.views.kyc_provider", return_value="prembly"))
        self.enterContext(patch("accounts.views._sync_wema_tier3"))
        self.bank_address = self.enterContext(patch("accounts.views.wema.upgrade_tier3",
                                                   return_value={"success": True}))
        self.bank_status = self.enterContext(patch("utility.wema.get_kyc_status", return_value={
            "success": True, "tier": 2, "address_verification": "pending"}))
        # The shared production service intentionally refuses new requests until
        # correlated bank evidence exists. Simulate a completed service result
        # only for these transport/security tests; production-gate tests below
        # explicitly verify that no service is invoked.
        from accounts.views import verify_kyc_address
        self.production_address_service = verify_kyc_address
        self.service = self.enterContext(patch(
            "accounts.views.verify_kyc_address", side_effect=self.complete_address))
        bind_test_bank(self.user, tier=2)
        self.path = self.start()

    @staticmethod
    def complete_address(user, data):
        user.address_verified = True
        user.recompute_tier()
        user.save(update_fields=["address_verified", "tier"])
        return JsonResponse({"success": True})

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
                                   (self.user, MSISDN, 4), (self.user, MSISDN, True)):
            with self.subTest(user=user.pk, number=number, tier=tier), self.assertRaises(ValueError):
                start_verification(user, number, tier)
        self.link.status = WhatsAppLink.PENDING
        self.link.save()
        with self.assertRaises(ValueError):
            start_verification(self.user, MSISDN, 3)

    def test_disabled_partnership_and_archive_refuse_new_verification_capabilities(self):
        count = PendingAction.objects.count()
        for settings_change in ({"NINEPSB": {**BANK_CONFIG, "ENABLED": False}},
                                {"NINEPSB": {**BANK_CONFIG, "UAT_APPROVED": False}}):
            with self.subTest(settings=settings_change), override_settings(**settings_change):
                for tier in (2, 3):
                    with self.subTest(tier=tier), self.assertRaises(ValueError):
                        start_verification(self.user, MSISDN, tier)
        self.assertEqual(PendingAction.objects.count(), count)
        self.pa.refresh_from_db()
        self.assertEqual(self.pa.state, PIN)

    def test_lifecycle_change_blocks_unsubmitted_pin_and_address_forms(self):
        for settings_change in ({"NINEPSB": {**BANK_CONFIG, "ENABLED": False}},
                                {"NINEPSB": {**BANK_CONFIG, "UAT_APPROVED": False}}):
            for unlocked in (False, True):
                with self.subTest(settings=settings_change, unlocked=unlocked):
                    self.path = self.start()
                    self.unlock() if unlocked else self.get()
                    with override_settings(**settings_change), \
                            patch("whatsapp.verification_web._address_data") as parse, \
                            patch("whatsapp.verification_web.evaluate_transaction_pin") as pin:
                        response = self.get()
                        self.assertEqual(response.status_code, 410)
                        self.assertNotContains(response, 'name="document"', status_code=410)
                        self.assertEqual(self.post({"action": "pin", "pin": GOOD_PIN}).status_code, 410)
                        self.assertEqual(self.post(self.address()).status_code, 410)
                    parse.assert_not_called()
                    pin.assert_not_called()
                    self.pa.refresh_from_db()
                    self.assertEqual(self.pa.state, READY if unlocked else PIN)
        self.provider.assert_not_called()
        self.bank_address.assert_not_called()


    def test_archive_does_not_rewrite_submitted_verification_records(self):
        self.get()  # Establish CSRF before attempting already-consumed links.
        original_payload = dict(self.pa.payload)
        for state in (PROCESSING, REVIEW, COMPLETE):
            with self.subTest(state=state):
                PendingAction.objects.filter(pk=self.pa.pk).update(state=state)
                with override_settings(NINEPSB={**BANK_CONFIG, "ENABLED": False}):
                    self.assertEqual(self.get().status_code, 410)
                    self.assertEqual(self.post(self.address()).status_code, 410)
                self.pa.refresh_from_db()
                self.assertEqual(self.pa.state, state)
                self.assertEqual(self.pa.payload, original_payload)
        self.provider.assert_not_called()
        self.bank_address.assert_not_called()

    def test_accepted_operation_keeps_its_pending_result_after_disable(self):
        self.unlock()
        def accepted(user, action, data, files, scope):
            self.pa.refresh_from_db()
            self.assertEqual(self.pa.state, PROCESSING)
            self.enterContext(override_settings(NINEPSB={**BANK_CONFIG, "ENABLED": False}))
            return {"pending": True, "identity_reference": "bank-owned-reference"}
        with patch("banking.web_verification.submit", side_effect=accepted) as provider:
            self.assertEqual(self.post({"action": "identity"}).status_code, 410)
        self.pa.refresh_from_db()
        self.assertEqual(self.pa.state, PROCESSING)
        self.assertEqual(self.get().status_code, 410)
        provider.assert_called_once()

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
        self.assertContains(self.get(), 'name="identifier"')
        with patch("banking.web_verification.submit", return_value={"pending": True}):
            self.assertEqual(self.post({"action": "identity_status"}).status_code, 200)

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
        def verifying(*args):
            with patch("whatsapp.router.reply") as reply:
                handle_inbound(MSISDN, "cancel")
            self.pa.refresh_from_db()
            self.assertEqual(self.pa.state, PROCESSING)
            self.assertIn("processing", reply.call_args.args[1])
            return {"pending": True}
        with patch("banking.web_verification.submit", side_effect=verifying) as provider:
            self.assertEqual(self.post({"action": "identity"}).status_code, 200)
        self.pa.refresh_from_db()
        self.assertEqual(self.pa.state, READY)
        provider.assert_called_once()

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
        self.assertContains(self.get(), 'name="identifier"')
        self.assertFalse(AccessToken.objects.exists())

    def test_fetch_pin_redirect_has_refreshed_form_csrf_and_no_referer_required(self):
        response = self.unlock()
        page = self.get(response["Location"])
        csrf_value = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.content.decode()).group(1)
        with patch("banking.web_verification.submit", return_value={"pending": True}):
            response = self.post({"action": "identity_status", "csrfmiddlewaretoken": csrf_value},
                csrf=False, HTTP_SEC_FETCH_MODE="cors", HTTP_SEC_FETCH_SITE="same-origin")
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







    def test_unrecognized_operation_or_fields_cannot_form_an_api_proxy(self):
        self.unlock()
        for action in ("transfer", "face", "kyc_face_start", "wema_wallet_upgrade_tier2", "/api/kyc/address/"):
            self.assertEqual(self.post({"action": action}).status_code, 400)
        for field, value in (("user_id", "2"), ("access_token", "secret"),
                             ("address_verified", "true"), ("url", "https://evil.example")):
            self.assertEqual(self.post(self.address(**{field: value})).status_code, 400)
        self.provider.assert_not_called()


    def test_reentrant_duplicate_post_cannot_dispatch_again(self):
        self.unlock()
        def verifying(*args):
            self.pa.refresh_from_db()
            self.assertEqual(self.pa.state, PROCESSING)
            self.assertEqual(self.post({"action": "identity"}).status_code, 410)
            return {"pending": True}
        with patch("banking.web_verification.submit", side_effect=verifying) as service:
            self.assertEqual(self.post({"action": "identity"}).status_code, 200)
        service.assert_called_once()

    def test_ambiguous_provider_error_cannot_be_replayed_or_echoed(self):
        self.unlock()
        with patch("banking.web_verification.submit", side_effect=RuntimeError("secret-document-12345678901")) as service:
            response = self.post({"action": "identity"})
            self.assertEqual(response.status_code, 503)
            self.assertNotContains(response, "12345678901", status_code=503)
            self.assert_security_headers(response)
            self.pa.refresh_from_db()
            self.assertEqual(self.pa.state, REVIEW)
            self.assertEqual(self.post({"action": "identity"}).status_code, 410)
        service.assert_called_once()















    def test_pending_status_cannot_be_read_after_credential_revocation(self):
        self.unlock()
        with patch("banking.web_verification.submit", return_value={"pending": True, "identity_reference": "bound-reference"}):
            self.assertEqual(self.post({"action": "identity"}).status_code, 200)
        User.objects.filter(pk=self.user.pk).update(password="changed")
        self.assertEqual(self.post({"action": "identity_status"}).status_code, 410)
        self.assertEqual(self.get().status_code, 410)

    def test_status_readback_rechecks_credentials_after_the_bank_call(self):
        self.unlock()
        def revoke(user, *args):
            User.objects.filter(pk=user.pk).update(password="changed-during-readback")
            return {"pending": True}
        with patch("banking.web_verification.submit", side_effect=revoke):
            self.assertEqual(self.post({"action": "identity_status"}).status_code, 410)


    @override_settings(RATELIMIT_ENABLE=True)
    def test_existing_identity_operation_rate_limit_is_shared(self):
        from common.ratelimit import opaque_cache_identifier
        self.unlock()
        key = "rl:bank_identity_start:" + opaque_cache_identifier("bank_identity_start", "127.0.0.1")
        cache.set(key, 5, 600)
        with patch("banking.web_verification.submit") as provider:
            self.assertEqual(self.post({"action": "identity"}).status_code, 429)
        provider.assert_not_called()

    def test_non_https_or_non_origin_config_cannot_issue_links(self):
        for base in ("http://testserver", "https://testserver/path", "https://user@host", ""):
            with self.subTest(base=base), override_settings(ZITCH_LINKS={"API_BASE": base}):
                with self.assertRaises(ImproperlyConfigured):
                    start_verification(self.user, MSISDN, 3)
