"""Tests for auth onboarding, OTP hardening, and credential-setting security."""
import json
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from betting.models import BettingPlatform
from exams.models import ExamProduct
from wallet.services import get_or_create_wallet
from wallet.tests import make_user

from .models import OTP, AccessToken

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

    def test_set_password_rejects_weak(self):
        # Server-side strength rules: an all-numeric or top-common password must
        # be refused even via a direct API call (the client hints are bypassable).
        _, token = make_user("08030000009", "weak@zitch.test")
        for pw in ("12345678", "password"):
            res, _ = self.post("/api/set-password/", {"access_token": token, "password": pw})
            self.assertEqual(res.status_code, 400)

    def test_set_pin_requires_auth_and_sets_owner(self):
        res, _ = self.post("/api/set-transaction-pin/", {"email": "x@zitch.test", "pin": "1357"})
        self.assertEqual(res.status_code, 401)
        # First-time PIN set (no existing PIN) needs only the session token.
        user = User.objects.create(username="08040000004", phone="08040000004", email="d@zitch.test")
        token = AccessToken.issue(user).key
        res, _ = self.post("/api/set-transaction-pin/", {"access_token": token, "pin": "1357"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(User.objects.get(pk=user.pk).check_transaction_pin("1357"))

    def test_changing_existing_pin_requires_account_password(self):
        # A token alone must not be enough to OVERWRITE an existing PIN (else the
        # brute-force lockout is moot — an attacker would just reset the PIN).
        user = User.objects.create(username="08050000005", phone="08050000005", email="e@zitch.test")
        user.set_password("Passw0rd123")
        user.set_transaction_pin("1234")
        user.save()
        token = AccessToken.issue(user).key
        res, body = self.post("/api/set-transaction-pin/", {"access_token": token, "pin": "9999"})
        self.assertEqual((res.status_code, body.get("code")), (403, "password_required"))
        self.assertTrue(User.objects.get(pk=user.pk).check_transaction_pin("1234"))  # unchanged
        # With the account password, the change goes through.
        res, _ = self.post("/api/set-transaction-pin/", {
            "access_token": token, "pin": "9999", "password": "Passw0rd123"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(User.objects.get(pk=user.pk).check_transaction_pin("9999"))

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
            "access_token": token, "pin": "5678", "password": "Passw0rd123"})
        self.assertEqual(res.status_code, 200)
        u = User.objects.get(pk=user.pk)
        self.assertEqual(u.pin_failed_attempts, 0)
        self.assertIsNone(u.pin_locked_until)
        self.assertTrue(u.check_transaction_pin("5678"))

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

    def test_bvn_otp_flow(self):
        # Redesigned flow: enter BVN -> code sent -> confirm code -> verified.
        from django.core.cache import cache
        r1, _ = self.post("/api/kyc/bvn/start/", {"access_token": self.token, "bvn": "12345678901"})
        self.assertEqual(r1.status_code, 200)
        code = cache.get(f"kyc_bvn:{self.user.id}")["code"]
        r2, body = self.post("/api/kyc/bvn/confirm/", {"access_token": self.token, "otp": code})
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(body["tier"], 2)  # BVN verified -> tier 2
        self.assertTrue(User.objects.get(pk=self.user.pk).bvn_verified)

    def test_bvn_otp_rejects_wrong_code(self):
        self.post("/api/kyc/bvn/start/", {"access_token": self.token, "bvn": "12345678901"})
        r, _ = self.post("/api/kyc/bvn/confirm/", {"access_token": self.token, "otp": "000000"})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(User.objects.get(pk=self.user.pk).bvn_verified)

    def test_bvn_rejects_bad_format(self):
        res, _ = self.post("/api/kyc/bvn/start/", {"access_token": self.token, "bvn": "123"})
        self.assertEqual(res.status_code, 400)

    def test_face_verification_sets_durable_flag(self):
        res, body = self.post("/api/kyc/face/", {"access_token": self.token})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["face_verified"])


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
    fund -> spend -> history -> transfer -> KYC + large-transfer face gate ->
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
        self.assertEqual(self.post("/api/phone_verification/", phone=P, email="e2e@zitch.test")[0], 200)
        otp = OTP.objects.filter(phone=P).latest("created").code
        self.assertEqual(len(otp), 6)
        s, b = self.post("/api/verify_otp/", phone=P, otp=otp)
        self.assertEqual(s, 200)
        tok = b["access_token"]
        self.assertEqual(self.post("/api/set-password/", access_token=tok, password="Passw0rd123")[0], 200)
        self.assertEqual(self.post("/api/set-password/", email=P, password="hacked12345")[0], 401)  # no token
        self.assertEqual(self.post("/api/set-transaction-pin/", access_token=tok, pin="1234")[0], 200)
        s, b = self.post("/api/sigin/", email_or_phone=P, password="Passw0rd123")
        self.assertEqual(s, 200)
        tok = b["access_token"]

        # --- fund (credited exactly once across a duplicate verify) ---
        ref = self.post("/api/fund/initialize/", access_token=tok, amount="50000")[1]["reference"]
        self.post("/api/fund/verify/", access_token=tok, reference=ref)
        self.post("/api/fund/verify/", access_token=tok, reference=ref)
        s, b = self.post("/api/wallet_balance/", access_token=tok)
        self.assertEqual(b["wallet"], "50000.00")
        self.assertIn("user_first_name", b)  # the app reads this

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

        # --- KYC tiers + large-transfer face gate ---
        self.post("/api/kyc/bvn/", access_token=tok, bvn="12345678901")
        self.assertEqual(self.post("/api/kyc/nin/", access_token=tok, nin="10987654321")[1]["tier"], 3)
        ref2 = self.post("/api/fund/initialize/", access_token=tok, amount="200000")[1]["reference"]
        self.post("/api/fund/verify/", access_token=tok, reference=ref2)
        s, b = self.post("/api/transfer/send/", access_token=tok, identifier=R, amount="150000", transaction_pin="1234")
        self.assertEqual((s, b.get("code")), (403, "face_required"))
        self.post("/api/kyc/face/", access_token=tok, selfie="MOCK")
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

    def setUp(self):
        self.client = Client()

    def post(self, path, **payload):
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    def _reset_code(self, phone):
        return OTP.objects.filter(phone=phone, purpose=OTP.RESET).latest("created").code

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
