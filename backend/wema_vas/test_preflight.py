"""Operator readiness never writes money, leaks credentials or claims bank signoff."""
import json
import re
from io import StringIO
from unittest import skipUnless
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, connection
from django.test import TestCase, override_settings

from accounts.models import IdentityProof, User, hash_identifier, record_identity_proof
from wallet.models import Transaction, Wallet
from wema_vas.enrollment import enroll_verified
from wema_vas.management.commands.vas_preflight import build_report, database_checks
from wema_vas.models import Receipt, VirtualAccount

KEY = Fernet.generate_key().decode()
VALUES = {"ENABLED": True, "MODE": "validation", "PREFIX": "711", "TOKEN": "unique-private-bank-token-" + "q" * 48,
          "IDENTITY_KEYS": [KEY], "REQUIRE_HTTPS": True, "ENABLE_ENROLLMENT": False,
          "RELEASE_PHASE": "closed", "PILOT_USER_IDS": [],
          "LIVE_APPROVAL_REFERENCE": "private-bank-approval-evidence", "COLLECTION_ACCOUNT": "2020202020"}
DB_READY = {"postgresql": True, "schema": True, "migrations": True, "immutable_evidence": True}
MODULE = "wema_vas.management.commands.vas_preflight"


@override_settings(WEMA_VAS=VALUES, BANK_ACCOUNT_PROVIDER="wema_vas", TESTING=True)
class PreflightTests(TestCase):
    def setUp(self):
        self.users = []
        self.accounts = []
        self.identities = []
        for index in range(1, 4):
            user, raw = self.new_verified_user(index)
            self.users.append(user)
            self.identities.append(raw)
            self.accounts.append(enroll_verified(user, bvn=raw, consent=True, validation=True))
        self.numbers = [account.number for account in self.accounts]

    def new_verified_user(self, index):
        raw = f"3456789012{index}"
        user = User.objects.create(username=f"preflight-user-{index}", phone=f"+234809912300{index}",
            first_name="Private", last_name=f"Holder{index}", phone_verified=True,
            bvn_verified=True, bvn_hash=hash_identifier(raw))
        Wallet.objects.create(user=user)
        record_identity_proof(user, "bvn", raw, source=IdentityProof.IDENTITY_PROVIDER_OTP,
                              provider_reference=f"provider-secret-reference-{index}", verified_name=f"Private Holder{index}")
        return user, raw

    def command(self, stage="validation", accounts=None, *, expect_failure=False):
        output = StringIO()
        with patch(MODULE + ".database_checks", return_value=DB_READY):
            if expect_failure:
                with self.assertRaises(CommandError):
                    call_command("vas_preflight", stage=stage, account=accounts or [], stdout=output)
            else:
                call_command("vas_preflight", stage=stage, account=accounts or [], stdout=output)
        return json.loads(output.getvalue())

    def package(self):
        output = StringIO()
        arguments = [value for number in self.numbers for value in ("--account", number)]
        call_command("vas_onboarding_package", *arguments, base_url="https://vas.zitch.test", service_email="ops@zitch.test",
                     stdout=output)
        return json.loads(output.getvalue())

    def test_validation_success_is_local_readiness_only(self):
        report = self.command()
        self.assertTrue(report["local_ready"])
        self.assertEqual(report["status"], "ready_for_bank_validation")
        self.assertEqual(report["sample_count"], 3)
        self.assertFalse(report["full_go_live_ready"])
        self.assertTrue(all(row["status"] == "pending_external_verification" for row in report["bank_evidence"]))

    def test_general_report_contains_no_personal_or_secret_values(self):
        result = json.dumps(self.command(accounts=self.numbers))
        private_values = [VALUES["TOKEN"], KEY, VALUES["COLLECTION_ACCOUNT"], VALUES["LIVE_APPROVAL_REFERENCE"],
                          *self.identities, *self.numbers]
        private_values += [account.encrypted_identity for account in self.accounts]
        private_values += [account.consent_reference for account in self.accounts]
        private_values += [user.phone for user in self.users]
        for value in private_values:
            self.assertNotIn(value, result)
        self.assertNotIn("Private Holder", result)

    def test_real_database_probe_and_report_issue_only_reads(self):
        statements = []
        def forbid_mutation(execute, sql, params, many, context):
            statements.append(sql)
            self.assertIsNone(re.match(r"\s*(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|REPLACE)\b", sql, re.I))
            return execute(sql, params, many, context)
        before = (VirtualAccount.objects.count(), Receipt.objects.count(), Transaction.objects.count())
        with connection.execute_wrapper(forbid_mutation):
            report = build_report(accounts=self.numbers)
        self.assertTrue(statements)
        self.assertTrue(report["read_only"])
        self.assertEqual(before, (VirtualAccount.objects.count(), Receipt.objects.count(), Transaction.objects.count()))

    def test_database_vendor_schema_and_migrations_are_actually_probed(self):
        checks = database_checks()
        self.assertTrue(checks["schema"])
        self.assertTrue(checks["migrations"])
        self.assertEqual(checks["postgresql"], connection.vendor == "postgresql")
        self.assertEqual(checks["immutable_evidence"], connection.vendor == "postgresql")

    @skipUnless(connection.vendor == "postgresql", "PostgreSQL trigger enforcement requires PostgreSQL")
    def test_replica_only_trigger_is_not_production_evidence_protection(self):
        self.assertTrue(database_checks()["immutable_evidence"])
        try:
            with connection.cursor() as cursor:
                cursor.execute("ALTER TABLE wema_vas_receipt ENABLE REPLICA TRIGGER wema_vas_receipt_immutable")
            self.assertFalse(database_checks()["immutable_evidence"])
        finally:
            with connection.cursor() as cursor:
                cursor.execute("ALTER TABLE wema_vas_receipt ENABLE TRIGGER wema_vas_receipt_immutable")
        self.assertTrue(database_checks()["immutable_evidence"])

    def test_missing_token_and_missing_samples_exit_nonzero_with_json(self):
        with override_settings(WEMA_VAS={**VALUES, "TOKEN": ""}):
            report = self.command(expect_failure=True)
        self.assertFalse(report["local_ready"])
        self.assertEqual(report["status"], "blocked_local_requirements")
        report = self.command(accounts=self.numbers[:2], expect_failure=True)
        self.assertFalse(report["local_ready"])

    def test_inactive_user_is_never_an_eligible_sample_or_package(self):
        User.objects.filter(pk=self.users[0].pk).update(is_active=False)
        report = self.command(accounts=self.numbers, expect_failure=True)
        self.assertFalse(report["local_ready"])
        with self.assertRaises(CommandError):
            self.package()

    def test_blocked_sample_and_wrong_prefix_are_rejected(self):
        VirtualAccount.objects.filter(pk=self.accounts[0].pk).update(active=False)
        self.assertFalse(self.command(accounts=self.numbers, expect_failure=True)["local_ready"])
        with self.assertRaises(CommandError):
            self.package()
        with override_settings(WEMA_VAS={**VALUES, "MODE": "live", "PREFIX": "712"}):
            self.assertFalse(self.command(accounts=self.numbers, expect_failure=True)["local_ready"])

    def test_missing_legal_name_proof_or_encrypted_identity_blocks_submission(self):
        account = self.accounts[0]
        VirtualAccount.objects.filter(pk=account.pk).update(encrypted_identity="not-a-fernet-value")
        self.assertFalse(self.command(accounts=self.numbers, expect_failure=True)["local_ready"])
        with self.assertRaises(CommandError):
            self.package()
        VirtualAccount.objects.filter(pk=account.pk).update(encrypted_identity=account.encrypted_identity)
        IdentityProof.objects.filter(user=self.users[0]).update(verified_name="")
        self.assertFalse(self.command(accounts=self.numbers, expect_failure=True)["local_ready"])

    def test_mismatched_proof_reference_is_not_treated_as_provisioning_evidence(self):
        VirtualAccount.objects.filter(pk=self.accounts[0].pk).update(verification_reference="unverified-reference")
        self.assertFalse(self.command(accounts=self.numbers, expect_failure=True)["local_ready"])
        with self.assertRaises(CommandError):
            self.package()

    def test_cached_zero_cannot_hide_real_ledger_liability_in_validation_sample(self):
        Transaction.objects.create(user=self.users[0], amount="7.00", service="funding", direction=Transaction.IN,
                                   transaction_status=Transaction.SUCCESS, reference="unreconciled-preflight-credit")
        self.assertEqual(Wallet.objects.get(user=self.users[0]).balance, 0)
        self.assertFalse(self.command(accounts=self.numbers, expect_failure=True)["local_ready"])
        with self.assertRaises(CommandError):
            self.package()

    def test_database_errors_are_redacted_for_http_report_consumers(self):
        secret = "postgresql://operator:private-password@host/db?account=1234567890"
        with patch(MODULE + ".database_checks", side_effect=DatabaseError(secret)):
            report = build_report()
        self.assertFalse(report["local_ready"])
        self.assertNotIn(secret, json.dumps(report))
        self.assertNotIn("private-password", json.dumps(report))

    def test_onboarding_package_contains_only_intended_accounts_and_never_bearer_or_identity(self):
        package = self.package()
        self.assertEqual(package["sample_accounts"], sorted(self.numbers))
        raw = json.dumps(package)
        self.assertNotIn(VALUES["TOKEN"], raw)
        self.assertNotIn(KEY, raw)
        self.assertTrue(all(identity not in raw for identity in self.identities))
        self.assertEqual(len(package["endpoints"]), 5)

    def test_controlled_pilot_local_pass_still_exits_nonzero_without_external_bank_acceptance(self):
        user, raw = self.new_verified_user(4)
        pilot = {**VALUES, "MODE": "live", "PREFIX": "712", "ENABLE_ENROLLMENT": True,
                 "RELEASE_PHASE": "pilot", "PILOT_USER_IDS": [user.pk]}
        with override_settings(WEMA_VAS=pilot):
            account = enroll_verified(user, bvn=raw, consent=True)
            report = self.command(stage="controlled-live-pilot", accounts=[account.number], expect_failure=True)
        self.assertTrue(report["local_ready"])
        self.assertEqual(report["status"], "bank_verification_pending")
        self.assertFalse(report["full_go_live_ready"])

    def test_pilot_pausing_shared_policy_blocks_local_release_readiness(self):
        user, raw = self.new_verified_user(4)
        pilot = {**VALUES, "MODE": "live", "PREFIX": "712", "ENABLE_ENROLLMENT": True,
                 "RELEASE_PHASE": "pilot", "PILOT_USER_IDS": [user.pk]}
        with override_settings(WEMA_VAS=pilot):
            account = enroll_verified(user, bvn=raw, consent=True)
        with override_settings(WEMA_VAS={**pilot, "PILOT_USER_IDS": []}):
            report = self.command(stage="controlled-live-pilot", accounts=[account.number], expect_failure=True)
        self.assertFalse(report["local_ready"])
