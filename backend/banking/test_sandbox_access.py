"""Sandbox enrollment never substitutes for verified contacts or production UAT."""
import json
from io import StringIO
from types import SimpleNamespace
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase, override_settings
from .accounts import sandbox_tester
from .client import customer_enabled
from .readiness import tester_report
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


class TesterStatusTests(TestCase):
    """The operator report names the first failing onboarding gate, redacted."""
    SANDBOX = {**CONFIG, "ENVIRONMENT": "sandbox", "SANDBOX_CUSTOMERS_ENABLED": True,
               "SANDBOX_PHONES": ["+2348010000001"], "SANDBOX_USER_IDS": []}

    def setUp(self):
        from accounts.models import User
        self.user = User.objects.create_user(username="tester", first_name="Ada", last_name="Eze",
            phone="08010000001", email="tester@example.test", phone_verified=True, email_verified=True)

    def report(self, **conf):
        with override_settings(NINEPSB={**self.SANDBOX, **conf}):
            return tester_report(self.user)

    def test_an_enrolled_verified_tester_is_ready_for_bank_identity(self):
        result = self.report()
        self.assertTrue(result["ready_for_bank_identity"])
        self.assertEqual(result["blockers"], [])
        self.assertEqual(result["sandbox_enrolled_by"], "phone")
        self.assertIn("BVN or NIN", result["next_step"])
        self.assertIsNone(result["bank_account"])

    def test_switches_are_reported_before_customer_gates(self):
        result = self.report(ENABLED=False, SANDBOX_CUSTOMERS_ENABLED=False, SANDBOX_PHONES=[])
        self.assertEqual(result["blockers"][:3], ["banking_disabled", "sandbox_customers_disabled", "not_enrolled"])
        self.assertIn("NINEPSB_ENABLED", result["next_step"])
        self.assertIn("waas_not_configured", self.report(BASE_URL="")["blockers"])

    def test_listed_phone_that_is_unverified_is_named_once(self):
        self.user.phone_verified = False
        result = self.report()
        self.assertIn("enrolled_phone_not_verified", result["blockers"])
        self.assertNotIn("phone_not_verified", result["blockers"])
        self.assertTrue(self.report(SANDBOX_USER_IDS=[str(self.user.pk)])["blockers"] == ["phone_not_verified"])

    def test_email_and_legal_name_gates_match_identity_start(self):
        self.user.email_verified, self.user.last_name = False, ""
        self.assertEqual(self.report()["blockers"], ["email_not_verified", "legal_name_incomplete"])

    def test_attached_active_account_reports_only_the_last_four_digits(self):
        from wallet.models import Wallet
        from .models import BankAccount
        wallet = Wallet.objects.create(user=self.user, account_number="1100000077", bank_name="9PSB")
        BankAccount.objects.create(wallet=wallet, number="1100000077", name="Ada Eze", environment="sandbox",
            status="active", tier=1, opening_review_required=False)
        result = self.report()
        self.assertEqual(result["bank_account"], {"status": "active", "tier": 1, "nuban_last4": "0077", "nuban_ready": True})
        self.assertNotIn("1100000077", json.dumps(result))
        self.assertIn("bank_account_in_other_environment", self.report(ENVIRONMENT="production")["blockers"])

    def test_command_finds_international_notation_and_redacts_contacts(self):
        out = StringIO()
        with override_settings(NINEPSB=self.SANDBOX):
            call_command("bank_tester_status", "--phone", "+234 801 000 0001", stdout=out)
        result = json.loads(out.getvalue())
        self.assertEqual((result["user_id"], result["phone_last4"]), (self.user.pk, "0001"))
        self.assertNotIn("tester@example.test", out.getvalue())
        self.assertNotIn("08010000001", out.getvalue())
        with self.assertRaises(CommandError):
            call_command("bank_tester_status", "--phone", "08019999999", stdout=StringIO())
