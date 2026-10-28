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
            response = self.post({"action": "identity", "user_id": "99999"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(submit.call_args.args[0].pk, self.user.pk)
        self.assertFalse(AccessToken.objects.exists())
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["Referrer-Policy"], "no-referrer")

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
