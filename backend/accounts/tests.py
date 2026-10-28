"""Tests for auth onboarding, OTP hardening, and credential-setting security."""
import json
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from accounts import views
from django.utils import timezone

from wallet.services import get_or_create_wallet
from wallet.tests import make_user
from common.ratelimit import _lockout_key, client_ip
from banking.test_client import CONFIG
from banking.test_fixtures import BankIdentityAPI, bind_test_bank, make_bank_user
from banking.client import Reply
from banking.models import BankAccount, IdentityChallenge, AddressVerification
from transfers.tests import BankIOCase

from .models import OTP, AccessToken

User = get_user_model()


class DeviceBoundSessionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="bound", phone="08035550000")

    def test_bound_token_resolves_only_for_the_authenticating_install(self):
        raw = AccessToken.issue(self.user, device_id="install-a").key
        self.assertEqual(AccessToken.resolve(raw, device_id="install-a"), self.user)
        self.assertIsNone(AccessToken.resolve(raw, device_id="install-b"))
        self.assertIsNone(AccessToken.resolve(raw, device_id=""))
        # Internal non-HTTP callers retain the existing model API. The HTTP auth
        # boundary never passes None, so a remote client cannot select this path.
        self.assertEqual(AccessToken.resolve(raw), self.user)

    def test_unbound_legacy_token_remains_valid_during_rollout(self):
        raw = AccessToken.issue(self.user).key
        self.assertEqual(AccessToken.resolve(raw, device_id="new-client"), self.user)

    def test_authenticated_endpoint_enforces_the_binding(self):
        raw = AccessToken.issue(self.user, device_id="install-a").key
        payload = json.dumps({"access_token": raw})
        allowed = self.client.post("/api/wallet_balance/", data=payload,
                                   content_type="application/json",
                                   HTTP_X_ZITCH_DEVICE="install-a")
        refused = self.client.post("/api/wallet_balance/", data=payload,
                                   content_type="application/json",
                                   HTTP_X_ZITCH_DEVICE="install-b")
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(refused.status_code, 401)


class OnboardingOtpTests(TestCase):
    def setUp(self):
        self.client = Client()

    def post(self, path, payload):
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    def test_onboarding_creates_user_and_token(self):
        # The raw code is never stored (only its hash), so pin it via _otp_code to
        # learn what to submit — reading it back off the row is no longer possible.
        with patch("accounts.views._otp_code", return_value="112233"):
            self.post("/api/phone_verification/", {"phone": "08011112222", "email": "new@zitch.test"})
        res, body = self.post("/api/verify_otp/", {"phone": "08011112222", "otp": "112233"})
        self.assertEqual(res.status_code, 200)
        self.assertIn("access_token", body)
        user = User.objects.get(phone="08011112222")
        self.assertEqual(body["account_namespace"], views.spend_account_namespace(user))
        self.assertTrue(User.objects.filter(phone="08011112222").exists())
        # The stored value is a hash, not the plaintext code.
        self.assertNotEqual(OTP.objects.get(phone="08011112222").code_hash, "112233")

    def test_verify_otp_stores_legal_name(self):
        # The name captured at register is sent to verify_otp so the account — and
        # its later dedicated funding NUBAN — is created with a holder name (an
        # unnamed funding account can't be safely paid into by transfer).
        with patch("accounts.views._otp_code", return_value="445566"):
            self.post("/api/phone_verification/", {"phone": "08099001122", "email": "named@zitch.test"})
        res, _ = self.post("/api/verify_otp/",
                           {"phone": "08099001122", "otp": "445566",
                            "first_name": "Ada", "last_name": "Okafor"})
        self.assertEqual(res.status_code, 200)
        u = User.objects.get(phone="08099001122")
        self.assertEqual(u.first_name, "Ada")
        self.assertEqual(u.last_name, "Okafor")
        self.assertEqual(u.get_full_name(), "Ada Okafor")

    def test_verify_otp_without_name_still_verifies(self):
        # Name is optional at the endpoint (older app builds) — omitting it must
        # never break verification.
        with patch("accounts.views._otp_code", return_value="778811"):
            self.post("/api/phone_verification/", {"phone": "08099002233", "email": ""})
        res, body = self.post("/api/verify_otp/", {"phone": "08099002233", "otp": "778811"})
        self.assertEqual(res.status_code, 200)
        self.assertIn("access_token", body)

    def test_signup_otp_is_sent_by_sms_only_not_email(self):
        # The signup OTP proves control of the PHONE, so it must never be emailed
        # to the caller-supplied (unverified) address — that would let an attacker
        # receive the code for someone else's number and squat the account.
        from unittest.mock import patch
        with patch("accounts.views.send_sms") as sms, patch("accounts.views.send_email") as email:
            self.post("/api/phone_verification/",
                      {"phone": "08012223333", "email": "attacker@evil.test"})
        sms.assert_called_once()                       # code goes to the phone
        email.assert_not_called()                      # never to the body-supplied email
        # The email is still captured on the OTP for the eventual account record.
        self.assertEqual(OTP.objects.get(phone="08012223333").email, "attacker@evil.test")

    def test_resend_signup_otp_is_sms_only(self):
        from unittest.mock import patch
        with patch("accounts.views.send_sms") as sms, patch("accounts.views.send_email") as email:
            self.post("/api/resend_verify_otp/",
                      {"phone": "08012224444", "email": "attacker@evil.test"})
        sms.assert_called_once()
        email.assert_not_called()

    def test_otp_attempts_are_capped(self):
        OTP.issue(phone="08033334444", code="13579")
        for _ in range(OTP.MAX_ATTEMPTS):
            res, _ = self.post("/api/verify_otp/", {"phone": "08033334444", "otp": "00000"})
            self.assertEqual(res.status_code, 400)
        # Cap reached: even the correct code is refused now.
        res, _ = self.post("/api/verify_otp/", {"phone": "08033334444", "otp": "13579"})
        self.assertEqual(res.status_code, 429)
        self.assertFalse(User.objects.filter(phone="08033334444").exists())

    def test_correct_code_works_within_attempt_cap(self):
        OTP.issue(phone="08055556666", code="24680")
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


PROD = {"DEBUG": False, "TESTING": False}
KEYED = {"BASE_URL": "https://v3.api.termii.com", "API_KEY": "tk_live",
         "SENDER_ID": "Zitch", "CHANNEL": "dnd"}
UNKEYED = {**KEYED, "API_KEY": ""}
NO_TEST_OTP = {"PHONE": "", "CODE": ""}


@override_settings(TEST_OTP=NO_TEST_OTP, **PROD)
class OtpDeliveryGuardTests(TestCase):
    @override_settings(TERMII=KEYED)
    def test_provider_rejection_is_not_reported_as_sent(self):
        for path, phone in (("/api/phone_verification/", "08055550101"),
                            ("/api/resend_verify_otp/", "08055550102")):
            with self.subTest(path=path), patch("accounts.views.send_sms", return_value={"success": False}):
                response = self.client.post(path, data=json.dumps({"phone": phone}),
                                            content_type="application/json")
                self.assertEqual(response.status_code, 503)

    """A signup OTP must never be promised over a rail that cannot deliver it.

    send_sms returns a MOCK SUCCESS when TERMII_API_KEY is unset, and the signup
    endpoints discard the send result on purpose (anti-enumeration). Together those
    two correct decisions produced one wrong outcome: an unkeyed production deploy
    answered "a verification code has been sent" and sent nothing at all, with no
    error logged and nothing for the customer to act on. These tests pin the refusal.
    """

    def setUp(self):
        self.client = Client()

    def post(self, path, payload):
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    @override_settings(TERMII=UNKEYED)
    def test_signup_refuses_instead_of_promising_an_undeliverable_code(self):
        res, body = self.post("/api/phone_verification/",
                              {"phone": "08055550001", "email": "a@zitch.test"})
        self.assertEqual(res.status_code, 503)
        self.assertNotIn("has been sent", json.dumps(body))

    @override_settings(TERMII=UNKEYED)
    def test_refusing_issues_no_code_so_the_number_is_not_left_on_cooldown(self):
        # Issuing a row here would be the worse bug: the customer is refused AND then
        # rate-limited out of retrying once the rail is fixed.
        self.post("/api/phone_verification/", {"phone": "08055550002", "email": "b@zitch.test"})
        self.assertFalse(OTP.objects.filter(phone="08055550002").exists())

    @override_settings(TERMII=UNKEYED)
    def test_resend_is_guarded_on_the_same_terms(self):
        res, _ = self.post("/api/resend_verify_otp/", {"phone": "08055550003"})
        self.assertEqual(res.status_code, 503)

    @override_settings(TERMII=KEYED)
    def test_a_keyed_rail_sends_normally(self):
        with patch("accounts.views.send_sms", return_value={"success": True}) as sms:
            res, _ = self.post("/api/phone_verification/",
                               {"phone": "08055550004", "email": "c@zitch.test"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(sms.called)
        self.assertTrue(OTP.objects.filter(phone="08055550004").exists())

    @override_settings(TERMII=UNKEYED, TEST_OTP={"PHONE": "08055550005", "CODE": "424242"})
    def test_the_test_otp_number_is_exempt(self):
        # TEST_OTP exists to walk signup while a sender ID awaits carrier approval —
        # i.e. exactly while the rail is down. Locking it out would break the only
        # stopgap the setting is there to provide.
        res, _ = self.post("/api/phone_verification/",
                           {"phone": "08055550005", "email": "d@zitch.test"})
        self.assertEqual(res.status_code, 200)
        res, body = self.post("/api/verify_otp/", {"phone": "08055550005", "otp": "424242"})
        self.assertEqual(res.status_code, 200)
        self.assertIn("access_token", body)

    @override_settings(TERMII=UNKEYED, DEBUG=True)
    def test_local_development_keeps_mock_mode(self):
        # Mock mode is the point of a dev box: the guard must not make signup
        # untestable without a live Termii key.
        res, _ = self.post("/api/phone_verification/",
                           {"phone": "08055550006", "email": "e@zitch.test"})
        self.assertEqual(res.status_code, 200)


class OtpTakeoverTests(TestCase):
    """Regression for the password-less account-takeover chain: resend_verify_otp
    must not mint a SIGNUP OTP for an established account (let alone deliver it to a
    client-supplied email), and verify_otp must never sign a SIGNUP OTP into an
    account that already has a password."""

    def setUp(self):
        self.client = Client()
        cache.clear()  # fresh otp_send rate-limit budget

    def post(self, path, payload):
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    def _established_victim(self, phone, email):
        victim, _ = make_user(phone, email)
        victim.set_password("Passw0rd123!")  # a real account has a usable password
        victim.save(update_fields=["password"])
        return victim

    def test_resend_will_not_mint_signup_otp_for_established_account(self):
        self._established_victim("08099990001", "victim@zitch.test")
        # Attacker tries to have the victim's signup OTP delivered to their own inbox.
        res, body = self.post("/api/resend_verify_otp/",
                              {"phone": "08099990001", "email": "attacker@evil.test"})
        self.assertEqual(res.status_code, 200)  # generic, non-enumerating reply
        # Crucially, no SIGNUP OTP was created — so there is nothing to verify with.
        self.assertFalse(OTP.objects.filter(phone="08099990001", purpose=OTP.SIGNUP).exists())

    def test_verify_otp_cannot_authenticate_into_established_account(self):
        self._established_victim("08099990002", "victim2@zitch.test")
        # Even if a SIGNUP OTP somehow exists for the phone, it must not log in.
        OTP.issue(phone="08099990002", email="attacker@evil.test", code="55555")
        res, body = self.post("/api/verify_otp/", {"phone": "08099990002", "otp": "55555"})
        self.assertEqual(res.status_code, 400)
        self.assertNotIn("access_token", body)

    def test_genuine_new_signup_still_works(self):
        # The guards must not break a real first-time signup.
        with patch("accounts.views._otp_code", return_value="778899"):
            self.post("/api/phone_verification/", {"phone": "08099990003", "email": "new@zitch.test"})
        res, body = self.post("/api/verify_otp/", {"phone": "08099990003", "otp": "778899"})
        self.assertEqual(res.status_code, 200)
        self.assertIn("access_token", body)


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
            "access_token": atk_token, "email": "victim@zitch.test", "password": "Newpass!12345",
        })
        self.assertEqual(res.status_code, 200)
        self.assertEqual(User.objects.get(pk=victim.pk).password, victim_hash)  # untouched
        self.assertTrue(User.objects.get(pk=attacker.pk).check_password("Newpass!12345"))

    def test_set_password_min_length(self):
        _, token = make_user("08030000003", "c@zitch.test")
        res, _ = self.post("/api/set-password/", {"access_token": token, "password": "short"})
        self.assertEqual(res.status_code, 400)

    def test_set_password_rejects_weak(self):
        # Server-side strength rules: an all-numeric or top-common password must
        # be refused even via a direct API call (the client hints are bypassable).
        _, token = make_user("08030000009", "weak@zitch.test")
        for pw in ("12345678", "password"):
            res, _ = self.post("/api/set-password/", {"access_token": token, "password": pw})
            self.assertEqual(res.status_code, 400)

    def test_change_password_requires_current(self):
        # Once an account HAS a password, changing it needs the current one, so a
        # stolen session token alone can't overwrite it. (First-time onboarding,
        # where no password exists yet, stays exempt.)
        user, token = make_user("08070000007", "chg@zitch.test")
        user.set_password("Oldpass123")
        user.save(update_fields=["password"])

        # No current password -> refused, password untouched.
        res, body = self.post("/api/set-password/", {"access_token": token, "password": "Newpass456!"})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(body.get("code"), "current_password_required")
        self.assertTrue(User.objects.get(pk=user.pk).check_password("Oldpass123"))

        # Wrong current password -> refused.
        res, _ = self.post("/api/set-password/", {
            "access_token": token, "password": "Newpass456!", "current_password": "nope12345"})
        self.assertEqual(res.status_code, 403)
        self.assertTrue(User.objects.get(pk=user.pk).check_password("Oldpass123"))

        # Correct current password -> changed.
        res, _ = self.post("/api/set-password/", {
            "access_token": token, "password": "Newpass456!", "current_password": "Oldpass123"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(User.objects.get(pk=user.pk).check_password("Newpass456!"))

    def test_set_pin_requires_auth_and_sets_owner(self):
        res, _ = self.post("/api/set-transaction-pin/", {"email": "x@zitch.test", "pin": "135790"})
        self.assertEqual(res.status_code, 401)
        # First-time PIN set (no existing PIN) needs only the session token.
        user = User.objects.create(username="08040000004", phone="08040000004", email="d@zitch.test")
        token = AccessToken.issue(user).key
        res, _ = self.post("/api/set-transaction-pin/", {"access_token": token, "pin": "135790"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(User.objects.get(pk=user.pk).check_transaction_pin("135790"))

    def test_new_pin_policy_is_enforced_by_the_api(self):
        user = User.objects.create(username="08040000006", phone="08040000006",
                                   email="pin-policy@zitch.test")
        token = AccessToken.issue(user).key
        for weak_pin in ("123456", "654321", "789012", "000000", "121212", "123123"):
            res, body = self.post("/api/set-transaction-pin/", {
                "access_token": token, "pin": weak_pin})
            self.assertEqual((res.status_code, body.get("code")), (400, "weak_pin"))
        user.refresh_from_db()
        self.assertFalse(user.transaction_pin)

    def test_changing_existing_pin_requires_current_pin_or_password(self):
        # A token alone must not be enough to OVERWRITE an existing PIN (else the
        # brute-force lockout is moot — an attacker would just reset the PIN).
        user = User.objects.create(username="08050000005", phone="08050000005", email="e@zitch.test")
        user.set_password("Passw0rd123!")
        user.set_transaction_pin("1234")
        user.save()
        token = AccessToken.issue(user).key
        # No proof at all -> rejected.
        res, body = self.post("/api/set-transaction-pin/", {"access_token": token, "pin": "975310"})
        self.assertEqual((res.status_code, body.get("code")), (403, "current_pin_required"))
        self.assertTrue(User.objects.get(pk=user.pk).check_transaction_pin("1234"))  # unchanged
        # A WRONG current PIN is rejected too.
        res, body = self.post("/api/set-transaction-pin/", {
            "access_token": token, "pin": "975310", "old_pin": "0000"})
        self.assertEqual(res.status_code, 403)
        self.assertTrue(User.objects.get(pk=user.pk).check_transaction_pin("1234"))  # unchanged
        # With the CURRENT PIN, the change goes through.
        res, _ = self.post("/api/set-transaction-pin/", {
            "access_token": token, "pin": "975310", "old_pin": "1234"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(User.objects.get(pk=user.pk).check_transaction_pin("975310"))
        # The account password remains a valid fallback (forgot-PIN recovery).
        res, _ = self.post("/api/set-transaction-pin/", {
            "access_token": token, "pin": "432100", "password": "Passw0rd123!"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(User.objects.get(pk=user.pk).check_transaction_pin("432100"))

    def test_setting_new_pin_clears_brute_force_lockout(self):
        # A user who locked their PIN and then legitimately changes it (which
        # requires the password) must not stay locked out against the new PIN.
        user = User.objects.create(username="08060000006", phone="08060000006", email="f@zitch.test")
        user.set_password("Passw0rd123!")
        user.set_transaction_pin("1234")
        user.pin_failed_attempts = 5
        user.pin_locked_until = timezone.now() + timedelta(minutes=15)
        user.save()
        token = AccessToken.issue(user).key
        res, _ = self.post("/api/set-transaction-pin/", {
            "access_token": token, "pin": "567891", "password": "Passw0rd123!"})
        self.assertEqual(res.status_code, 200)
        u = User.objects.get(pk=user.pk)
        self.assertEqual(u.pin_failed_attempts, 0)
        self.assertIsNone(u.pin_locked_until)
        self.assertTrue(u.check_transaction_pin("567891"))

    def test_verify_pin_endpoint_accepts_the_right_pin_and_refuses_a_wrong_one(self):
        """Biometric payment approval caches the PIN and replays it forever, so it
        must be checked before it is stored — one typo otherwise auto-submits a
        wrong PIN on every payment until the account locks."""
        user, token = make_user("08030000007", "pin@zitch.test", pin="1234")
        res, _ = self.post("/api/verify-transaction-pin/",
                           {"access_token": token, "pin": "1234"})
        self.assertEqual(res.status_code, 200)

        res, body = self.post("/api/verify-transaction-pin/",
                              {"access_token": token, "pin": "9999"})
        self.assertNotEqual(res.status_code, 200)
        self.assertNotIn("1234", json.dumps(body), "never echo the real PIN back")

    def test_changing_email_requires_reauth(self):
        """A stolen session token alone must not redirect account recovery.

        password_forgot mails the reset code to whatever user.email holds, so
        without this an attacker with only a bearer token could swap the email,
        request a reset, and take the account permanently — password_reset then
        deletes the real owner's tokens, so they cannot even get back in.
        """
        user, token = make_user("08030000003", "owner@zitch.test")
        res, body = self.post("/api/update_info/", {
            "access_token": token, "email": "attacker@evil.test"})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(body.get("code"), "reauth_required")
        self.assertEqual(User.objects.get(pk=user.pk).email, "owner@zitch.test")

    def test_changing_phone_requires_reauth(self):
        user, token = make_user("08030000004", "owner2@zitch.test")
        res, body = self.post("/api/update_info/", {
            "access_token": token, "phone": "08099999999"})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(User.objects.get(pk=user.pk).phone, "08030000004")

    def test_changing_email_with_the_password_clears_verification(self):
        user, token = make_user("08030000005", "owner3@zitch.test")
        user.set_password("Passw0rd123!")
        user.save(update_fields=["password"])
        res, _ = self.post("/api/update_info/", {
            "access_token": token, "email": "new@zitch.test",
            "password": "Passw0rd123!"})
        self.assertEqual(res.status_code, 200)
        u = User.objects.get(pk=user.pk)
        self.assertEqual(u.email, "new@zitch.test")
        # An address the account has never proven must not stay marked verified —
        # the KYC ladder reads this flag.
        self.assertFalse(u.email_verified)

    def test_a_name_only_update_still_needs_no_reauth(self):
        user, token = make_user("08030000006", "owner4@zitch.test")
        res, _ = self.post("/api/update_info/", {
            "access_token": token, "first_name": "Renamed"})
        self.assertEqual(res.status_code, 200)
        u = User.objects.get(pk=user.pk)
        self.assertEqual(u.first_name, "Renamed")
        self.assertTrue(u.email_verified, "a name change moves no trust")

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


@override_settings(NINEPSB=CONFIG)
class KycTierTests(BankIdentityAPI, TestCase):
    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08010000001", "a@zitch.test", identity_verified=False, tier=0)

    def post(self, path, payload):
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    def test_bank_status_reports_contact_prerequisites(self):
        self.user.email_verified = False
        self.user.save(update_fields=["email_verified"])
        response, body = self.post("/api/banking/status/", {"access_token": self.token})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(body["email_verified"])
        self.assertTrue(body["email_verification_required"])
        self.assertTrue(body["phone_verified"])
        self.assertFalse(body["identity_available"])

    def test_bvn_plus_nin_promote_to_tier_1(self):
        self.assertEqual(self.complete_identity(self.user, self.token)["tier"], 1)
        self.assertEqual(self.complete_identity(self.user, self.token, "nin", "10987654321")["tier"], 1)
        self.assertEqual(BankAccount.objects.count(), 1)

    def test_an_unverified_phone_holds_the_tier_at_zero(self):
        self.user.phone_verified = False
        self.user.save(update_fields=["phone_verified"])
        with patch("banking.client.Client.initiate_identity") as send:
            response = self.identity_post(self.user, self.token)
        self.assertEqual(response.status_code, 400)
        send.assert_not_called()
        self.assertFalse(IdentityChallenge.objects.exists())
        self.user.refresh_from_db()
        self.assertEqual(self.user.tier, 0)
        self.user.phone_verified = True
        self.user.save(update_fields=["phone_verified"])
        self.assertEqual(self.complete_identity(self.user, self.token)["tier"], 1)

    def test_unverified_email_holds_the_tier_at_zero(self):
        self.user.email_verified = False
        self.user.save(update_fields=["email_verified"])
        with patch("banking.client.Client.initiate_identity") as send:
            response = self.identity_post(self.user, self.token)
        self.assertEqual(response.status_code, 400)
        send.assert_not_called()
        with patch("accounts.views._otp_code", return_value="909090"):
            self.post("/api/email/verify/start/", {"access_token": self.token})
        response, body = self.post("/api/email/verify/confirm/", {"access_token": self.token, "otp": "909090"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["tier"], 0)
        self.user.refresh_from_db()
        self.assertEqual(self.complete_identity(self.user, self.token)["tier"], 1)

    def test_email_can_be_set_while_unverified(self):
        self.user.email, self.user.email_verified = "", False
        self.user.save(update_fields=["email", "email_verified"])
        res, _ = self.post("/api/email/verify/start/", {"access_token": self.token})
        self.assertEqual(res.status_code, 400)
        with patch("accounts.views._otp_code", return_value="909090"):
            res, _ = self.post("/api/email/verify/start/", {"access_token": self.token, "email": "Ada.New@Zitch.test"})
        self.assertEqual(res.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "ada.new@zitch.test")
        self.post("/api/email/verify/confirm/", {"access_token": self.token, "otp": "909090"})
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)

    def test_kyc_ladder_stops_at_unavailable_tier_3(self):
        self.complete_identity(self.user, self.token)
        self.complete_identity(self.user, self.token, "nin", "10987654321")
        self.approve_tier2(self.user, self.token)
        with patch("banking.client.Client.wallet", return_value=self.bank_wallet_reply(self.user, tier=2)), \
             patch("banking.client.Client.upgrade") as send:
            response, body = self.post("/api/banking/upgrade/", {"access_token": self.token, "tier": 3,
                "profile": {**self.upgrade_profile(self.user), "proofOfAddressVerification": "untrusted"}})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Prembly", body["message"])
        send.assert_not_called()
        self.user.refresh_from_db()
        self.assertEqual(self.user.tier, 2)
        self.assertFalse(self.user.address_verified)

    def test_address_without_proof_document_is_refused(self):
        with patch("banking.address._request") as send:
            response, body = self.post("/api/banking/address/start/", {"access_token": self.token,
                "address": {"street": "Test Road"}})
        self.assertEqual(response.status_code, 400)
        self.assertIn("provider setup", body["message"])
        send.assert_not_called()
        self.assertFalse(AddressVerification.objects.exists())

    def test_address_does_not_request_a_different_document_when_unavailable(self):
        response, body = self.post("/api/banking/address/start/", {"access_token": self.token,
            "address": {"street": "Test Road"}, "document": "A" * 64})
        self.assertEqual(response.status_code, 400)
        self.assertIn("provider setup", body["message"])
        self.assertNotIn("upload", body["message"].lower())

    def test_address_proof_is_not_retained(self):
        self.post("/api/banking/address/start/", {"access_token": self.token,
            "address": {"street": "Test Road"}, "document": "ZmFrZXByb29m"})
        self.user.refresh_from_db()
        self.assertFalse(self.user.address_verified)
        self.assertEqual(self.user.address, "")
        self.assertFalse(AddressVerification.objects.exists())
        self.assertNotIn("ZmFrZXByb29m", str(vars(self.user)))

    def test_bvn_nin_stored_hashed_not_raw(self):
        from accounts.models import hash_identifier
        self.complete_identity(self.user, self.token)
        self.complete_identity(self.user, self.token, "nin", "10987654321")
        self.user.refresh_from_db()
        self.assertEqual(self.user.bvn_last4, "8901")
        self.assertEqual(self.user.nin_last4, "4321")
        self.assertEqual(self.user.bvn_hash, hash_identifier("12345678901"))
        self.assertNotIn("12345678901", self.user.bvn_hash)
        self.assertFalse(hasattr(self.user, "bvn"))
        self.assertFalse(IdentityChallenge.objects.exclude(encrypted_input="").exists())

    def test_direct_kyc_rejects_identity_already_owned_by_another_user(self):
        other, _ = make_user("08020000002", "other@zitch.test")
        other.set_bvn("12345678901")
        other.set_nin("10987654321")
        other.save(update_fields=["bvn_hash", "bvn_last4", "nin_hash", "nin_last4"])
        with patch("banking.client.Client.initiate_identity") as send:
            for kind, identifier in (("bvn", "12345678901"), ("nin", "10987654321")):
                response = self.identity_post(self.user, self.token, kind, identifier)
                self.assertEqual(response.status_code, 400)
                self.assertIn("already linked", response.json()["message"])
        send.assert_not_called()
        self.assertFalse(IdentityChallenge.objects.exists())
        self.user.refresh_from_db()
        self.assertFalse(self.user.bvn_verified)
        self.assertFalse(self.user.nin_verified)

    def test_bvn_otp_start_rejects_identity_owned_by_another_user(self):
        other, _ = make_user("08020000003", "otp-other@zitch.test")
        other.set_bvn("12345678901")
        other.save(update_fields=["bvn_hash", "bvn_last4"])
        with patch("banking.client.Client.initiate_identity") as send:
            response = self.identity_post(self.user, self.token)
        self.assertEqual(response.status_code, 400)
        send.assert_not_called()

    def test_bvn_otp_flow(self):
        result = self.complete_identity(self.user, self.token)
        self.assertEqual(result["tier"], 1)
        self.assertEqual(result["account_number"], f"19{self.user.pk:08d}")
        self.assertTrue(User.objects.get(pk=self.user.pk).bvn_verified)
        self.assertNotIn("654321", str(IdentityChallenge.objects.values().first()))
        self.assertEqual(IdentityChallenge.objects.get().encrypted_input, "")

    def test_bvn_otp_rejects_wrong_code(self):
        reference = self.pending_identity(self.user, self.token)
        with patch("banking.client.Client.verify_identity", return_value=Reply({"message": "FAILED"}, 200)), \
             patch("banking.client.Client.open_wallet") as opened:
            response = self.identity_confirm(self.token, reference, "000000")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["success"])
        self.assertEqual(IdentityChallenge.objects.get().failures, 1)
        opened.assert_not_called()
        self.assertFalse(User.objects.get(pk=self.user.pk).bvn_verified)

    def test_bvn_otp_burns_after_repeated_wrong_codes(self):
        reference = self.pending_identity(self.user, self.token)
        with patch("banking.client.Client.verify_identity", return_value=Reply({"message": "FAILED"}, 200)) as verify:
            for _ in range(5):
                self.assertFalse(self.identity_confirm(self.token, reference, "000000").json()["success"])
            self.assertEqual(verify.call_count, 5)
            response = self.identity_confirm(self.token, reference)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(verify.call_count, 5)
        self.assertEqual(IdentityChallenge.objects.get().encrypted_input, "")
        self.assertFalse(User.objects.get(pk=self.user.pk).bvn_verified)
        self.assertFalse(BankAccount.objects.exists())

    def test_bvn_rejects_bad_format(self):
        with patch("banking.client.Client.initiate_identity") as send:
            response = self.identity_post(self.user, self.token, identifier="123")
        self.assertEqual(response.status_code, 400)
        send.assert_not_called()

    def test_face_submission_cannot_set_durable_flag_without_verified_session(self):
        with patch("banking.client.Client.initiate_identity") as send:
            response = self.identity_post(self.user, self.token, method="FACIAL", image="ZmFrZQ==", verified=True)
        self.assertEqual(response.status_code, 400)
        send.assert_not_called()
        self.user.refresh_from_db()
        self.assertFalse(self.user.face_verified)
        self.assertFalse(IdentityChallenge.objects.exists())


class ClientIpTests(TestCase):
    @override_settings(RATELIMIT_TRUSTED_PROXY_HOPS=1)
    def test_uses_rightmost_forwarded_ip_to_reject_prepended_spoof(self):
        request = SimpleNamespace(META={
            "HTTP_X_FORWARDED_FOR": "203.0.113.200, 198.51.100.7",
            "REMOTE_ADDR": "10.0.0.2",
        })
        self.assertEqual(client_ip(request), "198.51.100.7")

    @override_settings(RATELIMIT_TRUSTED_PROXY_HOPS=0)
    def test_ignores_forwarded_header_when_proxy_trust_is_disabled(self):
        request = SimpleNamespace(META={
            "HTTP_X_FORWARDED_FOR": "203.0.113.200",
            "REMOTE_ADDR": "198.51.100.8",
        })
        self.assertEqual(client_ip(request), "198.51.100.8")


@override_settings(
    RATELIMIT_ENABLE=True,
    USER_LOGIN_MAX_FAILS=3,
    USER_LOGIN_LOCKOUT_SECONDS=900,
)
class UserLoginLockoutTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="08012345678",
            phone="08012345678",
            email="ada@zitch.test",
            password="Correct#pass1",
        )

    def tearDown(self):
        cache.clear()

    def signin(self, password):
        return self.client.post(
            "/api/sigin/",
            data=json.dumps({"email_or_phone": "ada@zitch.test", "password": password}),
            content_type="application/json",
        )

    def test_distributed_guesses_lock_account_identifier(self):
        for _ in range(3):
            self.assertEqual(self.signin("wrong-password").status_code, 401)
        self.assertEqual(self.signin("Correct#pass1").status_code, 429)

    def test_failed_signin_log_masks_identifier(self):
        with patch("accounts.views.log.warning") as warning:
            self.signin("wrong-password")
        self.assertEqual(warning.call_args.args[1], "a***@zitch.test")

    def test_inactive_account_cannot_receive_new_session(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        response = self.signin("Correct#pass1")
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("access_token", response.json())


@override_settings(RATELIMIT_ENABLE=True)
class RateLimitTests(TestCase):
    """Per-IP rate limiting (disabled elsewhere in the suite; on here)."""

    def setUp(self):
        self.client = Client()
        cache.clear()  # LocMemCache is process-shared and not auto-cleared

    def tearDown(self):
        cache.clear()

    def send(self, phone):
        return self.client.post(
            "/api/phone_verification/",
            data=json.dumps({"phone": phone, "email": "a@zitch.test"}),
            content_type="application/json",
        )

    def test_otp_send_is_ip_rate_limited(self):
        # Distinct phones (avoids the per-phone cooldown) from one IP: the 6th
        # request trips the per-IP "otp_send" limit of 5/min.
        for i in range(5):
            self.assertEqual(self.send(f"070100000{i:02d}").status_code, 200)
        self.assertEqual(self.send("07019999999").status_code, 429)

    def test_limiter_is_a_noop_when_disabled(self):
        with override_settings(RATELIMIT_ENABLE=False):
            for i in range(8):
                self.assertEqual(self.send(f"070200000{i:02d}").status_code, 200)

    def test_login_cache_keys_do_not_expose_customer_identifiers(self):
        key = _lockout_key("user", "ada.customer@example.com")
        self.assertNotIn("ada.customer", key)
        self.assertNotIn("example.com", key)
        self.assertEqual(key, _lockout_key("user", "ADA.CUSTOMER@example.com"))


class FullJourneyE2ETests(BankIdentityAPI, BankIOCase):
    """Signup, bank identity/NUBAN, confirmed funding and payments share one owner.

    All bank responses below are synthetic; this regression is not bank UAT.
    """
    PHONE, RECIP = "08099000001", "08099000002"

    def setUp(self):
        super().setUp()
        self.client = Client()
        from transfers.models import Bank
        Bank.objects.create(code="9psb", name="9PSB", bank_code="120001")
        self.enquiry.side_effect = lambda number, code: Reply({"code": "00", "customer": {"account": {
            "number": number, "bank": code, "name": BankAccount.objects.get(number=number).name}}}, 200)

    def post(self, path, **body):
        response = self.client.post(path, body, content_type="application/json")
        return response.status_code, response.json()

    def fund(self, user, amount, reference):
        from banking.notifications import ingest, process
        bank = BankAccount.objects.get(wallet__user=user)
        payload = {"code": "00", "message": "SUCCESS", "accountnumber": bank.number,
            "amount": str(amount), "nipsessionid": reference, "narration": "Test deposit"}
        notification = ingest("transfer", payload)
        with patch("banking.client.Client.notification", return_value=Reply({"status": "SUCCESS", "data": payload}, 200)):
            self.assertTrue(process(notification))
            self.assertTrue(process(ingest("transfer", payload)))
        wallet = get_or_create_wallet(user)
        BankAccount.objects.filter(pk=bank.pk).update(ledger_balance=wallet.balance, available_balance=wallet.balance)

    def test_full_user_journey(self):
        from wallet.models import Transaction
        from wallet.services import wallet_expected_balance
        from banking.models import Instruction
        with patch("accounts.views._otp_code", return_value="135790"):
            self.assertEqual(self.post("/api/phone_verification/", phone=self.PHONE, email="e2e@zitch.test")[0], 200)
        status, body = self.post("/api/verify_otp/", phone=self.PHONE, otp="135790", first_name="Ada", last_name="Eze")
        self.assertEqual(status, 200)
        token = body["access_token"]
        self.assertEqual(self.post("/api/set-password/", access_token=token, password="Passw0rd123!")[0], 200)
        self.assertEqual(self.post("/api/set-password/", email=self.PHONE, password="hacked12345")[0], 401)
        self.assertEqual(self.post("/api/set-transaction-pin/", access_token=token, pin="246810")[0], 200)
        status, body = self.post("/api/sigin/", email_or_phone=self.PHONE, password="Passw0rd123!")
        self.assertEqual(status, 200)
        token = body["access_token"]
        user = User.objects.get(phone=self.PHONE)
        with patch("banking.client.Client.initiate_identity") as verify:
            self.assertEqual(self.identity_post(user, token).status_code, 400)
        verify.assert_not_called()
        with patch("accounts.views._otp_code", return_value="909090"), patch("accounts.views._otp_on_cooldown", return_value=False):
            self.assertEqual(self.post("/api/email/verify/start/", access_token=token)[0], 200)
        self.assertEqual(self.post("/api/email/verify/confirm/", access_token=token, otp="909090")[0], 200)
        user.refresh_from_db()
        identity = self.complete_identity(user, token)
        self.assertEqual(identity["tier"], 1)
        self.assertEqual(identity["bank_name"], "9PSB")
        self.complete_identity(user, token, "nin", "10987654321")
        status, body = self.post("/api/banking/status/", access_token=token)
        self.assertEqual(status, 200)
        self.assertEqual(body["account_number"], identity["account_number"])
        self.assertTrue(body["bvn_verified"])
        self.assertTrue(body["nin_verified"])
        self.fund(user, "50000.00", "JOURNEY-DEPOSIT-1")
        status, body = self.post("/api/wallet_balance/", access_token=token)
        self.assertEqual(body["wallet"], "50000.00")
        self.assertIn("user_first_name", body)

        purchase = {"access_token": token, "kind": "airtime", "phone": self.PHONE, "amount": "1000",
            "transaction_pin": "246810", "idempotency_key": "journey-airtime-1"}
        with patch("banking.client.Client.network", return_value=Reply({"status": "success", "responseCode": "200", "data": {"network": "MTN"}}, 200)), \
             patch("banking.client.Client.topup", return_value=Reply({"status": "success", "responseCode": "200"}, 200)) as send:
            status, paid = self.post("/api/banking/purchase/", **purchase)
            self.assertEqual(status, 200)
            self.assertTrue(paid["success"])
            self.assertEqual(self.post("/api/banking/purchase/", **purchase)[1]["reference"], paid["reference"])
            send.assert_called_once()
        self.assertEqual(get_or_create_wallet(user).balance, Decimal("49000"))
        transactions = self.post("/api/user-transaction-history/", access_token=token)[1]["all_site_transactions"]
        self.assertTrue({"service", "amount", "transaction_status", "date"} <= set(transactions[0]))

        recipient, _ = make_bank_user(self.RECIP, "r@zitch.test")
        self.assertEqual(self.post("/api/transfer/resolve/", access_token=token, identifier=self.RECIP)[0], 200)
        transfer = {"access_token": token, "identifier": self.RECIP, "amount": "5000",
            "transaction_pin": "246810", "expected_fee": "0.00", "idempotency_key": "journey-p2p-1"}
        status, sent = self.post("/api/transfer/send/", **transfer)
        self.assertEqual(status, 200, sent)
        self.assertEqual(self.post("/api/transfer/send/", **transfer)[1]["reference"], sent["reference"])
        self.bank_send.assert_called_once()
        self.assertEqual(get_or_create_wallet(user).balance, Decimal("44000"))
        # A successful debit cannot manufacture the recipient's bank inflow.
        self.assertEqual(get_or_create_wallet(recipient).balance, Decimal("0"))
        self.fund(recipient, "5000.00", "JOURNEY-RECIPIENT-INFLOW")
        self.assertEqual(get_or_create_wallet(recipient).balance, Decimal("5000"))
        self.assertEqual(self.post("/api/transfer/send/", **{**transfer, "amount": "150000",
            "idempotency_key": "journey-tier-blocked"})[0], 403)

        self.approve_tier2(user, token)
        status, address = self.post("/api/banking/address/start/", access_token=token, address={"street": "Test Road"})
        self.assertEqual(status, 400)
        self.assertFalse(address.get("success"))
        self.assertFalse(AddressVerification.objects.exists())
        user.refresh_from_db()
        self.assertEqual(user.tier, 2)
        self.assertFalse(user.address_verified)
        self.assertEqual(Instruction.objects.count(), 2)
        self.assertEqual(Transaction.objects.filter(user=user, direction=Transaction.OUT).count(), 2)
        for owner in (user, recipient):
            self.assertEqual(get_or_create_wallet(owner).balance, wallet_expected_balance(owner.pk))
        self.assertEqual(self.post("/api/banking/catalog/", kind="network", phone=self.PHONE)[0], 401)


@override_settings(NINEPSB=CONFIG)
class KycStatusRepairTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="08070000001",
            phone="08070000001",
            email="kyc-repair@zitch.test",
            password="Passw0rd123!",
        )
        self.user.bvn_verified = True
        self.user.nin_verified = True
        self.user.recompute_tier()
        self.user.save(update_fields=["bvn_verified", "nin_verified", "tier"])

    @override_settings(WEMA={"BASE_URL": "https://alat.test", "CHANNEL_ID": "chan",
                             "KEYS": {"wallet": "wallet-key"}, "SIMULATION": False})
    def test_kyc_status_never_clears_existing_verified_flags(self):
        """Historical flags survive, but cannot stand in for new bank proof."""
        from accounts.views import _kyc_state

        state = _kyc_state(self.user)
        self.user.refresh_from_db()

        self.assertFalse(state["bvn_verified"])
        self.assertFalse(state["nin_verified"])
        self.assertTrue(self.user.bvn_verified)
        self.assertTrue(self.user.nin_verified)

    @override_settings(WEMA={"BASE_URL": "https://alat.test", "CHANNEL_ID": "chan",
                             "KEYS": {"wallet": "wallet-key"}, "SIMULATION": False})
    def test_kyc_status_preserves_historical_proof_without_granting_new_bank_authority(self):
        from accounts.models import IdentityProof, record_identity_proof
        from accounts.views import _kyc_state

        self.user.bvn_verified = False
        self.user.nin_verified = False
        self.user.tier = 0
        self.user.save(update_fields=["bvn_verified", "nin_verified", "tier"])
        record_identity_proof(self.user, IdentityProof.BVN, "12345678901",
                              source=IdentityProof.WEMA_WALLET_OTP)

        state = _kyc_state(self.user)
        self.user.refresh_from_db()

        self.assertFalse(state["bvn_verified"])
        self.assertFalse(self.user.bvn_verified)
        self.assertFalse(state["nin_verified"])
        self.assertTrue(IdentityProof.objects.filter(user=self.user,
            source=IdentityProof.WEMA_WALLET_OTP).exists())

    def test_kyc_status_reads_current_bank_proof(self):
        from accounts.models import record_identity_proof
        from accounts.views import _kyc_state
        record_identity_proof(self.user, "bvn", "12345678901", source="9psb_otp")
        state = _kyc_state(self.user)
        self.assertTrue(state["bvn_verified"])
        self.assertFalse(state["nin_verified"])

    @override_settings(WEMA={"BASE_URL": "https://alat.test", "CHANNEL_ID": "chan",
                             "KEYS": {"wallet": "wallet-key"}, "SIMULATION": False})
    def test_kyc_status_does_not_trust_legacy_verified_attempt_without_proof(self):
        from accounts.views import _kyc_state
        from wallet.models import WemaProvisioningAttempt

        self.user.bvn_verified = False
        self.user.bvn_hash = ""
        self.user.bvn_last4 = ""
        self.user.tier = 0
        self.user.save(update_fields=["bvn_verified", "bvn_hash", "bvn_last4", "tier"])
        WemaProvisioningAttempt.objects.create(
            user=self.user,
            tracking_id="TRK-REPAIR-BVN",
            identity_type=WemaProvisioningAttempt.BVN,
            identity_hash="a" * 64,
            identity_last4="8901",
            status=WemaProvisioningAttempt.VERIFIED,
            expires_at=timezone.now() - timedelta(minutes=1),
        )

        state = _kyc_state(self.user)
        self.user.refresh_from_db()

        self.assertFalse(state["bvn_verified"])
        self.assertFalse(self.user.bvn_verified)
        self.assertEqual(self.user.bvn_hash, "")
        self.assertEqual(self.user.bvn_last4, "")


class TransactionPinLockoutTests(BankIOCase):
    """A stolen session token must not be usable to brute-force the short
    transaction PIN that gates money movement. The lock is per-user, so it can't
    be sidestepped by switching to a different money endpoint."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.user, self.token = make_bank_user("08010000001", "ada@zitch.test", pin="1234", balance="20000")
        recipient, _ = make_bank_user("08020000002", "bob@zitch.test")
        from transfers.models import Bank
        Bank.objects.create(code="9psb", name="9PSB", bank_code="120001")
        self.enquiry.side_effect = lambda number, code: Reply({"code": "00", "customer": {"account": {
            "number": number, "bank": code, "name": recipient.get_full_name()}}}, 200)
        self._transfer_sequence = 0

    def post(self, path, payload):
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    def transfer(self, pin):
        self._transfer_sequence += 1
        return self.post("/api/transfer/send/", {
            "access_token": self.token, "identifier": "08020000002",
            "amount": "1000", "transaction_pin": pin,
            "expected_fee": "0.00",
            "idempotency_key": f"pin-lock-transfer-{self._transfer_sequence}",
        })

    def balance(self):
        return get_or_create_wallet(self.user).balance

    def test_pin_locks_after_max_attempts_and_then_blocks_correct_pin(self):
        # The first MAX-1 wrong PINs are rejected as 'incorrect' (with a count).
        for _ in range(User.PIN_MAX_ATTEMPTS - 1):
            res, body = self.transfer("0000")
            self.assertEqual(res.status_code, 403)
            self.assertEqual(body.get("code"), "pin_incorrect")
        # The MAX-th wrong PIN trips the lock.
        res, body = self.transfer("0000")
        self.assertEqual(res.status_code, 429)
        self.assertEqual(body.get("code"), "pin_locked")
        # While locked, even the CORRECT PIN is refused — no money moves.
        res, body = self.transfer("1234")
        self.assertEqual(res.status_code, 429)
        self.assertEqual(body.get("code"), "pin_locked")
        self.assertEqual(self.balance(), Decimal("20000"))

    def test_lockout_is_per_user_not_per_endpoint(self):
        # Trip the lock on the transfer endpoint...
        for _ in range(User.PIN_MAX_ATTEMPTS):
            self.transfer("0000")
        # ...and a *different* money endpoint is locked too, even with the right
        # PIN — so an attacker can't just hop endpoints to keep guessing.
        res, body = self.post("/api/savings/create/", {
            "access_token": self.token, "amount": "5000", "days": 90, "transaction_pin": "1234",
            "idempotency_key": "pin-lock-savings-1",
        })
        self.assertEqual(res.status_code, 429)
        self.assertEqual(body.get("code"), "pin_locked")

    def test_correct_pin_resets_the_failure_counter(self):
        # A burst of wrong tries short of the cap...
        for _ in range(User.PIN_MAX_ATTEMPTS - 1):
            self.assertEqual(self.transfer("0000")[0].status_code, 403)
        # ...then a correct PIN succeeds and clears the count.
        self.assertEqual(self.transfer("1234")[0].status_code, 200)
        # So the next wrong PIN is 'incorrect' again, not an immediate lock.
        res, body = self.transfer("0000")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(body.get("code"), "pin_incorrect")

    def test_lock_expires_after_the_window(self):
        for _ in range(User.PIN_MAX_ATTEMPTS):
            self.transfer("0000")
        self.assertEqual(self.transfer("1234")[0].status_code, 429)  # locked
        # Age the lock into the past; the next correct PIN is accepted.
        User.objects.filter(pk=self.user.pk).update(
            pin_locked_until=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.transfer("1234")[0].status_code, 200)


class EscalatingPinLockoutTests(TestCase):
    """Five wrong PINs cost an hour; five more, without a correct PIN in between,
    cost a day and are told how to get out of it.

    Driven through `evaluate_transaction_pin` rather than a money endpoint on
    purpose: that function IS the gate — the app, the chat and the encrypted
    Flow all reach the lockout through it, so pinning the ladder here pins it
    for every surface at once.
    """

    def setUp(self):
        self.user, _ = make_user("08010000001", "ada@zitch.test", pin="1234", balance="20000")

    def _lock(self):
        """Burn a full round of wrong PINs and return the final refusal."""
        from common.http import evaluate_transaction_pin

        for _ in range(User.PIN_MAX_ATTEMPTS - 1):
            evaluate_transaction_pin(self.user, "0000")
        return evaluate_transaction_pin(self.user, "0000")

    def _expire_lock(self):
        """Serve the lock out, without serving it out."""
        User.objects.filter(pk=self.user.pk).update(
            pin_locked_until=timezone.now() - timedelta(seconds=1))
        self.user.refresh_from_db()

    def _held_minutes(self):
        u = User.objects.get(pk=self.user.pk)
        return round((u.pin_locked_until - timezone.now()).total_seconds() / 60)

    def test_the_first_lock_is_one_hour(self):
        ok, code, message = self._lock()
        self.assertEqual((ok, code), (False, "pin_locked"))
        self.assertAlmostEqual(self._held_minutes(), User.PIN_LOCKOUT_MINUTES, delta=1)
        self.assertEqual(User.objects.get(pk=self.user.pk).pin_lockout_strikes, 1)
        # A reset clears any lock outright and is reachable regardless of tier
        # (see set_transaction_pin), so it is offered from the FIRST lock —
        # withholding it here only made an honest slip cost an hour of waiting
        # before the customer learned they never had to.
        self.assertIn("about an hour", message)
        self.assertIn("reset your PIN", message)

    def test_a_second_round_after_the_first_lock_expires_costs_a_day(self):
        self._lock()
        self._expire_lock()
        ok, code, message = self._lock()
        self.assertEqual((ok, code), (False, "pin_locked"))
        self.assertAlmostEqual(self._held_minutes(),
                               User.PIN_LOCKOUT_ESCALATED_MINUTES, delta=1)
        self.assertEqual(User.objects.get(pk=self.user.pk).pin_lockout_strikes, 2)
        # And a day IS sold as a reset — waiting it out is not a real instruction.
        self.assertIn("about 24 hours", message)
        self.assertIn("reset your PIN", message)

    def test_the_ladder_stops_at_a_day_rather_than_growing_without_bound(self):
        """A third and fourth round must not compound into a week. Past the
        escalated tier the answer is the PIN reset, not a longer sentence."""
        for _ in range(4):
            self._lock()
            self._expire_lock()
        self._lock()
        self.assertAlmostEqual(self._held_minutes(),
                               User.PIN_LOCKOUT_ESCALATED_MINUTES, delta=1)

    def test_a_correct_pin_between_rounds_puts_the_next_lock_back_at_an_hour(self):
        """Knowing the PIN is exactly what separates a customer who forgot it
        from someone working through the keyspace, so it resets the ladder."""
        from common.http import evaluate_transaction_pin

        self._lock()
        self._expire_lock()
        self.assertTrue(evaluate_transaction_pin(self.user, "1234")[0])
        self.assertEqual(User.objects.get(pk=self.user.pk).pin_lockout_strikes, 0)

        self._lock()
        self.assertAlmostEqual(self._held_minutes(), User.PIN_LOCKOUT_MINUTES, delta=1)

    def test_a_correct_pin_is_still_refused_while_the_lock_stands(self):
        """The escalation must not have turned the lock into a warning: the
        whole point is that no PIN — right or wrong — moves money until it ends."""
        from common.http import evaluate_transaction_pin

        self._lock()
        ok, code, message = evaluate_transaction_pin(self.user, "1234")
        self.assertEqual((ok, code), (False, "pin_locked"))
        # Re-hitting the lock quotes the time LEFT, not the tier's nominal
        # length, and says the same thing the lock itself said.
        self.assertIn("about an hour", message)

    def test_a_wrong_pin_while_locked_does_not_extend_the_lock(self):
        """Hammering a locked account must not push the release further out —
        that turns a day into an unbounded lockout held open by the attacker."""
        from common.http import evaluate_transaction_pin

        self._lock()
        held = User.objects.get(pk=self.user.pk).pin_locked_until
        for _ in range(10):
            evaluate_transaction_pin(self.user, "0000")
        u = User.objects.get(pk=self.user.pk)
        self.assertEqual(u.pin_locked_until, held)
        self.assertEqual(u.pin_lockout_strikes, 1)

    def test_resetting_the_pin_is_the_advertised_way_out_and_actually_works(self):
        """The escalated message promises a new PIN unlocks payments straight
        away. Anything less makes the message a lie at the worst moment."""
        from common.http import evaluate_transaction_pin

        self._lock()
        self._expire_lock()
        self._lock()                                   # 24-hour tier, still standing
        self.assertTrue(User.objects.get(pk=self.user.pk).pin_lock_is_escalated)

        self.user.set_transaction_pin("975310")
        self.user.save(update_fields=list(User.PIN_UPDATE_FIELDS))

        u = User.objects.get(pk=self.user.pk)
        self.assertIsNone(u.pin_locked_until)
        self.assertEqual((u.pin_failed_attempts, u.pin_lockout_strikes), (0, 0))
        self.assertTrue(evaluate_transaction_pin(u, "975310")[0])

    def test_the_escalated_flag_is_about_the_lock_standing_now(self):
        """Admin surfaces read this flag to show which tier is active, so it must
        go quiet the moment the lock lapses — otherwise an unlocked customer's
        record still reads as sitting out a 24-hour lock."""
        self._lock()
        self._expire_lock()
        self._lock()
        self.assertTrue(User.objects.get(pk=self.user.pk).pin_lock_is_escalated)
        self._expire_lock()
        self.assertFalse(User.objects.get(pk=self.user.pk).pin_lock_is_escalated)

    def test_the_gate_reports_the_new_lock_state_on_the_instance_it_was_given(self):
        """The counting happens on a `select_for_update()` copy, so the caller's
        own object is a different one. Anything a caller reads off `user` after
        the call — `pin_locked`, `pin_lockout_strikes`, `pin_lock_is_escalated` —
        must reflect what the row-locked copy just wrote, or a caller acts on
        state a moment out of date. A stale value here is invisible: nothing
        errors, it just quietly disagrees with the database."""
        from common.http import evaluate_transaction_pin

        self._lock()
        self.assertEqual(self.user.pin_lockout_strikes, 1)
        self.assertTrue(self.user.pin_locked)
        self.assertFalse(self.user.pin_lock_is_escalated)

        self._expire_lock()
        self._lock()
        self.assertEqual(self.user.pin_lockout_strikes, 2)
        self.assertTrue(self.user.pin_lock_is_escalated)

        # And the clearing direction too, or a caller keeps showing a lock that
        # the correct PIN it just accepted has already lifted.
        self._expire_lock()
        self.assertTrue(evaluate_transaction_pin(self.user, "1234")[0])
        self.assertEqual((self.user.pin_lockout_strikes, self.user.pin_locked_until), (0, None))

    def test_the_counter_still_names_the_attempts_left_before_the_lock(self):
        from common.http import evaluate_transaction_pin

        ok, code, message = evaluate_transaction_pin(self.user, "0000")
        self.assertEqual((ok, code), (False, "pin_incorrect"))
        self.assertIn(f"{User.PIN_MAX_ATTEMPTS - 1} attempt(s) left", message)


class SessionRevocationTests(TestCase):
    """Tokens must be revocable server-side: logout invalidates the presented
    token, and a password change invalidates other (possibly stolen) sessions."""

    def setUp(self):
        self.client = Client()

    def post(self, path, token, **payload):
        res = self.client.post(path, data=json.dumps({"access_token": token, **payload}),
                               content_type="application/json")
        return res, res.json()

    def test_logout_revokes_the_presented_token(self):
        _, token = make_user("08010000001", "a@zitch.test")
        self.assertEqual(self.post("/api/wallet_balance/", token)[0].status_code, 200)
        self.assertEqual(self.post("/api/logout/", token)[0].status_code, 200)
        self.assertEqual(self.post("/api/wallet_balance/", token)[0].status_code, 401)

    def test_password_change_revokes_other_sessions_but_keeps_current(self):
        user, old_token = make_user("08010000001", "a@zitch.test")
        new_token = AccessToken.issue(user).key  # a second device/session
        self.assertEqual(self.post("/api/set-password/", new_token, password="Passw0rd123!")[0].status_code, 200)
        # The other session is revoked...
        self.assertEqual(self.post("/api/wallet_balance/", old_token)[0].status_code, 401)
        # ...but the one that changed the password stays signed in.
        self.assertEqual(self.post("/api/wallet_balance/", new_token)[0].status_code, 200)

    def test_the_endpoint_enforces_the_rule_the_signup_screen_draws(self):
        """The "a letter, a number, a special character" tick-boxes were CLIENT
        side only, so the API accepted passwords the app would not let anyone
        type. Both doors now call one rule — WhatsApp signup is the second door,
        and two rules would have meant the weaker one is the one that gets used.
        """
        user, _ = make_user("08010000002", "b@zitch.test")
        token = AccessToken.issue(user).key
        for weak in ("abcdefgh", "abcdefg1", "Passw0rd123"):
            res, body = self.post("/api/set-password/", token, password=weak)
            self.assertEqual(res.status_code, 400, weak)
            self.assertFalse(User.objects.get(pk=user.pk).check_password(weak), weak)
        self.assertEqual(
            self.post("/api/set-password/", token, password="Zitch!2026pay")[0].status_code,
            200)


class PasswordRecoveryTests(TestCase):
    """OTP-based password reset for users who can't sign in. Reset codes are a
    distinct OTP purpose, so they can't be replayed on the signup verifier."""

    RESET_CODE = "246813"

    def setUp(self):
        self.client = Client()
        # Codes are stored hashed, so pin the generator to a known value for the
        # whole class; _reset_code then returns that value instead of reading the row.
        patcher = patch("accounts.views._otp_code", return_value=self.RESET_CODE)
        patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, path, **payload):
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    def _reset_code(self, phone):
        return self.RESET_CODE

    def test_forgot_sends_reset_code_for_a_registered_phone(self):
        make_user("08010000001", "a@zitch.test")
        res, _ = self.post("/api/password/forgot/", phone="08010000001")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(OTP.objects.filter(phone="08010000001", purpose=OTP.RESET).exists())

    def test_forgot_does_not_reveal_an_unknown_number(self):
        res, _ = self.post("/api/password/forgot/", phone="07000000000")
        self.assertEqual(res.status_code, 200)  # same generic response...
        self.assertFalse(OTP.objects.filter(phone="07000000000").exists())  # ...but no code issued

    def test_reset_sets_new_password_revokes_sessions_and_returns_token(self):
        user, old_token = make_user("08010000001", "a@zitch.test")
        self.post("/api/password/forgot/", phone="08010000001")
        res, body = self.post("/api/password/reset/", phone="08010000001",
                              otp=self._reset_code("08010000001"), password="NewPassw0rd1!")
        self.assertEqual(res.status_code, 200)
        self.assertIn("access_token", body)
        self.assertEqual(body["account_namespace"], views.spend_account_namespace(user))
        self.assertTrue(User.objects.get(pk=user.pk).check_password("NewPassw0rd1!"))
        # Old session is revoked; the freshly issued one works.
        self.assertEqual(self._auth(old_token), 401)
        self.assertEqual(self._auth(body["access_token"]), 200)

    def test_reset_rejects_a_wrong_code(self):
        make_user("08010000001", "a@zitch.test")
        self.post("/api/password/forgot/", phone="08010000001")
        res, _ = self.post("/api/password/reset/", phone="08010000001", otp="000000", password="NewPassw0rd1!")
        self.assertEqual(res.status_code, 400)

    def test_forgot_and_reset_work_with_an_email_identifier(self):
        user, _ = make_user("08010000001", "a@zitch.test")
        # Request + reset using the email (not the phone) as the identifier.
        self.post("/api/password/forgot/", email_or_phone="a@zitch.test")
        self.assertTrue(OTP.objects.filter(phone="08010000001", purpose=OTP.RESET).exists())
        res, body = self.post("/api/password/reset/", email_or_phone="a@zitch.test",
                              otp=self._reset_code("08010000001"), password="NewPassw0rd1!")
        self.assertEqual(res.status_code, 200)
        self.assertIn("access_token", body)
        self.assertTrue(User.objects.get(pk=user.pk).check_password("NewPassw0rd1!"))

    def test_signup_verifier_will_not_honour_a_reset_code(self):
        make_user("08010000001", "a@zitch.test")
        self.post("/api/password/forgot/", phone="08010000001")
        res, _ = self.post("/api/verify_otp/", phone="08010000001", otp=self._reset_code("08010000001"))
        self.assertEqual(res.status_code, 400)  # reset code is not a signup/login code

    def _auth(self, token):
        return self.client.post("/api/wallet_balance/", data=json.dumps({"access_token": token}),
                                content_type="application/json").status_code


@override_settings(NINEPSB=CONFIG)
class ChatOnboardedUpgradeTests(BankIdentityAPI, TestCase):
    """The app-side upgrade contract for WhatsApp-onboarded accounts: both contact
    channels must be re-proven before the KYC ladder opens. The phone re-proves
    itself on the way in (no usable password, so entering the app runs the OTP
    password reset against the same number); the email was typed into a chat and
    is one typo from being someone else's inbox, so it gets its own round-trip —
    and until then it must never receive a password-reset code."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create(
            username="08155550001", phone="08155550001", first_name="Chidi", last_name="Eze",
            email="chidi@zitch.test", tier=0,
            onboarded_via_whatsapp=True, email_verified=False,
        )
        self.user.set_unusable_password()
        self.user.save()
        self.token = AccessToken.issue(self.user).key

    def post(self, path, payload):
        payload = {"access_token": self.token, **payload}
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    def test_bank_identity_requires_both_contact_channels(self):
        with patch("banking.client.Client.initiate_identity") as send:
            response = self.identity_post(self.user, self.token)
        self.assertEqual(response.status_code, 400)
        send.assert_not_called()
        self.assertFalse(IdentityChallenge.objects.exists())

    def test_kyc_status_reports_the_current_bank_email_requirement(self):
        res, body = self.post("/api/kyc/status/", {})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["email_verification_required"])
        self.assertFalse(body["identity_available"])

    def test_email_round_trip_opens_the_ladder(self):
        with patch("accounts.views._otp_code", return_value="424242"):
            self.post("/api/email/verify/start/", {})
        res, body = self.post("/api/email/verify/confirm/", {"otp": "424242"})
        self.assertEqual(res.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)
        self.assertFalse(body["email_verification_required"])
        # The email OTP does not also prove the chat-claimed phone number.
        with patch("banking.client.Client.initiate_identity") as send:
            self.assertEqual(self.identity_post(self.user, self.token).status_code, 400)
        send.assert_not_called()
        self.user.phone_verified = True
        self.user.save(update_fields=["phone_verified"])
        self.pending_identity(self.user, self.token)

    def test_a_wrong_code_does_not_verify(self):
        with patch("accounts.views._otp_code", return_value="424242"):
            self.post("/api/email/verify/start/", {})
        res, _ = self.post("/api/email/verify/confirm/", {"otp": "000000"})
        self.assertEqual(res.status_code, 400)
        self.user.refresh_from_db()
        self.assertFalse(self.user.email_verified)

    def test_a_reset_code_cannot_stand_in_for_inbox_control(self):
        """purpose=EMAIL is filtered: a code minted for password reset must not
        mark the email verified."""
        from accounts.models import OTP

        with patch("accounts.views._otp_code", return_value="424242"):
            self.client.post("/api/password/forgot/",
                             data=json.dumps({"email_or_phone": "08155550001"}),
                             content_type="application/json")
        res, _ = self.post("/api/email/verify/confirm/", {"otp": "424242"})
        self.assertEqual(res.status_code, 400)

    def test_unverified_chat_email_never_receives_a_reset_code(self):
        with patch("accounts.views.send_email") as email, \
             patch("accounts.views.send_sms") as sms, \
             patch("accounts.views._otp_code", return_value="424242"):
            self.client.post("/api/password/forgot/",
                             data=json.dumps({"email_or_phone": "08155550001"}),
                             content_type="application/json")
        self.assertTrue(sms.called)                      # the phone is the channel
        self.assertEqual(email.call_args[0][0], "")      # the inbox gets nothing

    def test_a_verified_email_receives_reset_codes_again(self):
        self.user.email_verified = True
        self.user.save(update_fields=["email_verified"])
        with patch("accounts.views.send_email") as email, \
             patch("accounts.views._otp_code", return_value="424242"):
            self.client.post("/api/password/forgot/",
                             data=json.dumps({"email_or_phone": "08155550001"}),
                             content_type="application/json")
        self.assertEqual(email.call_args[0][0], "chidi@zitch.test")

    def test_app_signup_also_requires_contact_verification_for_bank_identity(self):
        plain = User.objects.create(username="08155550002", phone="08155550002",
            first_name="Ada", last_name="Eze", email="plain@zitch.test")
        token = AccessToken.issue(plain).key
        with patch("banking.client.Client.initiate_identity") as send:
            response = self.identity_post(plain, token)
        self.assertEqual(response.status_code, 400)
        send.assert_not_called()
        # Keep the existing app-account recovery policy independent of bank KYC.
        with patch("accounts.views.send_email") as email, patch("accounts.views._otp_code", return_value="424242"):
            self.client.post("/api/password/forgot/", {"email_or_phone": plain.phone}, content_type="application/json")
        self.assertEqual(email.call_args[0][0], "plain@zitch.test")



class EmailVerificationReportsSuccessTests(TestCase):
    """The email endpoints must set `success` — the app branches on it.

    Every other KYC endpoint passes success=True; these two did not, and nothing
    caught it because the existing tests assert status codes and side effects only.
    The cost was total: the app advances to the code entry on `res.success`, so
    start() mailed the code and then reported a failure — rendering its own success
    message inside an error, since the screen falls back to res.message — and
    confirm() was unreachable behind it. Email gates Tier 1, so no app customer
    could climb the ladder at all.

    Pinned as behaviour rather than fixed in ok(): a 200 there does NOT universally
    mean success. A queued or ambiguously-timed-out payout answers 200 with
    `pending` and no `success`, exactly so the app says "processing" instead of
    "sent", and defaulting the flag would report money as delivered mid-flight.
    """

    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08010000077", "e@zitch.test",
                                          identity_verified=False)
        self.user.email_verified = False
        self.user.save(update_fields=["email_verified"])

    def post(self, path, payload):
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    def test_sending_the_code_reports_success(self):
        with patch("accounts.views._otp_code", return_value="909090"):
            res, body = self.post("/api/email/verify/start/", {"access_token": self.token})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body.get("success"))

    def test_confirming_the_code_reports_success(self):
        with patch("accounts.views._otp_code", return_value="909090"):
            self.post("/api/email/verify/start/", {"access_token": self.token})
        res, body = self.post("/api/email/verify/confirm/",
                              {"access_token": self.token, "otp": "909090"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body.get("success"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)

    def test_an_already_verified_email_reports_success(self):
        # Re-entering the step must not read as a failure either.
        self.user.email_verified = True
        self.user.save(update_fields=["email_verified"])
        res, body = self.post("/api/email/verify/start/", {"access_token": self.token})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body.get("success"))
