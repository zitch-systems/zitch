"""The Django admin and diagnostics must not bypass an enrolled operator factor."""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

from django.contrib import admin
from django.core.cache import cache
from django.db import close_old_connections, connections
from django.test import Client, TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone

from accounts.admin_site import SESSION_PROOF_KEY, ZitchAdminSite
from accounts.models import OperatorTotp, User
from accounts.totp import code_for, current_step, new_secret, verify
from admin_api.views import _mfa_login_error
from whatsapp.models import AuditLog


@override_settings(OPS_REQUIRE_MFA=False)
class DjangoAdminMfaTests(TestCase):
    password = "Admin-MFA-Secret-54!"

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_superuser(
            "mfa-admin", "mfa-admin@example.test", self.password, phone=None)

    def enrol(self, user=None):
        secret = new_secret()
        row = OperatorTotp.objects.create(user=user or self.user, secret=secret,
            confirmed=True, confirmed_at=timezone.now())
        return row, secret

    def login(self, code="", *, client=None, user=None, password=None):
        return (client or self.client).post("/admin/login/", {
            "username": (user or self.user).username, "password": password or self.password,
            "code": code, "next": "/admin/",
        })

    def assert_no_session(self, client=None):
        self.assertNotIn("_auth_user_id", (client or self.client).session)

    def test_site_and_login_form_are_wired(self):
        self.assertIsInstance(admin.site._wrapped, ZitchAdminSite)
        response = self.client.get("/admin/login/")
        self.assertContains(response, 'name="code"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')

    def test_enrolled_operator_cannot_use_password_only(self):
        self.enrol()
        response = self.login()
        self.assertContains(response, "Enter the code from your authenticator app.")
        self.assert_no_session()
        self.assertTrue(AuditLog.objects.filter(action="django_admin.login_mfa_failed").exists())

    def test_wrong_code_is_denied_without_echoing_it(self):
        _row, secret = self.enrol()
        wrong = str((int(code_for(secret, current_step())) + 1) % 1000000).zfill(6)
        response = self.login(wrong)
        self.assertContains(response, "not valid or has already been used")
        self.assertNotContains(response, f'value="{wrong}"')
        self.assert_no_session()

    def test_correct_code_authenticates_and_cannot_be_replayed_in_another_browser(self):
        _row, secret = self.enrol()
        code = code_for(secret, current_step())
        self.assertEqual(self.login(code).status_code, 302)
        self.assertIn(SESSION_PROOF_KEY, self.client.session)
        self.assertEqual(self.client.get("/admin/").status_code, 200)
        other = Client()
        self.assertContains(self.login(code, client=other), "not valid or has already been used")
        self.assert_no_session(other)
        self.assertEqual(self.client.get("/admin/").status_code, 200)

    def test_existing_password_only_session_cannot_open_admin_or_diagnostics(self):
        self.client.force_login(self.user)
        self.enrol()
        for route in ("/admin/", "/admin/diagnostics/", "/admin/accounts/user/"):
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 302)
                self.assertIn("/admin/login/", response.url)

    def test_verified_session_can_open_diagnostics(self):
        _row, secret = self.enrol()
        self.assertEqual(self.login(code_for(secret, current_step())).status_code, 302)
        with patch("django.core.management.call_command"):
            self.assertEqual(self.client.get("/admin/diagnostics/").status_code, 200)

    def test_factor_replacement_revokes_existing_admin_session(self):
        row, secret = self.enrol()
        self.login(code_for(secret, current_step()))
        row.refresh_from_db()
        row.secret = new_secret()
        row.confirmed_at = timezone.now()
        row.save(update_fields=["secret", "confirmed_at"])
        self.assertEqual(self.client.get("/admin/").status_code, 302)
        self.assertEqual(self.client.get("/admin/diagnostics/").status_code, 302)

    def test_factor_removal_does_not_fall_back_to_password_only_session(self):
        row, secret = self.enrol()
        self.login(code_for(secret, current_step()))
        row.delete()
        self.assertEqual(self.client.get("/admin/").status_code, 302)
        self.assertEqual(self.login().status_code, 302)
        self.assertEqual(self.client.get("/admin/").status_code, 200)

    def test_factor_reset_to_unconfirmed_revokes_existing_session(self):
        row, secret = self.enrol()
        self.login(code_for(secret, current_step()))
        OperatorTotp.objects.filter(pk=row.pk).update(confirmed=False)
        self.assertEqual(self.client.get("/admin/").status_code, 302)

    def test_corrupt_factor_fails_closed_for_login_and_existing_session(self):
        row, secret = self.enrol()
        self.login(code_for(secret, current_step()))
        OperatorTotp.objects.filter(pk=row.pk).update(secret="fernet:v1:corrupted")
        self.assertEqual(self.client.get("/admin/").status_code, 302)
        other = Client()
        self.assertEqual(self.login(code_for(secret, current_step()), client=other).status_code, 200)
        self.assert_no_session(other)

    def test_portal_code_consumption_does_not_revoke_verified_admin_session(self):
        row, secret = self.enrol()
        now_step = current_step()
        with patch("accounts.totp.current_step", return_value=now_step):
            self.login(code_for(secret, now_step))
        before = OperatorTotp.objects.get(pk=row.pk).secret
        with patch("accounts.totp.current_step", return_value=now_step + 1):
            self.assertIsNone(_mfa_login_error(self.user, code_for(secret, now_step + 1)))
        self.assertNotEqual(OperatorTotp.objects.get(pk=row.pk).secret, before)
        self.assertEqual(self.client.get("/admin/").status_code, 200)

    def test_switching_operator_keeps_new_accounts_proof_after_session_flush(self):
        self.login()
        other = User.objects.create_superuser("second-admin", "second@example.test", self.password, phone=None)
        _row, secret = self.enrol(other)
        self.assertEqual(self.login(code_for(secret, current_step()), user=other).status_code, 302)
        self.assertEqual(self.client.session["_auth_user_id"], str(other.pk))
        self.assertEqual(self.client.get("/admin/").status_code, 200)

    @override_settings(OPS_REQUIRE_MFA=True)
    def test_required_but_unenrolled_money_operator_is_denied(self):
        self.assertContains(self.login(), "Two-factor authentication is required for your role")
        self.assert_no_session()
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/admin/").status_code, 302)

    def test_unenrolled_operator_is_supported_during_staged_rollout(self):
        self.assertEqual(self.login().status_code, 302)
        self.assertEqual(self.client.get("/admin/").status_code, 200)

    @override_settings(RATELIMIT_ENABLE=True, ADMIN_LOGIN_MAX_FAILS=2)
    def test_failed_admin_logins_obey_account_lockout(self):
        self.login(password="wrong")
        self.login(password="wrong")
        response = self.login()
        self.assertContains(response, "Too many failed attempts")
        self.assert_no_session()

    @override_settings(RATELIMIT_ENABLE=True, ADMIN_LOGIN_MAX_FAILS=100)
    def test_admin_login_has_an_ip_rate_limit(self):
        for _ in range(10):
            self.login(password="wrong")
        self.assertEqual(self.login().status_code, 429)

    def test_csrf_protection_is_retained(self):
        strict = Client(enforce_csrf_checks=True)
        self.assertEqual(self.login(client=strict).status_code, 403)

    def test_session_proof_and_audit_never_contain_factor_material(self):
        row, secret = self.enrol()
        code = code_for(secret, current_step())
        self.login(code)
        proof = self.client.session[SESSION_PROOF_KEY]
        self.assertEqual(len(proof), 64)
        serialized = json.dumps(dict(self.client.session))
        audit = json.dumps(list(AuditLog.objects.values("action", "target", "before", "after")), default=str)
        for sensitive in (secret, row.secret, self.password):
            self.assertNotIn(sensitive, serialized + audit)


@skipUnlessDBFeature("has_select_for_update")
class OperatorCodeConcurrencyTests(TransactionTestCase):
    def test_same_current_code_authorizes_only_one_concurrent_login(self):
        user = User.objects.create_superuser("concurrent-operator", "race@example.test", "Strong-Password-54!", phone=None)
        secret = new_secret()
        OperatorTotp.objects.create(user=user, secret=secret, confirmed=True, confirmed_at=timezone.now())
        code = code_for(secret, current_step())
        entered, release, second_lock = Event(), Event(), Event()
        original_lock = OperatorTotp.objects.select_for_update

        def pause_first_verify(*args, **kwargs):
            if not entered.is_set():
                entered.set()
                if not release.wait(5):
                    raise AssertionError("First login was not released")
            return verify(*args, **kwargs)

        def locking(*args, **kwargs):
            if entered.is_set():
                second_lock.set()
            return original_lock(*args, **kwargs)

        def login():
            close_old_connections()
            try:
                error = _mfa_login_error(user, code)
                return None if error is None else json.loads(error.content)["code"]
            finally:
                connections.close_all()

        with patch("accounts.totp.verify", side_effect=pause_first_verify), \
                patch.object(OperatorTotp.objects, "select_for_update", side_effect=locking), \
                ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(login)
            self.assertTrue(entered.wait(5))
            second = pool.submit(login)
            try:
                self.assertTrue(second_lock.wait(5))
            finally:
                release.set()
            self.assertEqual([first.result(timeout=10), second.result(timeout=10)], [None, "mfa_invalid"])
