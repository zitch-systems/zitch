"""Signup, recovery and contact edits cannot reuse stale authentication state."""
import json
from datetime import timedelta
from inspect import unwrap
from unittest.mock import patch

from django.core.cache import cache
from django.db.models import F
from django.test import RequestFactory, TestCase
from django.utils import timezone

from accounts import views
from accounts.models import AccessToken, OTP, RefreshToken, User


class AuthChallengeAtomicityTests(TestCase):
    phone = "08010000721"
    email = "bound@zitch.test"
    password = "Original-Secret-72!"
    new_password = "Changed-Secret-73!"

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="challenge-bound", phone=self.phone, email=self.email,
            password=self.password, phone_verified=True, email_verified=True,
        )

    def post(self, path, **data):
        return self.client.post(path, data, content_type="application/json")

    def issue(self, code="121212", purpose=OTP.RESET, **kwargs):
        return OTP.issue(phone=kwargs.pop("phone", self.phone), code=code,
                         email=kwargs.pop("email", self.email), purpose=purpose, **kwargs)

    def reset(self, code="121212", **kwargs):
        return self.post("/api/password/reset/", email_or_phone=self.phone,
                         otp=code, password=self.new_password, **kwargs)

    def test_consumed_new_reset_cannot_reopen_an_older_unused_code(self):
        older = self.issue()
        latest = self.issue(code="343434")
        OTP.objects.filter(pk=latest.pk).update(created=older.created)
        OTP.objects.filter(pk=latest.pk).update(used=True)
        self.assertEqual(self.reset().status_code, 400)
        self.user.refresh_from_db()
        older.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))
        self.assertFalse(older.used)

    def test_reset_challenge_cannot_follow_an_email_change(self):
        challenge = self.issue()
        User.objects.filter(pk=self.user.pk).update(email="different@zitch.test")
        self.assertEqual(self.reset().status_code, 400)
        self.user.refresh_from_db()
        challenge.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))
        self.assertFalse(challenge.used)

    def test_reset_challenge_cannot_follow_a_phone_reassignment(self):
        self.issue()
        User.objects.filter(pk=self.user.pk).update(phone="08010000722")
        other = User.objects.create_user(
            username="new-phone-holder", phone=self.phone, email="newholder@zitch.test",
            password=self.password,
        )
        self.assertEqual(self.reset().status_code, 400)
        other.refresh_from_db()
        self.assertTrue(other.check_password(self.password))

    def test_inactive_account_cannot_receive_a_reset_session(self):
        self.issue()
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        response = self.reset()
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("access_token", response.json())

    def test_reset_replay_does_not_revoke_the_winners_session(self):
        self.issue()
        old_access = AccessToken.issue(self.user)
        old_refresh = RefreshToken.issue(self.user)
        winner = self.reset()
        self.assertEqual(winner.status_code, 200)
        self.assertFalse(AccessToken.objects.filter(pk=old_access.pk).exists())
        old_refresh.refresh_from_db()
        self.assertIsNotNone(old_refresh.revoked_at)
        self.assertEqual(self.reset().status_code, 400)
        self.assertIsNotNone(AccessToken.resolve(winner.json()["access_token"]))

    def test_interleaved_consumption_cannot_change_password_or_revoke_sessions(self):
        challenge = self.issue()
        old = AccessToken.issue(self.user)

        def another_request_claimed(_challenge, _code):
            OTP.objects.filter(pk=challenge.pk).update(used=True)
            return True

        # Reproduce a competing consume between the read and write, even on
        # SQLite where select_for_update is unavailable. The conditional claim
        # must still refuse every side effect on the losing request.
        with patch.object(OTP, "verify_code", autospec=True,
                          side_effect=another_request_claimed):
            self.assertEqual(self.reset().status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))
        self.assertTrue(AccessToken.objects.filter(pk=old.pk).exists())

    def test_interleaved_wrong_reset_guesses_do_not_lose_an_attempt(self):
        challenge = self.issue()

        def another_wrong_guess(_challenge, _code):
            OTP.objects.filter(pk=challenge.pk).update(attempts=F("attempts") + 1)
            return False

        with patch.object(OTP, "verify_code", autospec=True, side_effect=another_wrong_guess):
            self.assertEqual(self.reset(code="999999").status_code, 400)
        challenge.refresh_from_db()
        self.assertEqual(challenge.attempts, 2)

    def test_reset_attempts_remain_capped(self):
        challenge = self.issue()
        for _ in range(OTP.MAX_ATTEMPTS):
            self.assertEqual(self.reset(code="999999").status_code, 400)
        self.assertEqual(self.reset().status_code, 429)
        challenge.refresh_from_db()
        self.assertEqual(challenge.attempts, OTP.MAX_ATTEMPTS)

    def test_expired_reset_cannot_change_password(self):
        challenge = self.issue()
        OTP.objects.filter(pk=challenge.pk).update(
            created=timezone.now() - timedelta(minutes=OTP.EXPIRY_MINUTES + 1))
        self.assertEqual(self.reset().status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))

    def test_signup_consumed_newest_challenge_cannot_reopen_older_code(self):
        phone = "08010000723"
        older = self.issue(phone=phone, purpose=OTP.SIGNUP)
        latest = self.issue(phone=phone, code="343434", purpose=OTP.SIGNUP)
        OTP.objects.filter(pk=latest.pk).update(used=True)
        response = self.post("/api/verify_otp/", phone=phone, otp="121212")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.filter(phone=phone).exists())
        older.refresh_from_db()
        self.assertFalse(older.used)

    def test_signup_replay_is_single_use_and_new_code_can_resume_mid_signup(self):
        phone = "08010000724"
        self.issue(phone=phone, purpose=OTP.SIGNUP)
        response = self.post("/api/verify_otp/", phone=phone, otp="121212",
                             first_name="Ada", last_name="Okafor")
        self.assertEqual(response.status_code, 200)
        user = User.objects.get(phone=phone)
        self.assertFalse(user.has_usable_password())
        self.assertTrue(user.phone_verified)
        self.assertEqual(user.get_full_name(), "Ada Okafor")
        self.assertEqual(self.post("/api/verify_otp/", phone=phone, otp="121212").status_code, 400)
        self.issue(phone=phone, code="343434", purpose=OTP.SIGNUP)
        self.assertEqual(self.post("/api/verify_otp/", phone=phone, otp="343434").status_code, 200)
        self.assertEqual(User.objects.filter(phone=phone).count(), 1)

    def test_signup_challenge_does_not_authenticate_an_existing_changed_email(self):
        self.user.set_unusable_password()
        self.user.save(update_fields=["password"])
        self.issue(purpose=OTP.SIGNUP)
        User.objects.filter(pk=self.user.pk).update(email="changed@zitch.test")
        response = self.post("/api/verify_otp/", phone=self.phone, otp="121212")
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("access_token", response.json())

    def test_interleaved_signup_claim_cannot_create_account(self):
        phone = "08010000725"
        challenge = self.issue(phone=phone, purpose=OTP.SIGNUP)

        def another_request_claimed(_challenge, _code):
            OTP.objects.filter(pk=challenge.pk).update(used=True)
            return True

        with patch.object(OTP, "verify_code", autospec=True,
                          side_effect=another_request_claimed):
            response = self.post("/api/verify_otp/", phone=phone, otp="121212")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.filter(phone=phone).exists())

    def test_interleaved_wrong_signup_guesses_do_not_lose_an_attempt(self):
        phone = "08010000726"
        challenge = self.issue(phone=phone, purpose=OTP.SIGNUP)

        def another_wrong_guess(_challenge, _code):
            OTP.objects.filter(pk=challenge.pk).update(attempts=F("attempts") + 1)
            return False

        with patch.object(OTP, "verify_code", autospec=True, side_effect=another_wrong_guess):
            response = self.post("/api/verify_otp/", phone=phone, otp="999999")
        self.assertEqual(response.status_code, 400)
        challenge.refresh_from_db()
        self.assertEqual(challenge.attempts, 2)

    def test_an_account_created_by_another_channel_cannot_be_authenticated_from_stale_signup(self):
        phone = "08010000727"
        self.issue(phone=phone, purpose=OTP.SIGNUP)
        get_or_create = User.objects.get_or_create

        def competing_channel_created(*args, **kwargs):
            User.objects.create_user(username="competing-signup", phone=phone,
                                     email=self.email, password=self.password)
            return get_or_create(*args, **kwargs)

        with patch.object(User.objects, "get_or_create", side_effect=competing_channel_created):
            response = self.post("/api/verify_otp/", phone=phone, otp="121212")
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("access_token", response.json())
        self.assertTrue(User.objects.get(phone=phone).check_password(self.password))

    def test_unsupported_reset_and_signup_input_types_return_clean_error(self):
        self.assertEqual(self.post("/api/verify_otp/", phone=self.phone, otp=121212).status_code, 400)
        self.assertEqual(self.post("/api/password/reset/", phone=self.phone,
                                   otp="121212", password=123).status_code, 400)

    def test_retired_username_does_not_merge_accounts_or_raise_a_registration_error(self):
        retired_phone = "08010000728"
        holder = User.objects.create_user(username=retired_phone, phone="08010000729",
                                         email="retired@zitch.test", password=self.password)
        challenge = self.issue(phone=retired_phone, purpose=OTP.SIGNUP)
        response = self.post("/api/verify_otp/", phone=retired_phone, otp="121212")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "registration_review_required")
        self.assertNotIn("access_token", response.json())
        self.assertFalse(User.objects.filter(phone=retired_phone).exists())
        holder.refresh_from_db()
        challenge.refresh_from_db()
        self.assertEqual(holder.phone, "08010000729")
        self.assertTrue(holder.check_password(self.password))
        self.assertFalse(challenge.used)


class ProfileWriteAtomicityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="profile-current", phone="08010000731", email="owner@zitch.test",
            password="Original-Secret-74!", email_verified=True, phone_verified=True,
        )

    def update(self, stale=None, **data):
        request = RequestFactory().post("/api/update_info/")
        request.user_obj = stale or self.user
        request.data = data
        return unwrap(views.update_info)(request)

    def test_name_update_preserves_identity_completed_after_authentication(self):
        stale = User.objects.get(pk=self.user.pk)
        User.objects.filter(pk=self.user.pk).update(
            bvn_verified=True, bvn_hash="proof-bvn", nin_verified=True,
            nin_hash="proof-nin", face_verified=True, address_verified=True, tier=3,
        )
        self.assertEqual(self.update(stale, first_name="Renamed").status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "Renamed")
        self.assertTrue(self.user.bvn_verified)
        self.assertTrue(self.user.nin_verified)
        self.assertTrue(self.user.face_verified)
        self.assertTrue(self.user.address_verified)
        self.assertEqual(self.user.bvn_hash, "proof-bvn")
        self.assertEqual(self.user.nin_hash, "proof-nin")
        self.assertEqual(self.user.tier, 3)

    def test_name_update_preserves_password_and_pin_changed_after_authentication(self):
        stale = User.objects.get(pk=self.user.pk)
        self.user.set_password("Changed-Secret-75!")
        self.user.set_transaction_pin("582619")
        self.user.save(update_fields=["password", *User.PIN_UPDATE_FIELDS])
        self.assertEqual(self.update(stale, first_name="Renamed").status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Changed-Secret-75!"))
        self.assertTrue(self.user.check_transaction_pin("582619"))

    def test_changed_contact_persists_tier_drop_and_retires_previous_challenges(self):
        User.objects.filter(pk=self.user.pk).update(
            bvn_verified=True, nin_verified=True, face_verified=True,
            address_verified=True, tier=3,
        )
        reset = OTP.issue(self.user.phone, "121212", email=self.user.email, purpose=OTP.RESET)
        inbox = OTP.issue(self.user.phone, "343434", email=self.user.email, purpose=OTP.EMAIL)
        response = self.update(email="new@zitch.test", password="Original-Secret-74!")
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        reset.refresh_from_db()
        inbox.refresh_from_db()
        self.assertFalse(self.user.email_verified)
        self.assertTrue(self.user.phone_verified)
        self.assertTrue(self.user.bvn_verified)
        self.assertTrue(self.user.face_verified)
        self.assertEqual(self.user.tier, 0)
        self.assertTrue(reset.used)
        self.assertTrue(inbox.used)

    def test_stale_password_cannot_change_contact_after_password_reset(self):
        stale = User.objects.get(pk=self.user.pk)
        self.user.set_password("Changed-Secret-75!")
        self.user.save(update_fields=["password"])
        response = self.update(stale, email="new@zitch.test", password="Original-Secret-74!")
        self.assertEqual(response.status_code, 403)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "owner@zitch.test")

    def test_unchanged_contact_does_not_clear_verified_status(self):
        self.assertEqual(self.update(first_name="Renamed", email="Owner@Zitch.Test").status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)
        self.assertTrue(self.user.phone_verified)

    def test_invalid_contact_details_and_inactive_account_are_rejected(self):
        for details in ({"email": "broken-inbox@"}, {"phone": "not-a-phone"},
                        {"first_name": 123}, {"last_name": "x" * 151}):
            with self.subTest(details=details):
                self.assertEqual(self.update(**details).status_code, 400)
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        self.assertEqual(self.update(first_name="Renamed").status_code, 401)

    def test_phone_replacement_keeps_the_proven_contact_and_live_challenges_intact(self):
        challenge = OTP.issue(self.user.phone, "121212", email=self.user.email, purpose=OTP.RESET)
        response = self.update(phone="08010000732", password="Original-Secret-74!")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(json.loads(response.content)["code"], "phone_change_unavailable")
        self.user.refresh_from_db()
        challenge.refresh_from_db()
        self.assertEqual(self.user.phone, "08010000731")
        self.assertTrue(self.user.phone_verified)
        self.assertFalse(challenge.used)


class CredentialWriteAtomicityTests(TestCase):
    password = "Original-Secret-81!"
    replacement = "Changed-Secret-82!"

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="credential-current", phone="08010000741", email="current@zitch.test",
            password=self.password,
        )
        self.token = AccessToken.issue(self.user).key

    def call(self, view, stale=None, **data):
        request = RequestFactory().post("/api/credential/", HTTP_AUTHORIZATION=f"Bearer {self.token}")
        request.user_obj = stale or self.user
        request.data = data
        return unwrap(view)(request)

    def test_password_edit_does_not_accept_a_credential_replaced_after_authentication(self):
        stale = User.objects.get(pk=self.user.pk)
        self.user.set_password(self.replacement)
        self.user.save(update_fields=["password"])
        response = self.call(views.set_password, stale,
                             current_password=self.password, password="Another-Secret-83!")
        self.assertEqual(response.status_code, 403)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.replacement))

    def test_stale_first_password_set_cannot_overwrite_a_completed_onboarding(self):
        self.user.set_unusable_password()
        self.user.save(update_fields=["password"])
        stale = User.objects.get(pk=self.user.pk)
        self.user.set_password(self.replacement)
        self.user.save(update_fields=["password"])
        self.assertEqual(self.call(views.set_password, stale,
                                   password="Another-Secret-83!").status_code, 403)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.replacement))

    def test_password_update_revokes_other_sessions_and_preserves_identity(self):
        other_token = AccessToken.issue(self.user).key
        chain = RefreshToken.issue(self.user)
        User.objects.filter(pk=self.user.pk).update(bvn_verified=True, nin_verified=True,
                                                  face_verified=True)
        response = self.call(views.set_password, current_password=self.password,
                             password=self.replacement)
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        chain.refresh_from_db()
        self.assertTrue(self.user.check_password(self.replacement))
        self.assertTrue(self.user.bvn_verified)
        self.assertTrue(self.user.face_verified)
        self.assertIsNotNone(AccessToken.resolve(self.token))
        self.assertIsNone(AccessToken.resolve(other_token))
        self.assertIsNotNone(chain.revoked_at)

    def test_pin_edit_cannot_use_an_old_password_after_password_reset(self):
        self.user.set_transaction_pin("582619")
        self.user.save(update_fields=list(User.PIN_UPDATE_FIELDS))
        stale = User.objects.get(pk=self.user.pk)
        self.user.set_password(self.replacement)
        self.user.save(update_fields=["password"])
        self.assertEqual(self.call(views.set_transaction_pin, stale, pin="693704",
                                   password=self.password).status_code, 403)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_transaction_pin("582619"))

    def test_stale_first_pin_set_requires_proof_after_another_request_completes_it(self):
        stale = User.objects.get(pk=self.user.pk)
        self.user.set_transaction_pin("582619")
        self.user.save(update_fields=list(User.PIN_UPDATE_FIELDS))
        self.assertEqual(self.call(views.set_transaction_pin, stale, pin="693704").status_code, 403)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_transaction_pin("582619"))

    def test_old_pin_after_concurrent_change_costs_attempt_without_restoring_it(self):
        self.user.set_transaction_pin("582619")
        self.user.save(update_fields=list(User.PIN_UPDATE_FIELDS))
        stale = User.objects.get(pk=self.user.pk)
        self.user.set_transaction_pin("693704")
        self.user.save(update_fields=list(User.PIN_UPDATE_FIELDS))
        self.assertEqual(self.call(views.set_transaction_pin, stale, pin="704826",
                                   old_pin="582619").status_code, 403)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_transaction_pin("693704"))
        self.assertEqual(self.user.pin_failed_attempts, 1)

    def test_fresh_password_can_reset_locked_pin_and_preserves_identity(self):
        self.user.set_transaction_pin("582619")
        self.user.save(update_fields=list(User.PIN_UPDATE_FIELDS))
        stale = User.objects.get(pk=self.user.pk)
        User.objects.filter(pk=self.user.pk).update(
            pin_failed_attempts=4, pin_lockout_strikes=2,
            pin_locked_until=timezone.now() + timedelta(hours=1), bvn_verified=True,
        )
        self.assertEqual(self.call(views.set_transaction_pin, stale, pin="693704",
                                   password=self.password).status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_transaction_pin("693704"))
        self.assertFalse(self.user.pin_locked)
        self.assertEqual(self.user.pin_failed_attempts, 0)
        self.assertEqual(self.user.pin_lockout_strikes, 0)
        self.assertTrue(self.user.bvn_verified)

    def test_inactive_users_and_unsupported_credential_types_are_rejected(self):
        self.assertEqual(self.call(views.set_password, password=123).status_code, 400)
        self.assertEqual(self.call(views.set_transaction_pin, pin=5826).status_code, 400)
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        self.assertEqual(self.call(views.set_password, password=self.replacement).status_code, 401)
        self.assertEqual(self.call(views.set_transaction_pin, pin="582619").status_code, 401)


class AuthSenderInputTests(TestCase):
    phone = "08010000751"

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="sender-input", phone=self.phone, email="sender@zitch.test",
            password="Original-Secret-84!", email_verified=True,
        )

    def post(self, path, data):
        return self.client.post(path, data, content_type="application/json")

    def test_signup_and_resend_reject_malformed_contact_fields_without_delivery_or_challenge(self):
        cases = [
            {"phone": value, "email": "new@zitch.test"}
            for value in ([], {}, 123, False, "", "x" * 500, "0801abc2345")
        ] + [
            {"phone": "08010000752", "email": value}
            for value in ([], {}, 123, False, "broken-inbox@", "x" * 300 + "@zitch.test")
        ]
        for path in ("/api/phone_verification/", "/api/resend_verify_otp/"):
            for data in cases:
                with self.subTest(path=path, data=data), \
                        patch("accounts.views.send_sms") as sms, \
                        patch("accounts.views.send_email") as email:
                    cache.clear()
                    self.assertEqual(self.post(path, data).status_code, 400)
                    self.assertEqual(OTP.objects.count(), 0)
                    sms.assert_not_called()
                    email.assert_not_called()

    def test_forgot_rejects_malformed_identifiers_without_delivery_or_challenge(self):
        for field in ("email_or_phone", "phone"):
            for value in ([], {}, 123, False, "", "x" * 500, "broken-inbox@"):
                with self.subTest(field=field, value=value), \
                        patch("accounts.views.send_sms") as sms, \
                        patch("accounts.views.send_email") as email:
                    cache.clear()
                    self.assertEqual(self.post("/api/password/forgot/", {field: value}).status_code, 400)
                    self.assertEqual(OTP.objects.count(), 0)
                    sms.assert_not_called()
                    email.assert_not_called()

    def test_non_object_json_and_invalid_json_never_reach_auth_senders(self):
        for path in ("/api/phone_verification/", "/api/resend_verify_otp/", "/api/password/forgot/"):
            for body in ("[]", "[1]", "null", "{broken-json"):
                with self.subTest(path=path, body=body), \
                        patch("accounts.views.send_sms") as sms, \
                        patch("accounts.views.send_email") as email:
                    cache.clear()
                    self.assertEqual(self.client.post(path, body, content_type="application/json").status_code, 400)
                    self.assertEqual(OTP.objects.count(), 0)
                    sms.assert_not_called()
                    email.assert_not_called()

    def test_resend_preserves_signup_email_without_copying_other_challenge_purposes(self):
        phone = "08010000753"
        OTP.issue(phone, "121212", email="signup@zitch.test", purpose=OTP.SIGNUP)
        OTP.issue(phone, "343434", email="unrelated@zitch.test", purpose=OTP.EMAIL)
        OTP.objects.filter(phone=phone).update(created=timezone.now() - timedelta(seconds=25))
        with patch("accounts.views.send_sms", return_value={"success": True}):
            self.assertEqual(self.post("/api/resend_verify_otp/", {"phone": phone}).status_code, 200)
        latest = OTP.objects.filter(phone=phone).order_by("-created", "-pk").first()
        self.assertEqual(latest.purpose, OTP.SIGNUP)
        self.assertEqual(latest.email, "signup@zitch.test")

    def test_inactive_account_does_not_receive_recovery_codes(self):
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        with patch("accounts.views.send_sms") as sms, patch("accounts.views.send_email") as email:
            response = self.post("/api/password/forgot/", {"phone": self.phone})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(OTP.objects.count(), 0)
        sms.assert_not_called()
        email.assert_not_called()
