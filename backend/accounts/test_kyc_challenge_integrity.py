"""Identity confirmations preserve expiry and current verification state."""
import time
from inspect import unwrap
from unittest.mock import patch

from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from accounts import views
from accounts.models import OTP, User


@override_settings(BANK_ACCOUNT_PROVIDER="partnership", WEMA_PARTNERSHIP_MODE="active",
                   DEBUG=True, TESTING=True)
class KycChallengeIntegrityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="kyc-integrity", phone="08010000781",
            email="integrity@example.test", email_verified=True, phone_verified=True)
        self.raw = "12345678901"
        self.key = f"kyc_identity:bvn:{self.user.pk}"

    def pending(self, **changes):
        value = {"code_hash": OTP.hash_code("724915"),
                 "identity": views._pending_identity_encrypt("bvn", self.raw),
                 "verified_name": "", "attempts": 0,
                 "expires_at": timezone.now().timestamp() + 600}
        value.update(changes)
        cache.set(self.key, value, 600)
        return value

    def test_wrong_guess_does_not_extend_absolute_expiry(self):
        pending = self.pending(expires_at=timezone.now().timestamp() + 60)
        with patch.object(cache, "set", wraps=cache.set) as store:
            raw, error = views._confirm_identity_ownership_challenge(self.user, "bvn", "000000")
        self.assertIsNone(raw)
        self.assertEqual(error.status_code, 400)
        self.assertLessEqual(store.call_args.args[2], 60)
        self.assertEqual(cache.get(self.key)["expires_at"], pending["expires_at"])

    def test_expired_challenge_in_cache_cannot_verify(self):
        self.pending(expires_at=timezone.now().timestamp() - 1)
        raw, error = views._confirm_identity_ownership_challenge(self.user, "bvn", "724915")
        self.assertIsNone(raw)
        self.assertEqual(error.status_code, 400)
        self.assertIsNone(cache.get(self.key))

    def test_legacy_cache_entries_cannot_extend_encrypted_identity_lifetime(self):
        token = views._pending_identity_fernet().encrypt_at_time(
            f"bvn:{self.raw}".encode(), int(time.time()) - 601).decode()
        value = self.pending(identity=token)
        value.pop("expires_at")
        cache.set(self.key, value, 600)
        raw, error = views._confirm_identity_ownership_challenge(self.user, "bvn", "724915")
        self.assertIsNone(raw)
        self.assertEqual(error.status_code, 400)
        self.assertIsNone(cache.get(self.key))

    def test_malformed_otp_returns_clean_error_without_consuming_challenge(self):
        self.pending()
        for otp in (None, 724915, ["724915"], {"otp": "724915"}, "12345", "１２３４５６"):
            with self.subTest(otp=otp):
                raw, error = views._confirm_identity_ownership_challenge(self.user, "bvn", otp)
                self.assertIsNone(raw)
                self.assertEqual(error.status_code, 400)
        self.assertIsNotNone(cache.get(self.key))
        self.assertEqual(views._confirm_identity_ownership_challenge(self.user, "bvn", "724915"),
                         (self.raw, None))

    def test_identity_save_uses_contact_and_other_identity_changes_after_request_started(self):
        # Another request completed BVN and live/address checks while this
        # request still has the old User instance loaded by authentication.
        User.objects.filter(pk=self.user.pk).update(
            bvn_verified=True, face_verified=True, address_verified=True, tier=1)
        self.assertTrue(views._save_verified_identity(self.user, "nin", self.raw))
        self.user.refresh_from_db()
        self.assertTrue(self.user.bvn_verified and self.user.nin_verified)
        self.assertEqual(self.user.tier, 3)

    def test_revoked_contact_verification_is_observed_before_tier_save(self):
        User.objects.filter(pk=self.user.pk).update(email_verified=False)
        self.assertTrue(views._save_verified_identity(self.user, "bvn", self.raw))
        self.user.refresh_from_db()
        self.assertTrue(self.user.bvn_verified)
        self.assertEqual(self.user.tier, 0)

    def test_non_string_face_session_is_rejected_without_exception(self):
        request = RequestFactory().post("/api/kyc/face/status/", content_type="application/json")
        request.user_obj = self.user
        request.data = {"session": {"id": "anything"}}
        self.assertEqual(unwrap(views.kyc_face_status)(request).status_code, 404)
