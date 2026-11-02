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
        # BVN is the default ID, and the long form is split by a Next button.
        self.assertContains(page, 'name="kind" value="bvn" checked')
        self.assertNotContains(page, 'name="kind" value="nin" checked')
        self.assertContains(page, 'id="next"')
        # No test-mode banners or narration on the customer page.
        self.assertNotContains(page, "Test mode")
        self.assertNotContains(page, "Step 1 of 2")
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

        # The address must be verified first; only then does the Tier 3 form appear.
        page = self.open_tier(3)
        self.assertContains(page, "Upgrade to Tier 3")
        self.assertContains(page, "Verify your home address")
        self.assertNotContains(page, 'name="tier" value="3"')
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
        self.assertNotContains(page, "NIN-only account")
        self.assertNotContains(page, "required by bank")
        # Date of birth opens the number keypad and is masked to DD/MM/YYYY.
        self.assertContains(page, 'inputmode="numeric"')
        self.assertContains(page, 'data-mask="date"')

    def details(self, **changes):
        from .test_identity import PROFILE
        return {"action": "identity", "kind": "bvn", "identifier": "12345678901", "method": "FACIAL",
                **{key: PROFILE[key] for key in ("lastName", "otherNames", "phoneNo", "dateOfBirth", "address")},
                "gender": "1", **changes}

    def photo(self):
        from io import BytesIO
        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image
        raw = BytesIO()
        Image.new("RGB", (8, 8), "white").save(raw, "PNG")
        return SimpleUploadedFile("face.png", raw.getvalue(), content_type="image/png")

    def test_face_verification_opens_on_its_own_page_after_the_details(self):
        from whatsapp.models import PendingAction
        from .client import Reply
        from .models import IdentityChallenge
        page = self.open_tier(1)
        # The details page offers face verification but takes no photo itself.
        self.assertContains(page, 'value="FACIAL"')
        self.assertNotContains(page, 'name="face"')
        self.assertNotContains(page, 'capture=')
        with patch("banking.client.Client.initiate_identity") as send:
            page = self.post(self.details())
        self.assertEqual(page.status_code, 200)
        send.assert_not_called()
        self.assertContains(page, 'href="?step=face"')
        self.assertContains(page, "Open face verification")
        sealed = PendingAction.objects.get(pk=PendingAction.objects.latest("pk").pk).payload["face_request"]
        self.assertNotIn("12345678901", sealed)
        self.assertFalse(IdentityChallenge.objects.exists())

        face = self.browser.get(self.path + "?step=face", secure=True)
        self.assertContains(face, "Verify your face")
        self.assertContains(face, 'capture="user"')
        self.assertContains(face, 'value="face"')
        self.assertNotContains(face, 'name="identifier"')
        policy = face["Content-Security-Policy"]
        self.assertIn("img-src blob:", policy)
        self.assertNotIn("unsafe-inline", policy)
        self.assertNotIn("img-src *", policy)

        # Without a photo the customer stays on the face page.
        with patch("banking.client.Client.initiate_identity") as send:
            missing = self.post({"action": "face"})
        send.assert_not_called()
        self.assertEqual(missing.status_code, 400)
        self.assertContains(missing, "Take your photo to continue", status_code=400)
        self.assertContains(missing, 'capture="user"', status_code=400)

        reply = Reply({"status": "SUCCESS", "message": "PENDING"}, 200)
        with patch("banking.client.Client.initiate_identity", return_value=reply) as send:
            done = self.post({"action": "face", "face": self.photo()})
        self.assertEqual(done.status_code, 200)
        send.assert_called_once()
        self.assertEqual(send.call_args.kwargs["method"], "FACIAL")
        self.assertTrue(send.call_args.kwargs["image"])
        self.assertEqual(send.call_args.args[1:3], ("bvn", "12345678901"))
        payload = PendingAction.objects.latest("pk").payload
        self.assertNotIn("face_request", payload)
        self.assertEqual(payload["identity_reference"], IdentityChallenge.objects.get().reference)
        self.assertNotContains(done, 'href="?step=face"')

    def test_face_page_needs_confirmed_details_and_a_code_clears_them(self):
        from whatsapp.models import PendingAction
        from .client import Reply
        self.open_tier(1)
        # Opening the face page directly shows the details form instead.
        page = self.browser.get(self.path + "?step=face", secure=True)
        self.assertNotContains(page, 'capture="user"')
        self.assertContains(page, 'name="identifier"')
        with patch("banking.client.Client.initiate_identity") as send:
            refused = self.post({"action": "face", "face": self.photo()})
        send.assert_not_called()
        self.assertContains(refused, "Enter your details first", status_code=400)

        with patch("banking.client.Client.initiate_identity"):
            self.post(self.details())
        self.assertIn("face_request", PendingAction.objects.latest("pk").payload)
        reply = Reply({"status": "SUCCESS", "message": "PENDING"}, 200)
        with patch("banking.client.Client.initiate_identity", return_value=reply) as send:
            self.post(self.details(method="OTP"))
        send.assert_called_once()
        self.assertNotIn("face_request", PendingAction.objects.latest("pk").payload)

    def test_held_face_details_expire_with_the_link_and_are_erased_by_a_new_one(self):
        import json
        from datetime import timedelta
        from whatsapp.models import PendingAction
        from .secrets import _keys, unseal
        self.open_tier(1)
        with patch("banking.client.Client.initiate_identity"):
            self.post(self.details())
        old = PendingAction.objects.latest("pk")
        self.assertIn("face_request", old.payload)
        # Details sealed more than 15 minutes ago can no longer be used.
        held = unseal(old.payload["face_request"])
        stale = _keys()[0].encrypt_at_time(json.dumps(held).encode(),
                                           int((timezone.now() - timedelta(minutes=16)).timestamp())).decode()
        PendingAction.objects.filter(pk=old.pk).update(payload={**old.payload, "face_request": stale})
        with patch("banking.client.Client.initiate_identity") as send:
            refused = self.post({"action": "face", "face": self.photo()})
        send.assert_not_called()
        self.assertContains(refused, "Enter your details first", status_code=400)
        # A new link ends the old one and erases what it held.
        start_verification(self.user, self.msisdn, 1)
        old.refresh_from_db()
        self.assertNotIn("face_request", old.payload)

    def test_an_unknown_gender_value_gets_the_gender_message(self):
        self.open_tier(1)
        with patch("banking.client.Client.initiate_identity") as send:
            page = self.post(self.details(gender="abc", method="OTP"))
        send.assert_not_called()
        self.assertContains(page, "Select your gender", status_code=400)

    def test_details_survive_an_error_but_the_id_number_is_not_echoed(self):
        self.open_tier(1)
        with patch("banking.client.Client.initiate_identity") as send:
            page = self.post(self.details(dateOfBirth="31/02/1990", kind="nin", ninUserId=""))
        send.assert_not_called()
        self.assertEqual(page.status_code, 400)
        self.assertContains(page, 'value="31/02/1990"', status_code=400)
        self.assertContains(page, 'value="1 Test Road"', status_code=400)
        self.assertContains(page, 'name="kind" value="nin" checked', status_code=400)
        self.assertContains(page, 'name="method" value="FACIAL" checked', status_code=400)
        self.assertNotContains(page, "12345678901", status_code=400)

    def test_unreadable_or_future_birth_dates_get_a_clear_message(self):
        from banking.validation import wallet_profile
        from .test_identity import PROFILE
        for value in ("1990-01-01", "31/02/1990", "01/01/2999", "abc"):
            with self.subTest(value=value), self.assertRaisesMessage(ValueError, "Enter a valid date of birth as DD/MM/YYYY"):
                wallet_profile({**PROFILE, "dateOfBirth": value})
        self.assertEqual(wallet_profile(PROFILE)["dateOfBirth"], "01/01/1990")
