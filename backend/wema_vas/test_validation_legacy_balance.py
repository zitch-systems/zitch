"""An individually approved 711 tester retains, but never migrates, old money."""
import json
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import IdentityProof, User, hash_identifier, record_identity_proof
from wallet.models import Transaction, Wallet, WemaFaceSession, WemaProvisioningAttempt
from wallet.services import (LimitExceeded, assert_customer_spending_available,
                             biller_source_for_transaction, credit, debit, wallet_expected_balance)

from .config import validation_legacy_balance_user_ids
from .enrollment import (_allocation_blockers, customer_account_payload,
                         enroll_customer, enroll_verified)
from .management.commands.vas_preflight import sample_readiness
from .models import MigrationApproval, Receipt, VirtualAccount
from .services import account_balance, process_notification
from .tests import payload


VALUES = {
    "ENABLED": True, "MODE": "validation", "PREFIX": "711", "TOKEN": "x" * 64,
    "IDENTITY_KEYS": [Fernet.generate_key().decode()], "REQUIRE_HTTPS": True,
    "ENABLE_ENROLLMENT": False, "ENABLE_VALIDATION_ENROLLMENT": True,
    "VALIDATION_SELF_SERVICE": True, "RELEASE_PHASE": "closed",
}


@override_settings(WEMA_VAS=VALUES, BANK_ACCOUNT_PROVIDER="wema_vas",
                   WEMA_PARTNERSHIP_MODE="archive", WEMA_BILLER_MODE="active",
                   TESTING=True, TXN_ALERTS={"EMAIL": False, "SMS": False,
                                            "WHATSAPP": False, "PUSH": False})
class ValidationLegacyBalanceTests(TestCase):
    def setUp(self):
        self.raw = "12345678901"
        self.user, self.proof = self.make_user(1, self.raw)
        self.wallet = Wallet.objects.create(user=self.user, account_number="0451111101",
            account_name="Retained Customer", account_reference="old-account-reference")
        credit(self.user, "1000", "Legacy funding", reference="retained-real-credit")
        self.wallet.refresh_from_db()
        self.allowed = {**VALUES, "VALIDATION_LEGACY_BALANCE_USER_IDS": [self.user.pk]}

    def make_user(self, suffix, raw):
        user = User.objects.create(username=f"balance-tester-{suffix}",
            phone=f"+234801111110{suffix}", email=f"tester{suffix}@example.test",
            phone_verified=True, email_verified=True, bvn_verified=True,
            bvn_hash=hash_identifier(raw))
        proof = record_identity_proof(user, "bvn", raw,
            source=IdentityProof.IDENTITY_PROVIDER_OTP,
            provider_reference=f"verified-ownership-{suffix}", verified_name=f"Verified Customer {suffix}")
        return user, proof

    def enroll(self):
        return enroll_customer(self.user, bvn=self.raw, consent=True,
            consent_reference="customer-explicit-validation-consent")

    def test_default_and_other_user_allowlist_deny_reconciled_positive_balance(self):
        for values in (VALUES, {**self.allowed, "VALIDATION_LEGACY_BALANCE_USER_IDS": [self.user.pk + 1]}):
            with self.subTest(values=values.keys()), override_settings(WEMA_VAS=values):
                shown = customer_account_payload(self.user)
                self.assertEqual(shown["enrollment_blockers"], ["balance_review"])
                self.assertEqual(shown["migration_message"], shown["enrollment_message"])
                self.assertIn("migration review", shown["migration_message"])
                self.assertNotIn("reconciliation", shown["migration_message"])
                self.assertNotIn("activation pending", shown["migration_message"])
                with self.assertRaises(ValidationError):
                    self.enroll()
        self.assertFalse(VirtualAccount.objects.exists())

    def test_approved_balance_allocates_once_without_changing_old_records(self):
        wallet_before = Wallet.objects.filter(pk=self.wallet.pk).values().get()
        ledger_before = list(Transaction.objects.filter(user=self.user).values())
        with override_settings(WEMA_VAS=self.allowed):
            self.assertEqual(customer_account_payload(self.user)["enrollment_status"], "ready")
            account = self.enroll()
            self.assertEqual(self.enroll().pk, account.pk)
            shown = customer_account_payload(self.user)
        self.assertTrue(account.number.startswith("711"))
        self.assertEqual((account.mode, account.validation_balance, account.cutover_reference),
                         ("validation", Decimal("0"), ""))
        self.assertEqual(shown["migration_message"], "Account activation pending.")
        self.assertEqual(shown["account_number"], "")
        self.assertEqual(Wallet.objects.filter(pk=self.wallet.pk).values().get(), wallet_before)
        self.assertEqual(list(Transaction.objects.filter(user=self.user).values()), ledger_before)
        self.assertFalse(MigrationApproval.objects.exists())

    def test_malformed_allowlist_fails_closed_as_a_whole(self):
        for ids in ("", [], [self.user.pk, "bad"], [self.user.pk, True], [self.user.pk, 0],
                    [self.user.pk, 1.5], f"{self.user.pk},", {"user": self.user.pk},
                    [self.user.pk, "9223372036854775808"]):
            with self.subTest(ids=ids), override_settings(WEMA_VAS={**self.allowed,
                    "VALIDATION_LEGACY_BALANCE_USER_IDS": ids}):
                self.assertEqual(validation_legacy_balance_user_ids(), frozenset())
                with self.assertRaises(ValidationError):
                    self.enroll()
        with override_settings(WEMA_VAS={**self.allowed,
                "VALIDATION_LEGACY_BALANCE_USER_IDS": f" {self.user.pk} "}):
            self.assertEqual(validation_legacy_balance_user_ids(), frozenset({self.user.pk}))

    def test_operator_path_also_requires_exact_validation_policy_for_exception(self):
        changes = ({"ENABLED": False}, {"ENABLE_VALIDATION_ENROLLMENT": False},
            {"ENABLE_VALIDATION_ENROLLMENT": "true"}, {"ENABLE_ENROLLMENT": True},
            {"RELEASE_PHASE": "pilot"}, {"PREFIX": "712"},
            {"VALIDATION_SELF_SERVICE": False, "VALIDATION_USER_IDS": []})
        for change in changes:
            with self.subTest(change=change), override_settings(WEMA_VAS={**self.allowed, **change}):
                with self.assertRaises((ValidationError, ImproperlyConfigured)):
                    enroll_verified(self.user, bvn=self.raw, consent=True, validation=True)
        with override_settings(WEMA_VAS=self.allowed, BANK_ACCOUNT_PROVIDER="partnership"):
            with self.assertRaises(ValidationError):
                enroll_verified(self.user, bvn=self.raw, consent=True, validation=True)
        self.assertFalse(VirtualAccount.objects.exists())

    def test_allowlist_cannot_hide_cached_or_ledger_mismatch(self):
        for cached in (Decimal("0"), Decimal("999"), Decimal("1001")):
            with self.subTest(cached=cached), override_settings(WEMA_VAS=self.allowed):
                Wallet.objects.filter(pk=self.wallet.pk).update(balance=cached)
                self.assertIn("balance_review", customer_account_payload(self.user)["enrollment_blockers"])
                with self.assertRaises(ValidationError):
                    self.enroll()
        self.assertFalse(VirtualAccount.objects.exists())

    def test_negative_matched_balances_are_never_an_exception(self):
        # Database constraints prohibit this state too; guard remains fail-closed.
        self.wallet.balance = Decimal("-1")
        with override_settings(WEMA_VAS=self.allowed), patch(
                "wallet.services.wallet_expected_balance", return_value=Decimal("-1")):
            self.assertIn("balance_review", _allocation_blockers(self.user, self.wallet, validation=True))

    def test_pending_financial_activity_still_blocks_matched_balance(self):
        with override_settings(WEMA_VAS=self.allowed):
            debit(self.user, "100", "Airtime — MTN")
            self.assertEqual(wallet_expected_balance(self.user.pk), Decimal("900"))
            self.assertEqual(customer_account_payload(self.user)["enrollment_blockers"], ["pending_transactions"])
            with self.assertRaises(ValidationError):
                self.enroll()

    def test_active_legacy_issuance_still_blocks(self):
        WemaProvisioningAttempt.objects.create(user=self.user, tracking_id="pending-issuance",
            identity_type="bvn", identity_hash=hash_identifier(self.raw), identity_last4=self.raw[-4:],
            expires_at=timezone.now() + timedelta(minutes=10))
        with override_settings(WEMA_VAS=self.allowed), self.assertRaises(ValidationError):
            self.enroll()

    def test_expired_but_accepted_legacy_issuance_still_blocks(self):
        WemaProvisioningAttempt.objects.create(user=self.user, tracking_id="accepted-issuance",
            identity_type="bvn", identity_hash=hash_identifier(self.raw), identity_last4=self.raw[-4:],
            expires_at=timezone.now() - timedelta(days=1), otp_verified_at=timezone.now() - timedelta(days=2))
        with override_settings(WEMA_VAS=self.allowed), self.assertRaises(ValidationError):
            self.enroll()

    def test_unresolved_face_callback_still_blocks(self):
        WemaFaceSession.objects.create(user=self.user, state="unresolved-face", identity_type="bvn",
            identity_hash=hash_identifier(self.raw), status=WemaFaceSession.VERIFIED,
            account_state="awaiting_callback", expires_at=timezone.now() - timedelta(minutes=10))
        with override_settings(WEMA_VAS=self.allowed), self.assertRaises(ValidationError):
            self.enroll()

    def test_identity_ownership_and_explicit_consent_still_required(self):
        with override_settings(WEMA_VAS=self.allowed):
            for consent in (False, None, "true", 1):
                with self.subTest(consent=consent), self.assertRaises(ValidationError):
                    enroll_customer(self.user, bvn=self.raw, consent=consent)
            with self.assertRaises(ValidationError):
                enroll_customer(self.user, bvn="11111111111", consent=True)
            self.proof.delete()
            shown = customer_account_payload(self.user)
            self.assertEqual(shown["enrollment_blockers"], ["identity_verification"])
            self.assertEqual(shown["migration_message"], shown["enrollment_message"])
            self.assertIn("previous verification is saved", shown["migration_message"])
            self.assertIn("legal name and identity ownership", shown["migration_message"])
            with self.assertRaises(ValidationError):
                self.enroll()
        self.assertFalse(VirtualAccount.objects.exists())
        self.user.refresh_from_db()
        self.assertTrue(self.user.bvn_verified)

    def test_validation_credit_stays_separate_and_bills_use_retained_legacy_funds(self):
        with override_settings(WEMA_VAS=self.allowed):
            account = self.enroll()
            response, status = process_notification(payload(account))
            self.assertEqual((response["status"], status), ("00", 200))
            account.refresh_from_db()
            self.wallet.refresh_from_db()
            self.assertEqual(account_balance(account), Decimal("1250"))
            self.assertEqual(self.wallet.balance, Decimal("1000"))
            self.assertEqual(wallet_expected_balance(self.user.pk), Decimal("1000"))
            receipt = Receipt.objects.get()
            self.assertEqual(receipt.state, Receipt.VALIDATION)
            self.assertIsNone(receipt.transaction_id)
            txn = debit(self.user, "100", "Airtime — MTN")
            self.assertEqual(biller_source_for_transaction(txn.reference, amount="100"), self.wallet.account_number)
            self.assertIsNone(txn.bill_funding.vas_account_id)
            with self.assertRaises(LimitExceeded):
                assert_customer_spending_available(self.user)

    def test_sample_and_package_accept_only_authorized_reconciled_retained_funds(self):
        with override_settings(WEMA_VAS=self.allowed):
            account = self.enroll()
            numbers = [account.number]
            for suffix in (2, 3):
                raw = f"1234567890{suffix}"
                user, _proof = self.make_user(suffix, raw)
                Wallet.objects.create(user=user)
                numbers.append(enroll_customer(user, bvn=raw, consent=True).number)
            self.assertTrue(sample_readiness(numbers, mode="validation", prefix="711")[1])
            out = StringIO()
            account_args = [arg for number in numbers for arg in ("--account", number)]
            call_command("vas_onboarding_package", base_url="https://api.example.test",
                service_email="service@example.test", *account_args, stdout=out)
            self.assertEqual(json.loads(out.getvalue())["sample_accounts"], sorted(numbers))
            Wallet.objects.filter(pk=self.wallet.pk).update(balance="999")
            self.assertFalse(sample_readiness(numbers, mode="validation", prefix="711")[1])
            with self.assertRaises(CommandError):
                call_command("vas_onboarding_package", base_url="https://api.example.test",
                    service_email="service@example.test", *account_args, stdout=StringIO())

    def test_live_cutover_remains_zero_balance_and_requires_separate_approval(self):
        live = {**self.allowed, "MODE": "live", "PREFIX": "712", "ENABLE_ENROLLMENT": True,
            "ENABLE_VALIDATION_ENROLLMENT": False, "RELEASE_PHASE": "general",
            "LIVE_APPROVAL_REFERENCE": "bank-approved-prefix", "GENERAL_APPROVAL_REFERENCE": "approved-launch",
            "COLLECTION_ACCOUNT": "0123456789"}
        with override_settings(WEMA_VAS=live):
            shown = customer_account_payload(self.user)
            self.assertEqual(shown["enrollment_blockers"], ["balance_review", "migration_review"])
            with self.assertRaises(ValidationError):
                self.enroll()
            MigrationApproval.objects.create(user=self.user, legacy_account_number=self.wallet.account_number,
                reference="reviewed-cutover", approved_by="reviewing-operator")
            self.assertEqual(customer_account_payload(self.user)["enrollment_blockers"], ["balance_review"])
            with self.assertRaises(ValidationError):
                self.enroll()
        self.assertFalse(VirtualAccount.objects.exists())
