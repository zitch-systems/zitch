"""Sandbox enrollment never substitutes for verified contacts or production UAT."""
from types import SimpleNamespace
from django.test import SimpleTestCase, override_settings
from .accounts import sandbox_tester
from .client import customer_enabled
from .test_client import CONFIG


class SandboxEnrollmentTests(SimpleTestCase):
    def user(self, **changes):
        return SimpleNamespace(pk=42, phone="08010000001", phone_verified=True, **changes)

    def test_enrollment_defaults_to_denied(self):
        with override_settings(NINEPSB={**CONFIG, "SANDBOX_USER_IDS": [], "SANDBOX_PHONES": []}):
            self.assertFalse(sandbox_tester(self.user()))

    def test_id_enrollment_remains_exact(self):
        with override_settings(NINEPSB={**CONFIG, "SANDBOX_USER_IDS": ["42"]}):
            self.assertTrue(sandbox_tester(self.user()))

    def test_phone_enrollment_requires_the_verified_exact_number(self):
        with override_settings(NINEPSB={**CONFIG, "SANDBOX_PHONES": ["+2348010000001"]}):
            user = self.user()
            self.assertTrue(sandbox_tester(user))
            user.phone_verified = False
            self.assertFalse(sandbox_tester(user))
            user.phone_verified, user.phone = True, "08010000002"
            self.assertFalse(sandbox_tester(user))

    def test_malformed_phone_entries_cannot_grant_access(self):
        with override_settings(NINEPSB={**CONFIG, "SANDBOX_PHONES": ["*", "", "0801", None]}):
            self.assertFalse(sandbox_tester(self.user()))

    def test_enrollment_does_not_activate_production_or_disable_the_sandbox_switch(self):
        for environment in ("production", "sandbox"):
            with self.subTest(environment=environment), override_settings(NINEPSB={
                    **CONFIG, "ENVIRONMENT": environment, "SANDBOX_PHONES": ["08010000001"],
                    "UAT_APPROVED": False, "SANDBOX_CUSTOMERS_ENABLED": False}):
                self.assertFalse(customer_enabled())
