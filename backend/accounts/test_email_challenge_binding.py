"""An inbox confirmation proves the exact contact that received its code."""
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from accounts.models import AccessToken, OTP, User


class EmailChallengeBindingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="email-bound", phone="08010000541", email="first@zitch.test",
            email_verified=False, phone_verified=True, bvn_verified=True,
        )
        self.token = AccessToken.issue(self.user).key

    def post(self, suffix, **data):
        return self.client.post(f"/api/email/verify/{suffix}/",
                                {"access_token": self.token, **data},
                                content_type="application/json")

    def issue(self, code="121212", email=None, **kwargs):
        return OTP.issue(self.user.phone, code, email=email or self.user.email,
                         purpose=OTP.EMAIL, **kwargs)

    def test_changing_email_during_cooldown_does_not_change_contact(self):
        self.issue()
        with patch("accounts.views.send_email") as send:
            response = self.post("start", email="second@zitch.test")
        self.assertEqual(response.status_code, 429)
        self.assertFalse(response.json().get("success", False))
        send.assert_not_called()
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "first@zitch.test")
        self.assertFalse(self.user.email_verified)

    def test_code_for_previous_email_cannot_verify_changed_contact(self):
        challenge = self.issue()
        User.objects.filter(pk=self.user.pk).update(email="second@zitch.test")
        response = self.post("confirm", otp="121212")
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        challenge.refresh_from_db()
        self.assertFalse(self.user.email_verified)
        self.assertFalse(challenge.used)

    def test_new_challenge_retires_old_inbox_code_and_verifies_current_contact(self):
        old = self.issue()
        OTP.objects.filter(pk=old.pk).update(created=timezone.now() - timedelta(seconds=21))
        with patch("accounts.views._otp_code", return_value="343434"), \
                patch("accounts.views.send_email", return_value={"success": True}) as send:
            response = self.post("start", email="Second@Zitch.Test")
        self.assertEqual(response.status_code, 200)
        send.assert_called_once()
        self.assertEqual(send.call_args.args[0], "second@zitch.test")
        old.refresh_from_db()
        self.assertTrue(old.used)
        self.assertEqual(self.post("confirm", otp="121212").status_code, 400)
        response = self.post("confirm", otp="343434")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)
        self.assertEqual(self.user.tier, 1)

    def test_a_consumed_new_challenge_does_not_reopen_older_code(self):
        self.issue()
        latest = self.issue(code="343434")
        latest.used = True
        latest.save(update_fields=["used"])
        response = self.post("confirm", otp="121212")
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertFalse(self.user.email_verified)

    def test_contact_confirmation_is_single_use(self):
        self.issue()
        self.assertEqual(self.post("confirm", otp="121212").status_code, 200)
        self.assertEqual(self.post("confirm", otp="121212").status_code, 400)

    def test_wrong_guesses_share_persisted_attempt_budget(self):
        challenge = self.issue()
        for _ in range(OTP.MAX_ATTEMPTS):
            self.assertEqual(self.post("confirm", otp="999999").status_code, 400)
        self.assertEqual(self.post("confirm", otp="121212").status_code, 429)
        challenge.refresh_from_db()
        self.assertEqual(challenge.attempts, OTP.MAX_ATTEMPTS)
        self.user.refresh_from_db()
        self.assertFalse(self.user.email_verified)

    def test_failed_delivery_does_not_report_sent_or_leave_usable_code(self):
        with patch("accounts.views._otp_code", return_value="121212"), \
                patch("accounts.views.send_email", return_value={"success": False}):
            response = self.post("start")
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json().get("success", False))
        self.assertTrue(OTP.objects.get(phone=self.user.phone).used)
        self.assertEqual(self.post("confirm", otp="121212").status_code, 400)

    def test_production_mock_delivery_cannot_be_reported_as_sent(self):
        with patch("accounts.views.send_email", return_value={"success": True, "mock": True}), \
                patch("accounts.views.mock_disabled_in_prod", return_value=True):
            response = self.post("start")
        self.assertEqual(response.status_code, 503)
        self.assertTrue(OTP.objects.get(phone=self.user.phone).used)

    def test_email_and_code_must_have_supported_types(self):
        for email in (123, "not-an-inbox@", "x\ny@zitch.test"):
            with self.subTest(email=email), patch("accounts.views.send_email") as send:
                self.assertEqual(self.post("start", email=email).status_code, 400)
                send.assert_not_called()
        self.issue()
        self.assertEqual(self.post("confirm", otp=121212).status_code, 400)

    def test_confirmation_uses_current_identity_flags_when_deriving_tier(self):
        self.issue()
        stale = User.objects.get(pk=self.user.pk)
        User.objects.filter(pk=self.user.pk).update(
            nin_verified=True, face_verified=True, address_verified=True,
        )
        from accounts.views import email_verify_confirm
        from django.test import RequestFactory
        request = RequestFactory().post("/api/email/verify/confirm/")
        request.user_obj, request.data = stale, {"otp": "121212"}
        # Call the service below transport/rate-limit/auth decorators to recreate
        # authentication resolving a user just before a concurrent KYC callback.
        from inspect import unwrap
        response = unwrap(email_verify_confirm)(request)
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.tier, 3)
