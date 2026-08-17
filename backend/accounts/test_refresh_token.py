"""A session that outlives the access token without weakening it.

TOKEN_TTL_HOURS is 24, so the app signed a customer out daily and biometric
sign-in fell back to email+password. The two candidate fixes were a longer
access-token window or this. A longer window is strictly worse: an access token
rides on EVERY API call, so extending its life multiplies the value of a single
intercepted bearer and there is no way to end it early. A refresh token is
presented to one endpoint, rotates on every use, and revokes as a family.

The properties that make it safe are the ones tested here — rotation, reuse
detection, family revocation, an absolute ceiling, device binding, and a single
indistinguishable refusal for every failure mode.
"""
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import AccessToken, RefreshToken, User

DEVICE = "install-a"


def _user(email="ada@example.com", phone="08010000001"):
    u = User.objects.create_user(username=email, email=email, password="Sup3r-Secret-1")
    u.phone = phone
    u.save(update_fields=["phone"])
    return u


class RotationTests(TestCase):

    def setUp(self):
        self.user = _user()

    def test_a_refresh_returns_a_new_token_and_burns_the_old_one(self):
        first = RefreshToken.issue(self.user, device_id=DEVICE)
        raw = first.key
        fresh, who = RefreshToken.rotate(raw, device_id=DEVICE)
        self.assertEqual(who, self.user)
        self.assertNotEqual(fresh.key, raw)
        first.refresh_from_db()
        self.assertIsNotNone(first.used_at)

    def test_the_chain_keeps_its_family_so_it_can_be_revoked_as_one(self):
        first = RefreshToken.issue(self.user, device_id=DEVICE)
        second, _ = RefreshToken.rotate(first.key, device_id=DEVICE)
        third, _ = RefreshToken.rotate(second.key, device_id=DEVICE)
        self.assertEqual({first.family, second.family, third.family}, {first.family})

    def test_a_replay_is_treated_as_a_theft_and_ends_the_whole_family(self):
        """The second presentation of a spent token proves two parties hold the
        chain. Issuing a fresh token to whoever asked would hide the breach;
        revoking both means the victim notices, which is the outcome to want."""
        first = RefreshToken.issue(self.user, device_id=DEVICE)
        second, _ = RefreshToken.rotate(first.key, device_id=DEVICE)
        fresh, reason = RefreshToken.rotate(first.key, device_id=DEVICE)
        self.assertIsNone(fresh)
        self.assertEqual(reason, RefreshToken.REUSED)
        # The thief's newer token dies too — revoking only the presented row
        # would leave the live end of the chain working.
        dead, why = RefreshToken.rotate(second.key, device_id=DEVICE)
        self.assertIsNone(dead)
        self.assertEqual(why, RefreshToken.REVOKED)

    def test_an_unknown_token_is_refused_without_creating_anything(self):
        fresh, reason = RefreshToken.rotate("deadbeef" * 8, device_id=DEVICE)
        self.assertIsNone(fresh)
        self.assertEqual(reason, RefreshToken.UNKNOWN)
        self.assertEqual(RefreshToken.objects.count(), 0)

    def test_a_blank_token_is_refused_rather_than_matching_a_row(self):
        RefreshToken.issue(self.user, device_id=DEVICE)
        self.assertEqual(RefreshToken.rotate("", device_id=DEVICE)[1], RefreshToken.UNKNOWN)

    def test_the_raw_token_is_never_stored(self):
        tok = RefreshToken.issue(self.user, device_id=DEVICE)
        self.assertFalse(RefreshToken.objects.filter(key=tok.key).exists())
        self.assertTrue(RefreshToken.objects.filter(key=RefreshToken._hash(tok.key)).exists())


class ExpiryTests(TestCase):

    def setUp(self):
        self.user = _user()

    @override_settings(REFRESH_TOKEN_TTL_DAYS=30)
    def test_an_idle_session_expires_after_the_idle_window(self):
        tok = RefreshToken.issue(self.user, device_id=DEVICE)
        raw = tok.key
        RefreshToken.objects.filter(pk=tok.pk).update(
            created=timezone.now() - timedelta(days=31))
        self.assertEqual(RefreshToken.rotate(raw, device_id=DEVICE)[1], RefreshToken.EXPIRED)

    @override_settings(REFRESH_TOKEN_TTL_DAYS=30, REFRESH_ABSOLUTE_DAYS=90)
    def test_rotation_cannot_extend_a_session_forever(self):
        """Rotation alone lets an active chain live indefinitely. The family dies
        a fixed time after the password was last proven, so a session cannot
        outlive the credential behind it."""
        tok = RefreshToken.issue(self.user, device_id=DEVICE)
        raw = tok.key
        RefreshToken.objects.filter(pk=tok.pk).update(
            family_started=timezone.now() - timedelta(days=91))
        self.assertEqual(RefreshToken.rotate(raw, device_id=DEVICE)[1], RefreshToken.EXPIRED)

    @override_settings(REFRESH_ABSOLUTE_DAYS=90)
    def test_the_ceiling_is_carried_across_rotations_not_reset_by_them(self):
        tok = RefreshToken.issue(self.user, device_id=DEVICE)
        started = timezone.now() - timedelta(days=89)
        RefreshToken.objects.filter(pk=tok.pk).update(family_started=started)
        fresh, _ = RefreshToken.rotate(tok.key, device_id=DEVICE)
        self.assertAlmostEqual(fresh.family_started, started, delta=timedelta(seconds=2))

    def test_a_deactivated_account_cannot_refresh(self):
        tok = RefreshToken.issue(self.user, device_id=DEVICE)
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.assertEqual(RefreshToken.rotate(tok.key, device_id=DEVICE)[1],
                         RefreshToken.INACTIVE)


class DeviceBindingTests(TestCase):

    def setUp(self):
        self.user = _user()

    def test_a_token_lifted_onto_another_install_is_refused(self):
        tok = RefreshToken.issue(self.user, device_id=DEVICE)
        self.assertEqual(RefreshToken.rotate(tok.key, device_id="install-b")[1],
                         RefreshToken.DEVICE)

    def test_a_missing_device_header_does_not_satisfy_a_binding(self):
        tok = RefreshToken.issue(self.user, device_id=DEVICE)
        self.assertEqual(RefreshToken.rotate(tok.key, device_id="")[1],
                         RefreshToken.DEVICE)

    def test_the_binding_is_carried_onto_the_rotated_token(self):
        tok = RefreshToken.issue(self.user, device_id=DEVICE)
        fresh, _ = RefreshToken.rotate(tok.key, device_id=DEVICE)
        self.assertEqual(fresh.device_id, DEVICE)

    def test_an_unbound_token_still_works_so_a_deploy_signs_nobody_out(self):
        tok = RefreshToken.issue(self.user, device_id="")
        fresh, who = RefreshToken.rotate(tok.key, device_id="install-anything")
        self.assertEqual(who, self.user)
        self.assertIsNotNone(fresh)


class EndpointTests(TestCase):

    def setUp(self):
        self.user = _user()
        self.headers = {"HTTP_X_ZITCH_DEVICE": DEVICE}

    def _signin(self):
        return self.client.post(
            "/api/sigin/",
            {"email_or_phone": self.user.email, "password": "Sup3r-Secret-1"},
            content_type="application/json", **self.headers).json()

    def _refresh(self, token, **extra):
        headers = {**self.headers, **extra}
        return self.client.post("/api/token/refresh/", {"refresh_token": token},
                                content_type="application/json", **headers)

    def test_signing_in_hands_back_both_halves_and_the_access_lifetime(self):
        body = self._signin()
        self.assertTrue(body["access_token"])
        self.assertTrue(body["refresh_token"])
        # So the client can renew BEFORE a request fails, rather than discovering
        # the expiry as a 401 in the middle of a payment.
        self.assertEqual(body["expires_in"], 24 * 3600)

    def test_a_refresh_returns_a_working_access_token_and_a_new_refresh_token(self):
        body = self._signin()
        renewed = self._refresh(body["refresh_token"]).json()
        self.assertNotEqual(renewed["access_token"], body["access_token"])
        self.assertNotEqual(renewed["refresh_token"], body["refresh_token"])
        self.assertEqual(AccessToken.resolve(renewed["access_token"], device_id=DEVICE),
                         self.user)

    def test_the_endpoint_works_when_the_access_token_is_already_dead(self):
        """The entire point: it must not be behind @require_user, or it would be
        unreachable exactly when it is needed."""
        body = self._signin()
        AccessToken.objects.all().delete()
        self.assertEqual(self._refresh(body["refresh_token"]).status_code, 200)

    def test_every_refusal_looks_identical_to_the_caller(self):
        """Naming the reason would tell someone holding a stolen token whether it
        was ever valid, whether the chain is live, and whether they guessed the
        device. The distinction stays in the logs."""
        body = self._signin()
        good = body["refresh_token"]
        self._refresh(good)                              # burn it
        answers = {
            "reused": self._refresh(good),
            "unknown": self._refresh("00" * 32),
            "blank": self._refresh(""),
            "wrong_device": self._refresh(self._signin()["refresh_token"],
                                          HTTP_X_ZITCH_DEVICE="install-b"),
        }
        for name, res in answers.items():
            self.assertEqual(res.status_code, 401, name)
            self.assertEqual(res.json()["message"],
                             "Your session has expired. Please sign in again.", name)

    def test_a_replay_through_the_endpoint_ends_the_family(self):
        body = self._signin()
        second = self._refresh(body["refresh_token"]).json()["refresh_token"]
        self.assertEqual(self._refresh(body["refresh_token"]).status_code, 401)
        self.assertEqual(self._refresh(second).status_code, 401)

    def test_signing_out_revokes_the_chain_not_just_the_access_token(self):
        """Otherwise "sign out" would mean "signed out for a few hours"."""
        body = self._signin()
        res = self.client.post(
            "/api/logout/", {"refresh_token": body["refresh_token"]},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {body['access_token']}", **self.headers)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self._refresh(body["refresh_token"]).status_code, 401)

    def test_an_older_client_that_sends_no_refresh_token_still_signs_out(self):
        """The app that ships today has no refresh token to send. Falling back to
        this install's chains means it still gets a real sign-out."""
        body = self._signin()
        self.client.post("/api/logout/", {}, content_type="application/json",
                         HTTP_AUTHORIZATION=f"Bearer {body['access_token']}",
                         **self.headers)
        self.assertEqual(self._refresh(body["refresh_token"]).status_code, 401)

    def test_one_customer_cannot_revoke_another_customers_chain(self):
        mine = self._signin()
        theirs_user = _user("bola@example.com", "08010000002")
        theirs = RefreshToken.issue(theirs_user, device_id=DEVICE)
        self.client.post(
            "/api/logout/", {"refresh_token": theirs.key},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {mine['access_token']}", **self.headers)
        # Still live: logout scopes the lookup to the authenticated user.
        self.assertIsNotNone(RefreshToken.rotate(theirs.key, device_id=DEVICE)[0])

    def test_a_password_reset_kills_every_refresh_chain(self):
        """A stolen refresh token must not outlive the password change that was
        meant to end it."""
        body = self._signin()
        from accounts.models import OTP

        code = "123456"
        OTP.issue(phone=self.user.phone, code=code, purpose=OTP.RESET)
        res = self.client.post(
            "/api/password/reset/",
            {"email_or_phone": self.user.phone, "otp": code, "password": "An0ther-Secret-2"},
            content_type="application/json", **self.headers)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self._refresh(body["refresh_token"]).status_code, 401)
        # ...and the reset itself hands back a usable new session.
        self.assertEqual(self._refresh(res.json()["refresh_token"]).status_code, 200)
