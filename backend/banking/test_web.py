from urllib.parse import urlsplit
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, Client, override_settings
from django.utils import timezone

from accounts.models import AccessToken
from whatsapp.models import WhatsAppLink
from whatsapp.verification_web import start_verification
from .test_identity import IdentityTests
from .test_client import CONFIG


@override_settings(NINEPSB=CONFIG, ZITCH_LINKS={"API_BASE": "https://testserver"}, SECURE_SSL_REDIRECT=False,
    CSRF_COOKIE_SECURE=True, RATELIMIT_ENABLE=False, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class WebTests(TestCase):
    def setUp(self):
        IdentityTests.setUp(self)
        self.user.set_transaction_pin("582940")
        self.user.save()
        self.msisdn = "2347041267880"
        self.link = WhatsAppLink.objects.create(user=self.user, wa_msisdn=self.msisdn,
            status=WhatsAppLink.ACTIVE, linked_at=timezone.now())
        self.path = urlsplit(start_verification(self.user, self.msisdn, 1)).path
        self.browser = Client(enforce_csrf_checks=True)

    def post(self, body, *, csrf=True):
        headers = {"HTTP_ORIGIN": "https://testserver"}
        if csrf and settings.CSRF_COOKIE_NAME in self.browser.cookies:
            headers["HTTP_X_CSRFTOKEN"] = self.browser.cookies[settings.CSRF_COOKIE_NAME].value
        return self.browser.post(self.path, body, secure=True, **headers)

    def unlock(self):
        self.assertEqual(self.browser.get(self.path, secure=True).status_code, 200)
        self.assertEqual(self.post({"action": "pin", "pin": "582940"}).status_code, 303)

    def test_no_identity_submission_before_pin_or_without_csrf(self):
        self.browser.get(self.path, secure=True)
        with patch("banking.web_verification.submit") as submit:
            self.assertEqual(self.post({"action": "identity"}).status_code, 403)
            self.assertEqual(self.post({"action": "pin", "pin": "582940"}, csrf=False).status_code, 403)
        submit.assert_not_called()
        self.assertFalse(AccessToken.objects.exists())

    def test_grant_is_bound_to_owner_and_has_no_general_access_token(self):
        self.unlock()
        with patch("banking.web_verification.submit", return_value={"pending": True, "identity_reference": "bound-ref"}) as submit:
            response = self.post({"action": "identity"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(submit.call_args.args[0].pk, self.user.pk)
        self.assertFalse(AccessToken.objects.exists())
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["Referrer-Policy"], "no-referrer")

    def test_client_authentication_and_unrecognized_operations_cannot_reach_adapter(self):
        self.unlock()
        with patch("banking.web_verification.submit") as submit:
            for body in ({"action": "transfer"}, {"action": "identity", "user_id": "99999"},
                         {"action": "identity", "access_token": "attacker-token"},
                         {"action": "identity", "url": "https://evil.example"}):
                self.assertEqual(self.post(body).status_code, 400)
        submit.assert_not_called()

    def test_reentrant_post_and_chat_cancel_cannot_duplicate_bank_operation(self):
        from whatsapp.models import PendingAction
        from whatsapp import router
        from whatsapp.verification_web import PROCESSING, READY
        self.unlock()

        def submit(*args):
            self.assertEqual(PendingAction.objects.get().state, PROCESSING)
            self.assertEqual(self.post({"action": "identity"}).status_code, 410)
            with patch.object(router, "reply"):
                router.handle_inbound(self.msisdn, "cancel")
            self.assertTrue(PendingAction.objects.exists())
            return {"pending": True, "identity_reference": "bound-ref"}

        with patch("banking.web_verification.submit", side_effect=submit) as adapter:
            self.assertEqual(self.post({"action": "identity"}).status_code, 200)
        adapter.assert_called_once()
        self.assertEqual(PendingAction.objects.get().state, READY)

    def test_unknown_service_exception_is_redacted_and_cannot_be_resubmitted(self):
        self.unlock()
        with patch("banking.web_verification.submit", side_effect=RuntimeError("private-identity-12345678901")) as submit:
            response = self.post({"action": "identity"})
            self.assertEqual(response.status_code, 503)
            self.assertNotIn(b"12345678901", response.content)
            self.assertIn("no-store", response["Cache-Control"])
            self.assertEqual(self.post({"action": "identity"}).status_code, 410)
        submit.assert_called_once()

    @override_settings(RATELIMIT_ENABLE=True)
    def test_browser_cannot_bypass_the_api_identity_request_budget(self):
        from django.core.cache import cache
        from common.ratelimit import opaque_cache_identifier
        self.unlock()
        key = "rl:bank_identity_start:" + opaque_cache_identifier("bank_identity_start", "127.0.0.1")
        cache.set(key, 5, 600)
        with patch("banking.web_verification.submit") as submit:
            response = self.post({"action": "identity"})
        self.assertEqual(response.status_code, 429)
        self.assertIn("no-store", response["Cache-Control"])
        submit.assert_not_called()

    def test_pin_change_revokes_existing_browser_grant(self):
        self.unlock()
        self.user.set_transaction_pin("731952")
        self.user.save()
        with patch("banking.web_verification.submit") as submit:
            self.assertEqual(self.post({"action": "identity_status"}).status_code, 410)
        submit.assert_not_called()

    def test_copying_link_does_not_copy_browser_proof(self):
        self.unlock()
        other = Client(enforce_csrf_checks=True)
        response = other.get(self.path, secure=True)
        self.assertContains(response, "transaction PIN")
        self.assertNotContains(response, 'name="identifier"')

    def test_tampered_link_is_closed(self):
        self.assertEqual(self.browser.get(self.path[:-2]+"xx", secure=True).status_code, 410)

    def open_tier(self, tier):
        self.path = urlsplit(start_verification(self.user, self.msisdn, tier)).path
        self.browser = Client(enforce_csrf_checks=True)
        self.unlock()
        return self.browser.get(self.path, secure=True)

    def test_each_link_shows_only_its_own_tier(self):
        page = self.open_tier(1)
        self.assertContains(page, "Open your Zitch account")
        self.assertContains(page, 'name="identifier"')
        self.assertContains(page, 'name="kind" value="nin" checked')
        self.assertNotContains(page, 'value="upgrade"')
        self.assertNotContains(page, 'value="address"')

        page = self.open_tier(2)
        self.assertContains(page, "Upgrade to Tier 2")
        self.assertContains(page, '<input type="hidden" name="tier" value="2">', html=True)
        self.assertNotContains(page, 'name="identifier"')
        self.assertNotContains(page, 'value="address"')
        self.assertNotContains(page, 'name="tier" value="3"')
        # What Zitch already holds is prefilled; only new details are typed.
        self.assertContains(page, 'value="jane@example.test"')

        page = self.open_tier(3)
        self.assertContains(page, "Upgrade to Tier 3")
        self.assertContains(page, '<input type="hidden" name="tier" value="3">', html=True)
        self.assertNotContains(page, 'name="identifier"')
        self.assertNotContains(page, 'name="tier" value="2"')

    def test_customer_page_never_names_partners_and_explains_the_nimc_id(self):
        for tier in (1, 2, 3):
            with self.subTest(tier=tier):
                page = self.open_tier(tier).content.decode()
                for partner in ("9PSB", "9psb", "Prembly"):
                    self.assertNotIn(partner, page)
        page = self.open_tier(1)
        self.assertContains(page, "NIMC User ID")
        self.assertContains(page, "not your 11-digit NIN")
        self.assertNotContains(page, "NIN-only account")
        self.assertNotContains(page, "required by bank")
        # Date of birth opens the number keypad and is masked to DD/MM/YYYY.
        self.assertContains(page, 'inputmode="numeric"')
        self.assertContains(page, 'data-mask="date"')

    def test_face_step_is_offered_and_its_preview_is_allowed_only_from_the_device(self):
        page = self.open_tier(1)
        self.assertContains(page, 'id="face-step"')
        self.assertContains(page, 'capture="user"')
        policy = page["Content-Security-Policy"]
        self.assertIn("img-src blob:", policy)
        self.assertNotIn("unsafe-inline", policy)
        self.assertNotIn("img-src *", policy)

    def test_face_method_without_a_selfie_asks_for_one(self):
        from django.utils.datastructures import MultiValueDict
        from banking.web_verification import submit
        with patch("banking.accounts.require_enabled"), patch("banking.accounts.start_identity") as start, \
                self.assertRaisesMessage(ValueError, "Take a selfie to continue"):
            submit(self.user, "identity", {"method": "FACIAL", "kind": "nin", "identifier": "12345678901"},
                   MultiValueDict(), {})
        start.assert_not_called()

    def test_unreadable_or_future_birth_dates_get_a_clear_message(self):
        from banking.validation import wallet_profile
        from .test_identity import PROFILE
        for value in ("1990-01-01", "31/02/1990", "01/01/2999", "abc"):
            with self.subTest(value=value), self.assertRaisesMessage(ValueError, "Enter a valid date of birth as DD/MM/YYYY"):
                wallet_profile({**PROFILE, "dateOfBirth": value})
        self.assertEqual(wallet_profile(PROFILE)["dateOfBirth"], "01/01/1990")
