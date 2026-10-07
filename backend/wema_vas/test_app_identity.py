"""Single-entry app setup retains ownership, consent and money isolation gates."""
import json
import time
from unittest.mock import patch

from django.core.cache import cache
from django.db import DatabaseError
from django.test import TestCase, override_settings

from accounts.models import AccessToken, IdentityProof, User, hash_identifier, record_identity_proof
from wallet.models import Transaction, Wallet

from . import app_identity
from .enrollment import VALIDATION_CONSENT_VERSION
from .identity import cipher
from .models import VirtualAccount
from .test_validation_enrollment import VALIDATION


@override_settings(BANK_ACCOUNT_PROVIDER="wema_vas", WEMA_PARTNERSHIP_MODE="archive",
                   KYC_PROVIDER="prembly", TESTING=True, RATELIMIT_ENABLE=False,
                   SECURE_SSL_REDIRECT=False, WEMA_VAS_BILLER_ENABLED=False)
class AppIdentityEnrollmentTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="app-private-setup", phone="2348011111111",
            first_name="Ada", last_name="Eze", email_verified=True, phone_verified=True)
        self.wallet = Wallet.objects.create(user=self.user)
        self.token = AccessToken.issue(self.user).key
        self.raw = "12345678901"
        self.values = {**VALIDATION, "VALIDATION_USER_IDS": [self.user.pk]}
        settings = override_settings(WEMA_VAS=self.values)
        settings.enable()
        self.addCleanup(settings.disable)
        patches = {
            "network": patch("requests.sessions.Session.request", side_effect=AssertionError("Unexpected network")),
            "configured": patch("utility.providers._prembly_identity_live", return_value=True),
            "lookup": patch("accounts.views._lookup_identity", return_value={"success": True, "phone": "08077778888",
                "email": "holder@example.com", "first_name": "Ada", "last_name": "Eze"}),
            "code": patch("accounts.views._otp_code", return_value="123456"),
            "sms_live": patch("accounts.views.sms_live", return_value=True),
            "sms": patch("accounts.views.send_sms", return_value={"success": True}),
            "email_live": patch("accounts.views.email_live", return_value=False),
        }
        for name, item in patches.items():
            setattr(self, name, item.start())
            self.addCleanup(item.stop)

    def post(self, suffix, *, token=None, secure=True, device="", **data):
        return self.client.post("/api/wallet/vas/identity/" + suffix + "/", data,
            content_type="application/json", secure=secure,
            HTTP_AUTHORIZATION="Bearer " + (token or self.token), HTTP_X_ZITCH_DEVICE=device)

    def start(self, kind="bvn", **overrides):
        return self.post("start", **{"identity_type": kind, "number": self.raw, "consent": True,
            "enrollment_mode": "validation", "consent_version": VALIDATION_CONSENT_VERSION, **overrides})

    def challenge(self, kind="bvn"):
        response = self.start(kind)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["challenge_id"]

    def stored(self, reference):
        return json.loads(cipher().decrypt(cache.get(app_identity._key(reference)).encode()))

    def test_one_bvn_entry_plus_otp_allocates_once_and_erases_private_input(self):
        reference = self.challenge()
        key = app_identity._key(reference)
        self.assertNotIn(self.raw, str(cache.get(key)))
        self.assertNotIn(self.raw, str(cache.get(key + ":otp")))
        self.assertFalse(IdentityProof.objects.exists())
        response = self.post("confirm", challenge_id=reference, otp="123456")
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertTrue(data["identity_verified"])
        self.assertTrue(data["enrollment_completed"])
        self.assertFalse(data["has_account"])
        self.assertFalse(data["spending_available"])
        account = VirtualAccount.objects.get(user=self.user)
        self.assertTrue(account.number.startswith("711"))
        self.assertEqual(account.display_name, "Zitch/Ada Eze")
        self.assertIn(":app:", account.consent_reference)
        self.assertNotIn(account.number, response.content.decode())
        self.assertNotIn(self.raw, response.content.decode())
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertIsNone(cache.get(key))
        self.assertIsNone(cache.get(key + ":otp"))
        self.assertEqual(self.post("confirm", challenge_id=reference, otp="123456").status_code, 409)
        self.assertEqual(VirtualAccount.objects.count(), 1)
        self.assertEqual(IdentityProof.objects.get().verified_name, "Ada Eze")
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 0)
        self.assertFalse(Transaction.objects.exists())
        self.lookup.assert_called_once()
        self.sms.assert_called_once()

    def test_nin_uses_same_private_single_entry_and_ownership_path(self):
        reference = self.challenge("nin")
        self.assertEqual(self.post("confirm", challenge_id=reference, otp="123456").status_code, 200)
        self.assertEqual(IdentityProof.objects.get().identity_type, "nin")

    def test_existing_named_proof_does_not_repeat_provider_lookup_or_otp(self):
        self.user.bvn_verified = True
        self.user.set_bvn(self.raw)
        self.user.save()
        record_identity_proof(self.user, "bvn", self.raw, source=IdentityProof.IDENTITY_PROVIDER_OTP,
                              verified_name="Ada Eze")
        response = self.start()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["enrollment_completed"])
        self.assertFalse(response.json()["otp_required"])
        self.lookup.assert_not_called()
        self.sms.assert_not_called()

    def test_legacy_verified_flag_without_named_proof_still_requires_otp(self):
        self.user.bvn_verified = True
        self.user.set_bvn(self.raw)
        self.user.save()
        self.assertTrue(self.start().json()["otp_required"])
        self.assertFalse(VirtualAccount.objects.exists())

    def test_missing_stale_or_nonliteral_consent_refused_before_provider_cost(self):
        for changes in ({"consent": False}, {"consent": "true"}, {"consent_version": "old"},
                        {"enrollment_mode": "live"}):
            with self.subTest(changes=changes):
                self.assertEqual(self.start(**changes).status_code, 409)
        self.lookup.assert_not_called()
        self.sms.assert_not_called()

    def test_wrong_codes_never_create_proof_or_account(self):
        reference = self.challenge()
        for _ in range(5):
            self.assertIn(self.post("confirm", challenge_id=reference, otp="000000").status_code, (400, 429))
        self.assertEqual(self.post("confirm", challenge_id=reference, otp="123456").status_code, 400)
        self.assertFalse(IdentityProof.objects.exists())
        self.assertFalse(VirtualAccount.objects.exists())

    def test_challenge_cannot_cross_user_session_or_device(self):
        reference = self.challenge()
        other = User.objects.create_user(username="other-private-setup", phone="2348011111112")
        for token, device in ((AccessToken.issue(other).key, ""), (AccessToken.issue(self.user).key, ""),
                              (self.token, "different-install")):
            response = self.post("confirm", token=token, device=device, challenge_id=reference, otp="123456")
            self.assertEqual(response.status_code, 409, response.content)
            self.assertEqual(response.json()["code"], "vas_identity_challenge_expired")
        self.assertEqual(self.post("confirm", challenge_id=reference, otp="123456").status_code, 200)

    def test_expired_or_changed_consent_cannot_complete(self):
        reference = self.challenge()
        with override_settings(WEMA_VAS={**self.values, "MODE": "live", "PREFIX": "712"}):
            self.assertEqual(self.post("confirm", challenge_id=reference, otp="123456").status_code, 409)
        with patch("wema_vas.app_identity.time.time", return_value=time.time() + 601):
            self.assertEqual(self.post("confirm", challenge_id=reference, otp="123456").status_code, 409)
        self.assertFalse(IdentityProof.objects.exists())
        self.assertFalse(VirtualAccount.objects.exists())

    def test_allocator_failure_retries_without_second_identity_entry_or_otp(self):
        reference = self.challenge()
        with patch("wema_vas.app_identity.enroll_customer", side_effect=DatabaseError("unavailable")):
            response = self.post("confirm", challenge_id=reference, otp="123456")
        self.assertEqual(response.status_code, 503)
        self.assertTrue(response.json()["identity_verified"])
        self.assertTrue(response.json()["retry_available"])
        self.assertTrue(self.stored(reference)["verified"])
        response = self.post("confirm", challenge_id=reference)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(IdentityProof.objects.count(), 1)
        self.assertEqual(VirtualAccount.objects.count(), 1)
        self.lookup.assert_called_once()
        self.sms.assert_called_once()

    def test_policy_closed_during_ownership_preserves_proof_without_allocation(self):
        reference = self.challenge()
        with override_settings(WEMA_VAS={**self.values, "ENABLE_VALIDATION_ENROLLMENT": False}):
            response = self.post("confirm", challenge_id=reference, otp="123456")
        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.json()["identity_verified"])
        self.assertFalse(VirtualAccount.objects.exists())
        self.assertEqual(IdentityProof.objects.count(), 1)
        self.assertEqual(self.post("confirm", challenge_id=reference).status_code, 200)

    def test_resend_uses_same_encrypted_record_without_lookup_and_fixed_expiry(self):
        reference = self.challenge()
        original = self.stored(reference)
        self.assertEqual(self.post("resend", challenge_id=reference).status_code, 429)
        for attempt in range(3):
            value = self.stored(reference)
            value["last_sent"] = time.time() - 61
            app_identity._store(reference, value)
            response = self.post("resend", challenge_id=reference)
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(self.stored(reference)["expires"], original["expires"])
        value = self.stored(reference)
        value["last_sent"] = time.time() - 61
        app_identity._store(reference, value)
        self.assertEqual(self.post("resend", challenge_id=reference).status_code, 429)
        self.lookup.assert_called_once()
        self.assertEqual(self.sms.call_count, 4)

    def test_cache_failure_never_starts_paid_lookup_or_sends_code(self):
        with patch("wema_vas.app_identity.cache.add", return_value=False):
            self.assertEqual(self.start().status_code, 409)
        self.lookup.assert_not_called()
        self.sms.assert_not_called()

    def test_lock_cache_outage_returns_bounded_error_without_verification(self):
        reference = self.challenge()
        with patch("wema_vas.app_identity.cache.add", side_effect=ConnectionError("private cache outage")):
            response = self.post("confirm", challenge_id=reference, otp="123456")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private cache outage", response.content.decode())
        self.assertFalse(IdentityProof.objects.exists())
        self.assertFalse(VirtualAccount.objects.exists())
        self.assertEqual(self.post("confirm", challenge_id=reference, otp="123456").status_code, 200)

    def test_proof_database_failure_does_not_burn_valid_ownership_code(self):
        reference = self.challenge()
        with patch("accounts.views._save_verified_identity", side_effect=DatabaseError("proof store unavailable")):
            response = self.post("confirm", challenge_id=reference, otp="123456")
        self.assertEqual(response.status_code, 503)
        self.assertFalse(IdentityProof.objects.exists())
        self.assertFalse(VirtualAccount.objects.exists())
        response = self.post("confirm", challenge_id=reference, otp="123456")
        self.assertEqual(response.status_code, 200, response.content)
        self.lookup.assert_called_once()
        self.sms.assert_called_once()

    def test_unmatched_balance_or_pending_work_not_waived(self):
        self.wallet.balance = 1000
        self.wallet.save(update_fields=["balance"])
        self.assertEqual(self.start().status_code, 409)
        self.lookup.assert_not_called()
        self.assertFalse(VirtualAccount.objects.exists())

    def test_invalid_identity_and_authentication_or_http_rejected(self):
        self.assertEqual(self.start(number="wrong").status_code, 400)
        self.assertEqual(self.start(identity_type=["bvn"]).status_code, 400)
        self.assertEqual(self.post("start", token="wrong").status_code, 401)
        self.assertEqual(self.post("start", secure=False).status_code, 403)
        self.assertEqual(self.client.get("/api/wallet/vas/identity/start/").status_code, 405)
        self.lookup.assert_not_called()

    def test_legacy_generic_confirm_cannot_consume_app_challenge(self):
        reference = self.challenge()
        response = self.client.post("/api/kyc/bvn/confirm/", {"otp": "123456"},
            content_type="application/json", secure=True, HTTP_AUTHORIZATION="Bearer " + self.token)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.post("confirm", challenge_id=reference, otp="123456").status_code, 200)

    def test_foreign_or_replaced_verified_identity_is_refused_before_lookup(self):
        other = User.objects.create_user(username="owns-identifier", phone="2348011111113",
                                         bvn_hash=hash_identifier(self.raw))
        self.assertEqual(self.start().status_code, 409)
        other.bvn_hash = ""
        other.save(update_fields=["bvn_hash"])
        self.user.bvn_verified = True
        self.user.set_bvn("22222222222")
        self.user.save()
        self.assertEqual(self.start().status_code, 409)
        self.lookup.assert_not_called()
