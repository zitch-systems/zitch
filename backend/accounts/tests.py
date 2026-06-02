"""Tests for auth onboarding, OTP hardening, and credential-setting security."""
import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from wallet.tests import make_user

from .models import OTP

User = get_user_model()


class OnboardingOtpTests(TestCase):
    def setUp(self):
        self.client = Client()

    def post(self, path, payload):
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    def test_onboarding_creates_user_and_token(self):
        self.post("/api/phone_verification/", {"phone": "08011112222", "email": "new@zitch.test"})
        otp = OTP.objects.filter(phone="08011112222").latest("created")
        res, body = self.post("/api/verify_otp/", {"phone": "08011112222", "otp": otp.code})
        self.assertEqual(res.status_code, 200)
        self.assertIn("access_token", body)
        self.assertTrue(User.objects.filter(phone="08011112222").exists())

    def test_otp_attempts_are_capped(self):
        OTP.objects.create(phone="08033334444", code="13579")
        for _ in range(OTP.MAX_ATTEMPTS):
            res, _ = self.post("/api/verify_otp/", {"phone": "08033334444", "otp": "00000"})
            self.assertEqual(res.status_code, 400)
        # Cap reached: even the correct code is refused now.
        res, _ = self.post("/api/verify_otp/", {"phone": "08033334444", "otp": "13579"})
        self.assertEqual(res.status_code, 429)
        self.assertFalse(User.objects.filter(phone="08033334444").exists())

    def test_correct_code_works_within_attempt_cap(self):
        OTP.objects.create(phone="08055556666", code="24680")
        for _ in range(OTP.MAX_ATTEMPTS - 1):
            self.post("/api/verify_otp/", {"phone": "08055556666", "otp": "00000"})
        res, body = self.post("/api/verify_otp/", {"phone": "08055556666", "otp": "24680"})
        self.assertEqual(res.status_code, 200)
        self.assertIn("access_token", body)

    def test_resend_is_rate_limited_then_allowed(self):
        self.post("/api/phone_verification/", {"phone": "08077778888", "email": "x@zitch.test"})
        res, _ = self.post("/api/resend_verify_otp/", {"phone": "08077778888"})
        self.assertEqual(res.status_code, 429)  # within cooldown
        # Age the code past the cooldown, then resend is allowed.
        OTP.objects.filter(phone="08077778888").update(
            created=timezone.now() - timedelta(seconds=OTP.RESEND_COOLDOWN_SECONDS + 5))
        res, _ = self.post("/api/resend_verify_otp/", {"phone": "08077778888"})
        self.assertEqual(res.status_code, 200)


class CredentialSecurityTests(TestCase):
    """The set-password / set-PIN endpoints must act on the authenticated user
    only — never on an arbitrary account identified by a body field."""

    def setUp(self):
        self.client = Client()

    def post(self, path, payload):
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    def test_set_password_requires_auth(self):
        res, _ = self.post("/api/set-password/", {"email": "victim@zitch.test", "password": "hunter2hunter"})
        self.assertEqual(res.status_code, 401)

    def test_set_password_cannot_target_another_account(self):
        victim, _ = make_user("08010000001", "victim@zitch.test")
        attacker, atk_token = make_user("08020000002", "atk@zitch.test")
        victim_hash = User.objects.get(pk=victim.pk).password
        # Even passing the victim's email, only the token owner's password changes.
        res, _ = self.post("/api/set-password/", {
            "access_token": atk_token, "email": "victim@zitch.test", "password": "newpass12345",
        })
        self.assertEqual(res.status_code, 200)
        self.assertEqual(User.objects.get(pk=victim.pk).password, victim_hash)  # untouched
        self.assertTrue(User.objects.get(pk=attacker.pk).check_password("newpass12345"))

    def test_set_password_min_length(self):
        _, token = make_user("08030000003", "c@zitch.test")
        res, _ = self.post("/api/set-password/", {"access_token": token, "password": "short"})
        self.assertEqual(res.status_code, 400)

    def test_set_pin_requires_auth_and_sets_owner(self):
        res, _ = self.post("/api/set-transaction-pin/", {"email": "x@zitch.test", "pin": "1357"})
        self.assertEqual(res.status_code, 401)
        user, token = make_user("08040000004", "d@zitch.test", pin="0000")
        res, _ = self.post("/api/set-transaction-pin/", {"access_token": token, "pin": "1357"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(User.objects.get(pk=user.pk).check_transaction_pin("1357"))

    def test_update_info_rejects_phone_collision_cleanly(self):
        make_user("08010000001", "a@zitch.test")
        _, token = make_user("08020000002", "b@zitch.test")
        res, body = self.post("/api/update_info/", {"access_token": token, "phone": "08010000001"})
        self.assertEqual(res.status_code, 400)  # clean error, not a 500 IntegrityError
        self.assertIn("phone", body["message"].lower())

    def test_update_info_name_change_does_not_trip_on_shared_email(self):
        # email isn't DB-unique; updating only the name while re-sending one's own
        # (here, a duplicated) email must not be blocked by the uniqueness guard.
        make_user("08010000001", "dup@zitch.test")
        _, token = make_user("08020000002", "dup@zitch.test")
        res, _ = self.post("/api/update_info/", {
            "access_token": token, "first_name": "Renamed", "email": "dup@zitch.test",
        })
        self.assertEqual(res.status_code, 200)


class KycTierTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08010000001", "a@zitch.test")

    def post(self, path, payload):
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    def test_bvn_plus_nin_promote_to_tier_3(self):
        self.post("/api/kyc/bvn/", {"access_token": self.token, "bvn": "12345678901"})
        res, body = self.post("/api/kyc/nin/", {"access_token": self.token, "nin": "10987654321"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(body["tier"], 3)

    def test_bvn_rejects_bad_format(self):
        res, _ = self.post("/api/kyc/bvn/", {"access_token": self.token, "bvn": "123"})
        self.assertEqual(res.status_code, 400)

    def test_face_verification_sets_durable_flag(self):
        res, body = self.post("/api/kyc/face/", {"access_token": self.token})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["face_verified"])
