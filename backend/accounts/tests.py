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

from betting.models import BettingPlatform
from exams.models import ExamProduct
from wallet.services import get_or_create_wallet
from wallet.tests import make_user
from common.ratelimit import client_ip

from .models import OTP, AccessToken

User = get_user_model()


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
        victim.set_password("Passw0rd123")  # a real account has a usable password
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
            "access_token": atk_token, "email": "victim@zitch.test", "password": "newpass12345",
        })
        self.assertEqual(res.status_code, 200)
        self.assertEqual(User.objects.get(pk=victim.pk).password, victim_hash)  # untouched
        self.assertTrue(User.objects.get(pk=attacker.pk).check_password("newpass12345"))

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
        res, body = self.post("/api/set-password/", {"access_token": token, "password": "Newpass456"})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(body.get("code"), "current_password_required")
        self.assertTrue(User.objects.get(pk=user.pk).check_password("Oldpass123"))

        # Wrong current password -> refused.
        res, _ = self.post("/api/set-password/", {
            "access_token": token, "password": "Newpass456", "current_password": "nope12345"})
        self.assertEqual(res.status_code, 403)
        self.assertTrue(User.objects.get(pk=user.pk).check_password("Oldpass123"))

        # Correct current password -> changed.
        res, _ = self.post("/api/set-password/", {
            "access_token": token, "password": "Newpass456", "current_password": "Oldpass123"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(User.objects.get(pk=user.pk).check_password("Newpass456"))

    def test_set_pin_requires_auth_and_sets_owner(self):
        res, _ = self.post("/api/set-transaction-pin/", {"email": "x@zitch.test", "pin": "135790"})
        self.assertEqual(res.status_code, 401)
        # First-time PIN set (no existing PIN) needs only the session token.
        user = User.objects.create(username="08040000004", phone="08040000004", email="d@zitch.test")
        token = AccessToken.issue(user).key
        res, _ = self.post("/api/set-transaction-pin/", {"access_token": token, "pin": "135790"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(User.objects.get(pk=user.pk).check_transaction_pin("135790"))

    def test_changing_existing_pin_requires_current_pin_or_password(self):
        # A token alone must not be enough to OVERWRITE an existing PIN (else the
        # brute-force lockout is moot — an attacker would just reset the PIN).
        user = User.objects.create(username="08050000005", phone="08050000005", email="e@zitch.test")
        user.set_password("Passw0rd123")
        user.set_transaction_pin("1234")
        user.save()
        token = AccessToken.issue(user).key
        # No proof at all -> rejected.
        res, body = self.post("/api/set-transaction-pin/", {"access_token": token, "pin": "999999"})
        self.assertEqual((res.status_code, body.get("code")), (403, "current_pin_required"))
        self.assertTrue(User.objects.get(pk=user.pk).check_transaction_pin("1234"))  # unchanged
        # A WRONG current PIN is rejected too.
        res, body = self.post("/api/set-transaction-pin/", {
            "access_token": token, "pin": "999999", "old_pin": "0000"})
        self.assertEqual(res.status_code, 403)
        self.assertTrue(User.objects.get(pk=user.pk).check_transaction_pin("1234"))  # unchanged
        # With the CURRENT PIN, the change goes through.
        res, _ = self.post("/api/set-transaction-pin/", {
            "access_token": token, "pin": "999999", "old_pin": "1234"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(User.objects.get(pk=user.pk).check_transaction_pin("999999"))
        # The account password remains a valid fallback (forgot-PIN recovery).
        res, _ = self.post("/api/set-transaction-pin/", {
            "access_token": token, "pin": "432100", "password": "Passw0rd123"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(User.objects.get(pk=user.pk).check_transaction_pin("432100"))

    def test_setting_new_pin_clears_brute_force_lockout(self):
        # A user who locked their PIN and then legitimately changes it (which
        # requires the password) must not stay locked out against the new PIN.
        user = User.objects.create(username="08060000006", phone="08060000006", email="f@zitch.test")
        user.set_password("Passw0rd123")
        user.set_transaction_pin("1234")
        user.pin_failed_attempts = 5
        user.pin_locked_until = timezone.now() + timedelta(minutes=15)
        user.save()
        token = AccessToken.issue(user).key
        res, _ = self.post("/api/set-transaction-pin/", {
            "access_token": token, "pin": "567890", "password": "Passw0rd123"})
        self.assertEqual(res.status_code, 200)
        u = User.objects.get(pk=user.pk)
        self.assertEqual(u.pin_failed_attempts, 0)
        self.assertIsNone(u.pin_locked_until)
        self.assertTrue(u.check_transaction_pin("567890"))

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
        # Starts where a real account does — the point of these tests is the
        # ladder being climbed, so nothing may be pre-granted.
        self.user, self.token = make_user("08010000001", "a@zitch.test",
                                         identity_verified=False)

    def post(self, path, payload):
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    def test_bvn_plus_nin_promote_to_tier_1(self):
        # New ladder: BVN + NIN together = Tier 1 (BVN alone stays Tier 0).
        r, b0 = self.post("/api/kyc/bvn/", {"access_token": self.token, "bvn": "12345678901"})
        self.assertEqual(b0["tier"], 0)  # BVN only -> still Tier 0
        res, body = self.post("/api/kyc/nin/", {"access_token": self.token, "nin": "10987654321"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(body["tier"], 1)

    def test_an_unverified_phone_holds_the_tier_at_zero(self):
        """Both contact channels are required. A WhatsApp signup does not earn
        phone_verified from possession of the chat — a messenger session outlives
        a SIM swap — so this can genuinely be the missing piece."""
        self.user.phone_verified = False
        self.user.save(update_fields=["phone_verified"])
        self.post("/api/kyc/bvn/", {"access_token": self.token, "bvn": "12345678901"})
        body = self.post("/api/kyc/nin/", {"access_token": self.token, "nin": "10987654321"})[1]
        self.assertEqual(body["tier"], 0)
        self.assertFalse(body["phone_verified"])
        # Re-read: BVN/NIN were set server-side, so the local copy is stale.
        self.user.refresh_from_db()
        self.user.phone_verified = True
        self.user.recompute_tier()
        self.assertEqual(self.user.tier, 1)

    def test_unverified_email_holds_the_tier_at_zero(self):
        # BVN + NIN complete, email not confirmed: the ladder must not move —
        # Tier 1 requires all three, however the account signed up.
        self.user.email_verified = False
        self.user.save(update_fields=["email_verified"])
        self.post("/api/kyc/bvn/", {"access_token": self.token, "bvn": "12345678901"})
        res, body = self.post("/api/kyc/nin/", {"access_token": self.token, "nin": "10987654321"})
        self.assertEqual(body["tier"], 0)
        with patch("accounts.views._otp_code", return_value="909090"):
            self.post("/api/email/verify/start/", {"access_token": self.token})
        body = self.post("/api/email/verify/confirm/", {"access_token": self.token, "otp": "909090"})[1]
        self.assertEqual(body["tier"], 1)

    def test_email_can_be_set_while_unverified(self):
        # A blank or mistyped address must not strand the account below Tier 1:
        # start() accepts a replacement for as long as the email is unverified.
        self.user.email, self.user.email_verified = "", False
        self.user.save(update_fields=["email", "email_verified"])
        res, body = self.post("/api/email/verify/start/", {"access_token": self.token})
        self.assertEqual(res.status_code, 400)  # nothing on file, none supplied
        with patch("accounts.views._otp_code", return_value="909090"):
            res, body = self.post("/api/email/verify/start/",
                                  {"access_token": self.token, "email": "Ada.New@Zitch.test"})
        self.assertEqual(res.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "ada.new@zitch.test")
        self.post("/api/email/verify/confirm/", {"access_token": self.token, "otp": "909090"})
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)

    def test_full_kyc_ladder_to_tier_3(self):
        # BVN+NIN -> Tier 1; + face + address -> Tier 2; + government ID -> Tier 3.
        self.post("/api/kyc/bvn/", {"access_token": self.token, "bvn": "12345678901"})
        b1 = self.post("/api/kyc/nin/", {"access_token": self.token, "nin": "10987654321"})[1]
        self.assertEqual(b1["tier"], 1)
        self.post("/api/kyc/face/", {"access_token": self.token})
        b2 = self.post("/api/kyc/address/", {"access_token": self.token, "address": "12 Allen Avenue", "city": "Ikeja", "state": "Lagos", "document": "ZmFrZQ=="})[1]
        self.assertEqual(b2["tier"], 2)
        self.assertTrue(b2["address_verified"] and b2["face_verified"])
        b3 = self.post("/api/kyc/id/", {"access_token": self.token, "image": "ZmFrZQ==", "doc_type": "passport"})[1]
        self.assertEqual(b3["tier"], 3)
        self.assertTrue(b3["id_document_verified"])

    def test_address_without_proof_document_is_refused(self):
        """Typed text is a claim, not evidence. Tier 2 raises the limit to
        ₦200,000, so "address verified" has to mean a document was seen — not
        that the user typed seven characters."""
        self.post("/api/kyc/bvn/", {"access_token": self.token, "bvn": "12345678901"})
        self.post("/api/kyc/nin/", {"access_token": self.token, "nin": "10987654321"})
        self.post("/api/kyc/face/", {"access_token": self.token})
        res, body = self.post("/api/kyc/address/", {
            "access_token": self.token, "address": "12 Allen Avenue",
            "city": "Ikeja", "state": "Lagos"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("proof of address", body["message"].lower())
        self.user.refresh_from_db()
        self.assertFalse(self.user.address_verified)
        self.assertLess(self.user.tier, 2)

    def test_address_proof_too_large_is_refused_by_size_not_absence(self):
        """A document IS present, so the message must name the real problem —
        the size cap, not a missing upload. (The cap is patched down so the test
        exercises our check rather than Django's request-body limit.)"""
        with patch.object(views, "MAX_KYC_IMAGE_BASE64", 8):
            res, body = self.post("/api/kyc/address/", {
                "access_token": self.token, "address": "12 Allen Avenue",
                "document": "A" * 64})
        self.assertEqual(res.status_code, 400)
        self.assertIn("too large", body["message"].lower())

    def test_address_proof_is_not_retained(self):
        """Same promise as the NIN slip and government ID: the flag survives,
        the image does not."""
        self.post("/api/kyc/address/", {"access_token": self.token,
                                        "address": "12 Allen Avenue",
                                        "document": "ZmFrZXByb29m"})
        self.user.refresh_from_db()
        self.assertTrue(self.user.address_verified)
        blob = " ".join(str(v) for v in vars(self.user).values())
        self.assertNotIn("ZmFrZXByb29m", blob)

    def test_bvn_nin_stored_hashed_not_raw(self):
        # Defence in depth: the raw government IDs must not be recoverable at rest —
        # only a keyed hash (for audit) + last 4 (for support) are kept.
        from accounts.models import User, hash_identifier
        self.post("/api/kyc/bvn/", {"access_token": self.token, "bvn": "12345678901"})
        self.post("/api/kyc/nin/", {"access_token": self.token, "nin": "10987654321"})
        u = User.objects.get(pk=self.user.pk)
        self.assertEqual(u.bvn_last4, "8901")
        self.assertEqual(u.nin_last4, "4321")
        self.assertEqual(u.bvn_hash, hash_identifier("12345678901"))
        self.assertNotIn("12345678901", u.bvn_hash)   # the plaintext isn't in the hash
        self.assertFalse(hasattr(u, "bvn"))            # the raw column no longer exists

    def test_direct_kyc_rejects_identity_already_owned_by_another_user(self):
        other, _ = make_user("08020000002", "other@zitch.test")
        other.set_bvn("12345678901")
        other.set_nin("10987654321")
        other.bvn_verified = other.nin_verified = True
        other.save(update_fields=["bvn_hash", "bvn_last4", "nin_hash", "nin_last4",
                                  "bvn_verified", "nin_verified"])

        bvn_response, _ = self.post(
            "/api/kyc/bvn/", {"access_token": self.token, "bvn": "12345678901"})
        nin_response, _ = self.post(
            "/api/kyc/nin/", {"access_token": self.token, "nin": "10987654321"})
        self.assertEqual(bvn_response.status_code, 409)
        self.assertEqual(nin_response.status_code, 409)
        current = User.objects.get(pk=self.user.pk)
        self.assertFalse(current.bvn_verified)
        self.assertFalse(current.nin_verified)

    def test_bvn_otp_start_rejects_identity_owned_by_another_user(self):
        other, _ = make_user("08020000003", "otp-other@zitch.test")
        other.set_bvn("12345678901")
        other.bvn_verified = True
        other.save(update_fields=["bvn_hash", "bvn_last4", "bvn_verified"])
        response, _ = self.post(
            "/api/kyc/bvn/start/", {"access_token": self.token, "bvn": "12345678901"})
        self.assertEqual(response.status_code, 409)

    def test_bvn_otp_flow(self):
        # Redesigned flow: enter BVN -> code sent -> confirm code -> verified.
        from django.core.cache import cache
        r1, _ = self.post("/api/kyc/bvn/start/", {"access_token": self.token, "bvn": "12345678901"})
        self.assertEqual(r1.status_code, 200)
        code = cache.get(f"kyc_bvn:{self.user.id}")["code"]
        r2, body = self.post("/api/kyc/bvn/confirm/", {"access_token": self.token, "otp": code})
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(body["tier"], 0)  # BVN alone (NIN still pending) -> Tier 0
        self.assertTrue(User.objects.get(pk=self.user.pk).bvn_verified)

    def test_bvn_otp_rejects_wrong_code(self):
        self.post("/api/kyc/bvn/start/", {"access_token": self.token, "bvn": "12345678901"})
        r, _ = self.post("/api/kyc/bvn/confirm/", {"access_token": self.token, "otp": "000000"})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(User.objects.get(pk=self.user.pk).bvn_verified)

    def test_bvn_otp_burns_after_repeated_wrong_codes(self):
        # The 6-digit code is brute-forceable inside its 10-min window without a
        # cap: after 5 wrong guesses the pending code is burned (429) and the
        # right code no longer works until the user restarts.
        from django.core.cache import cache
        self.post("/api/kyc/bvn/start/", {"access_token": self.token, "bvn": "12345678901"})
        real = cache.get(f"kyc_bvn:{self.user.id}")["code"]
        for _ in range(4):
            r, _b = self.post("/api/kyc/bvn/confirm/", {"access_token": self.token, "otp": "000000"})
            self.assertEqual(r.status_code, 400)
        r, _b = self.post("/api/kyc/bvn/confirm/", {"access_token": self.token, "otp": "000000"})
        self.assertEqual(r.status_code, 429)  # 5th wrong guess burns the code
        # Even the correct code is now rejected — the pending entry is gone.
        r, _b = self.post("/api/kyc/bvn/confirm/", {"access_token": self.token, "otp": real})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(User.objects.get(pk=self.user.pk).bvn_verified)

    def test_bvn_rejects_bad_format(self):
        res, _ = self.post("/api/kyc/bvn/start/", {"access_token": self.token, "bvn": "123"})
        self.assertEqual(res.status_code, 400)

    def test_face_verification_sets_durable_flag(self):
        res, body = self.post("/api/kyc/face/", {"access_token": self.token})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["face_verified"])


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


class FullJourneyE2ETests(TestCase):
    """One chained journey through the whole stack — onboarding -> sign in ->
    fund -> KYC -> spend -> history -> transfer -> tier + face gate ->
    loan -> savings -> card -> betting/exams -> auth-gated lookups. Guards the
    cross-app integration that per-app unit tests don't. (Rate limiting is off
    under tests, so creating users via the API isn't throttled.)"""

    PHONE, RECIP = "08099000001", "08099000002"

    def setUp(self):
        self.client = Client()
        BettingPlatform.objects.create(code="bet9ja", name="Bet9ja", service_id="bet9ja")
        ExamProduct.objects.create(code="waec", name="WAEC", description="Result PIN", price=Decimal("3500"))

    def post(self, path, **body):
        r = self.client.post(path, data=json.dumps(body), content_type="application/json")
        return r.status_code, r.json()

    def test_full_user_journey(self):
        P, R = self.PHONE, self.RECIP

        # --- onboarding -> sign in (the auth refactor, end to end) ---
        with patch("accounts.views._otp_code", return_value="135790"):
            self.assertEqual(self.post("/api/phone_verification/", phone=P, email="e2e@zitch.test")[0], 200)
        otp = "135790"
        s, b = self.post("/api/verify_otp/", phone=P, otp=otp)
        self.assertEqual(s, 200)
        tok = b["access_token"]
        self.assertEqual(self.post("/api/set-password/", access_token=tok, password="Passw0rd123")[0], 200)
        self.assertEqual(self.post("/api/set-password/", email=P, password="hacked12345")[0], 401)  # no token
        self.assertEqual(self.post("/api/set-transaction-pin/", access_token=tok, pin="1234")[0], 200)
        s, b = self.post("/api/sigin/", email_or_phone=P, password="Passw0rd123")
        self.assertEqual(s, 200)
        tok = b["access_token"]

        # --- fund (Wema: a bank transfer into the user's NUBAN, credited by the
        # reconcile_wema poller — simulated here with a settled credit) ---
        from wallet.services import credit as _credit
        user_obj = User.objects.get(phone=P)
        _credit(user_obj, Decimal("50000"), "Wallet top-up")
        s, b = self.post("/api/wallet_balance/", access_token=tok)
        self.assertEqual(b["wallet"], "50000.00")
        self.assertIn("user_first_name", b)  # the app reads this

        # --- verify before spending ---
        # Email, phone, BVN and NIN are now a floor beneath the tier ceilings:
        # money cannot leave an account that has not proved who owns it, so this
        # step comes before the first spend rather than after it.
        self.post("/api/kyc/bvn/", access_token=tok, bvn="12345678901")
        # BVN+NIN alone no longer promote: Tier 1 also requires the verified email.
        self.assertEqual(self.post("/api/kyc/nin/", access_token=tok, nin="10987654321")[1]["tier"], 0)
        with patch("accounts.views._otp_code", return_value="909090"), \
             patch("accounts.views._otp_on_cooldown", return_value=False):
            self.post("/api/email/verify/start/", access_token=tok)
        body = self.post("/api/email/verify/confirm/", access_token=tok, otp="909090")[1]
        self.assertEqual(body["tier"], 1)  # email was the last piece; confirm recomputes

        # --- spend + history shape the app depends on ---
        self.assertEqual(self.post("/api/utility/buyairtime/", access_token=tok, amount="1000",
                                   network="1", phone=P, transaction_pin="1234")[0], 200)
        self.assertEqual(self.post("/api/wallet_balance/", access_token=tok)[1]["wallet"], "49000.00")
        txns = self.post("/api/user-transaction-history/", access_token=tok)[1]["all_site_transactions"]
        self.assertTrue({"service", "amount", "transaction_status", "date"} <= set(txns[0]))

        # --- transfer ---
        recip = User.objects.create(username=R, phone=R, email="r@zitch.test", first_name="Reci", last_name="Pient")
        get_or_create_wallet(recip)
        self.assertEqual(self.post("/api/transfer/resolve/", access_token=tok, identifier=R)[0], 200)
        self.assertEqual(self.post("/api/transfer/send/", access_token=tok, identifier=R,
                                   amount="5000", transaction_pin="1234")[0], 200)
        self.assertEqual(get_or_create_wallet(recip).balance, Decimal("5000"))

        # --- tier limits ---
        _credit(user_obj, Decimal("200000"), "Wallet top-up")
        # Tier 1 caps at ₦50k/txn, so a ₦150k transfer is blocked...
        self.assertEqual(self.post("/api/transfer/send/", access_token=tok, identifier=R,
                                   amount="150000", transaction_pin="1234")[0], 403)
        # ...face + address raise the user to Tier 2 (₦200k), which also satisfies
        # the >=₦100k face step-up, so the same transfer now goes through.
        self.post("/api/kyc/face/", access_token=tok, selfie="MOCK")
        self.assertEqual(self.post("/api/kyc/address/", access_token=tok,
                                   address="12 Allen Avenue", city="Ikeja", state="Lagos",
                                   document="ZmFrZQ==")[1]["tier"], 2)
        self.assertEqual(self.post("/api/transfer/send/", access_token=tok, identifier=R,
                                   amount="150000", transaction_pin="1234")[0], 200)

        # --- loan, savings, card, betting, exam ---
        self.assertEqual(self.post("/api/loans/request/", access_token=tok, amount="100000",
                                   tenure_days=30, transaction_pin="1234")[0], 200)
        self.assertEqual(self.post("/api/loans/repay/", access_token=tok, amount="200000",
                                   transaction_pin="1234")[1]["loan"]["status"], "repaid")
        self.assertEqual(self.post("/api/savings/create/", access_token=tok, amount="10000",
                                   days=90, transaction_pin="1234")[0], 200)
        self.assertGreaterEqual(len(self.post("/api/savings/list/", access_token=tok)[1]["plans"]), 1)
        self.assertEqual(self.post("/api/cards/create/", access_token=tok)[0], 200)
        self.assertEqual(self.post("/api/cards/fund/", access_token=tok, amount="5000",
                                   transaction_pin="1234")[1]["card"]["balance"], "5000.00")
        self.assertEqual(self.post("/api/cards/details/", access_token=tok, transaction_pin="1234")[0], 200)
        self.assertEqual(self.post("/api/betting/fund/", access_token=tok, platform="bet9ja",
                                   user_id="ZB99999", amount="1000", transaction_pin="1234")[0], 200)
        self.assertEqual(self.post("/api/exams/buy/", access_token=tok, exam="waec",
                                   quantity=1, phone=P, transaction_pin="1234")[0], 200)

        # --- name lookups require auth ---
        self.assertEqual(self.post("/api/utility/validate_meter/", disco="1", meter="1234567890")[0], 401)
        self.assertEqual(self.post("/api/utility/validate_meter/", access_token=tok,
                                   disco="1", meter="1234567890")[0], 200)


class TransactionPinLockoutTests(TestCase):
    """A stolen session token must not be usable to brute-force the short
    transaction PIN that gates money movement. The lock is per-user, so it can't
    be sidestepped by switching to a different money endpoint."""

    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08010000001", "ada@zitch.test", pin="1234", balance="20000")
        make_user("08020000002", "bob@zitch.test")  # a transfer recipient

    def post(self, path, payload):
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    def transfer(self, pin):
        return self.post("/api/transfer/send/", {
            "access_token": self.token, "identifier": "08020000002",
            "amount": "1000", "transaction_pin": pin,
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
        self.assertEqual(self.post("/api/set-password/", new_token, password="Passw0rd123")[0].status_code, 200)
        # The other session is revoked...
        self.assertEqual(self.post("/api/wallet_balance/", old_token)[0].status_code, 401)
        # ...but the one that changed the password stays signed in.
        self.assertEqual(self.post("/api/wallet_balance/", new_token)[0].status_code, 200)


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
                              otp=self._reset_code("08010000001"), password="NewPassw0rd1")
        self.assertEqual(res.status_code, 200)
        self.assertIn("access_token", body)
        self.assertTrue(User.objects.get(pk=user.pk).check_password("NewPassw0rd1"))
        # Old session is revoked; the freshly issued one works.
        self.assertEqual(self._auth(old_token), 401)
        self.assertEqual(self._auth(body["access_token"]), 200)

    def test_reset_rejects_a_wrong_code(self):
        make_user("08010000001", "a@zitch.test")
        self.post("/api/password/forgot/", phone="08010000001")
        res, _ = self.post("/api/password/reset/", phone="08010000001", otp="000000", password="NewPassw0rd1")
        self.assertEqual(res.status_code, 400)

    def test_forgot_and_reset_work_with_an_email_identifier(self):
        user, _ = make_user("08010000001", "a@zitch.test")
        # Request + reset using the email (not the phone) as the identifier.
        self.post("/api/password/forgot/", email_or_phone="a@zitch.test")
        self.assertTrue(OTP.objects.filter(phone="08010000001", purpose=OTP.RESET).exists())
        res, body = self.post("/api/password/reset/", email_or_phone="a@zitch.test",
                              otp=self._reset_code("08010000001"), password="NewPassw0rd1")
        self.assertEqual(res.status_code, 200)
        self.assertIn("access_token", body)
        self.assertTrue(User.objects.get(pk=user.pk).check_password("NewPassw0rd1"))

    def test_signup_verifier_will_not_honour_a_reset_code(self):
        make_user("08010000001", "a@zitch.test")
        self.post("/api/password/forgot/", phone="08010000001")
        res, _ = self.post("/api/verify_otp/", phone="08010000001", otp=self._reset_code("08010000001"))
        self.assertEqual(res.status_code, 400)  # reset code is not a signup/login code

    def _auth(self, token):
        return self.client.post("/api/wallet_balance/", data=json.dumps({"access_token": token}),
                                content_type="application/json").status_code


class ChatOnboardedUpgradeTests(TestCase):
    """The app-side upgrade contract for WhatsApp-onboarded accounts: both contact
    channels must be re-proven before the KYC ladder opens. The phone re-proves
    itself on the way in (no usable password, so entering the app runs the OTP
    password reset against the same number); the email was typed into a chat and
    is one typo from being someone else's inbox, so it gets its own round-trip —
    and until then it must never receive a password-reset code."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create(
            username="08155550001", phone="08155550001", first_name="Chidi",
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

    def test_kyc_is_closed_until_the_email_is_confirmed(self):
        res, body = self.post("/api/kyc/bvn/start/", {"bvn": "12345678901"})
        self.assertEqual(res.status_code, 403)
        self.assertIn("email", body["message"].lower())
        self.user.refresh_from_db()
        self.assertFalse(self.user.bvn_verified)

    def test_kyc_status_names_the_gate(self):
        res, body = self.post("/api/kyc/status/", {})
        self.assertTrue(body["email_verification_required"])
        self.assertEqual(body["email"], "chidi@zitch.test")

    def test_email_round_trip_opens_the_ladder(self):
        with patch("accounts.views._otp_code", return_value="424242"):
            self.post("/api/email/verify/start/", {})
        res, body = self.post("/api/email/verify/confirm/", {"otp": "424242"})
        self.assertEqual(res.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)
        self.assertFalse(body["email_verification_required"])
        res, _ = self.post("/api/kyc/bvn/start/", {"bvn": "12345678901"})
        self.assertEqual(res.status_code, 200)   # the gate is open

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

    def test_app_signup_accounts_are_untouched_by_the_gate(self):
        """The gate is scoped to chat onboarding — an app-signup account keeps
        today's behaviour on both KYC and recovery."""
        plain = User.objects.create(username="08155550002", phone="08155550002",
                                    email="plain@zitch.test")
        tok = AccessToken.issue(plain).key
        res = self.client.post("/api/kyc/bvn/start/",
                               data=json.dumps({"access_token": tok, "bvn": "12345678901"}),
                               content_type="application/json")
        self.assertEqual(res.status_code, 200)
        with patch("accounts.views.send_email") as email, \
             patch("accounts.views._otp_code", return_value="424242"):
            self.client.post("/api/password/forgot/",
                             data=json.dumps({"email_or_phone": "08155550002"}),
                             content_type="application/json")
        self.assertEqual(email.call_args[0][0], "plain@zitch.test")
