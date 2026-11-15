"""A sign-in from a device the account has never used needs a face check first.

The password alone no longer opens a session on a new phone: the server answers
with a one-time challenge, and tokens are issued only after a Prembly face check
for that account passes. The check never upgrades anyone, the challenge is bound
to the device that signed in, and it can be used once. Provider traffic is mocked.
"""
from datetime import timedelta
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import (
    IdentityProof, KnownDevice, LoginChallenge, PremblyFaceSession, User, record_identity_proof,
)

CONFIG = {"API_KEY": "secret-test", "FACE_WIDGET_KEY": "public-test", "FACE_WIDGET_ID": "face-only",
          "FACE_SDK_ENABLED": True, "ORGANISATION_ID": "org-test"}
PASSWORD = "Test-only-password1!"
OLD, NEW = "device-old-0001", "device-new-0002"


@override_settings(PREMBLY=CONFIG, RATELIMIT_ENABLE=False)
class NewDeviceSigninTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="newdev", phone="08011112222", email="newdev@example.test", password=PASSWORD,
            email_verified=True, phone_verified=True, bvn_verified=True, tier=1)
        self.user.set_bvn("12345678901")
        self.user.save()
        record_identity_proof(self.user, "bvn", "12345678901", source=IdentityProof.WEMA_FACE)
        KnownDevice.objects.create(user=self.user, device_id=OLD)
        self.enterContext(patch("requests.sessions.Session.request", side_effect=AssertionError("Unexpected network")))

    def post(self, path, body, device=NEW):
        headers = {"HTTP_X_ZITCH_DEVICE": device} if device else {}
        return self.client.post(path, body, content_type="application/json", **headers)

    def signin(self, device=NEW):
        return self.post("/api/sigin/", {"email_or_phone": "08011112222", "password": PASSWORD}, device)

    def start(self, challenge, device=NEW):
        response = Mock(status_code=201)
        response.json.return_value = {"status": True, "data": {"session": {"session_id": "sdk_login_12345"}}}
        with patch("utility.liveness.requests.post", return_value=response) as call:
            r = self.post("/api/signin/face/start/", {"challenge": challenge}, device)
        return r, call

    def attestation(self, purpose="login", passed=True):
        session = PremblyFaceSession.objects.get(user=self.user, purpose="login")
        return {"status": True, "data": {"session_id": "sdk_login_12345",
            "widget_config": {"id": "face-only", "enable_face_scan": True,
                "use_gesture_liveness": True, "metadata": {"image_comparison": True}},
            "status": "completed" if passed else "failed", "overall_result": "passed" if passed else "failed",
            "completed_at": timezone.now().isoformat(), "failure_reason": None,
            "metadata": {"zitch_action": str(session.state), "purpose": purpose},
            "verification_data": {"number": "12345678901"},
            "liveness": {"enabled": True, "status": "completed", "is_live": True,
                         "process_id": "live-provider-123", "error": None},
            "face_scan_result": {"comparison_result": [{"result": {
                "status": True, "response_code": "00", "confidence": 99.9}}]}}}

    def status(self, challenge, session, body, device=NEW):
        response = Mock(status_code=200)
        response.json.return_value = body
        with patch("utility.liveness.requests.get", return_value=response):
            return self.post("/api/signin/face/status/", {"challenge": challenge, "session": session}, device)

    def test_known_device_signs_in_as_before(self):
        r = self.signin(OLD)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["access_token"])

    def test_new_device_gets_a_challenge_and_no_tokens(self):
        body = self.signin().json()
        self.assertEqual(body["code"], "new_device_face_required")
        self.assertFalse(body.get("success"))
        self.assertNotIn("access_token", body)
        self.assertNotIn("refresh_token", body)
        self.assertTrue(body["challenge"])
        self.assertFalse(KnownDevice.objects.filter(user=self.user, device_id=NEW).exists())
        # only the hash is stored
        self.assertFalse(LoginChallenge.objects.filter(key=body["challenge"]).exists())

    def test_a_missing_device_header_is_a_new_device(self):
        self.assertEqual(self.signin(device=None).json()["code"], "new_device_face_required")

    def test_passing_the_face_check_signs_in_remembers_the_device_and_upgrades_nobody(self):
        challenge = self.signin().json()["challenge"]
        r, call = self.start(challenge)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(call.call_args.kwargs["json"]["metadata"]["purpose"], "login")
        session = r.json()["session"]
        r = self.status(challenge, session, self.attestation())
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()["access_token"])
        self.assertTrue(KnownDevice.objects.filter(user=self.user, device_id=NEW).exists())
        self.user.refresh_from_db()
        self.assertFalse(self.user.face_verified)
        self.assertEqual(self.user.tier, 1)
        # used once
        again = self.status(challenge, session, self.attestation())
        self.assertEqual(again.status_code, 401)

    def test_a_tier2_attestation_cannot_open_a_sign_in(self):
        challenge = self.signin().json()["challenge"]
        session = self.start(challenge)[0].json()["session"]
        r = self.status(challenge, session, self.attestation(purpose="tier2_liveness"))
        self.assertNotIn("access_token", r.json())

    def test_challenge_is_bound_to_the_device_that_signed_in(self):
        challenge = self.signin().json()["challenge"]
        r, call = self.start(challenge, device="someone-else-0003")
        self.assertEqual(r.status_code, 401)
        call.assert_not_called()

    def test_failed_checks_use_up_the_challenge(self):
        challenge = self.signin().json()["challenge"]
        session = self.start(challenge)[0].json()["session"]
        for _ in range(LoginChallenge.MAX_FAILED_CHECKS):
            PremblyFaceSession.objects.filter(state=session).update(status="pending")
            self.status(challenge, session, self.attestation(passed=False))
        self.assertEqual(self.start(challenge)[0].status_code, 401)

    def test_expired_challenge_is_refused(self):
        challenge = self.signin().json()["challenge"]
        LoginChallenge.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.start(challenge)[0].status_code, 401)

    def test_accounts_that_cannot_be_face_matched_sign_in_as_before(self):
        IdentityProof.objects.filter(user=self.user).delete()
        self.assertTrue(self.signin().json()["access_token"])

    def test_first_device_on_an_account_is_not_challenged(self):
        KnownDevice.objects.filter(user=self.user).delete()
        self.assertTrue(self.signin().json()["access_token"])

    @override_settings(PREMBLY={**CONFIG, "FACE_SDK_ENABLED": False})
    def test_face_provider_off_signs_in_as_before(self):
        self.assertTrue(self.signin().json()["access_token"])

    def reset(self, device=NEW):
        from accounts.models import OTP

        OTP.issue(phone=self.user.phone, code="246810", email=self.user.email, purpose=OTP.RESET)
        return self.post("/api/password/reset/", {"email_or_phone": self.user.phone, "otp": "246810",
                                                   "password": "Another-test-pass2!"}, device)

    def test_a_password_reset_on_a_new_device_also_waits_for_the_face_check(self):
        body = self.reset().json()
        self.assertEqual(body["code"], "new_device_face_required")
        self.assertNotIn("access_token", body)
        self.assertNotIn("refresh_token", body)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Another-test-pass2!"))
        # and that challenge opens the session once the face check passes
        session = self.start(body["challenge"])[0].json()["session"]
        r = self.status(body["challenge"], session, self.attestation())
        self.assertTrue(r.json()["access_token"])

    def test_a_password_reset_on_a_known_device_signs_in_as_before(self):
        self.assertTrue(self.reset(OLD).json()["access_token"])

