"""A retained validation account never becomes a customer's live bank account."""
from datetime import timedelta
from decimal import Decimal
from unittest import skipUnless

from cryptography.fernet import Fernet
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import IdentityProof, User, hash_identifier, record_identity_proof
from wallet.models import (Transaction, TransactionAlertDelivery, Wallet,
                           WemaFaceSession, WemaProvisioningAttempt)
from wallet.services import wallet_expected_balance

from .enrollment import (CONSENT_VERSION, VALIDATION_CONSENT_VERSION,
                         customer_account_payload, customer_enrollment_available,
                         enroll_customer, validation_enrollment_available)
from .models import MigrationApproval, Receipt, VirtualAccount
from .services import account_balance, account_details, process_notification
from .tests import payload


VALIDATION = {
    "ENABLED": True, "MODE": "validation", "PREFIX": "711", "TOKEN": "s" * 64,
    "IDENTITY_KEYS": [Fernet.generate_key().decode()], "REQUIRE_HTTPS": True,
    "ENABLE_ENROLLMENT": False, "RELEASE_PHASE": "closed",
    "ENABLE_VALIDATION_ENROLLMENT": True, "VALIDATION_SELF_SERVICE": True,
    "VALIDATION_USER_IDS": [],
}
LIVE = {
    **VALIDATION, "MODE": "live", "PREFIX": "712", "ENABLE_ENROLLMENT": True,
    "ENABLE_VALIDATION_ENROLLMENT": False, "RELEASE_PHASE": "general",
    "LIVE_APPROVAL_REFERENCE": "bank-approved-live-prefix",
    "GENERAL_APPROVAL_REFERENCE": "reviewed-general-release",
    "COLLECTION_ACCOUNT": "0123456789",
}


@override_settings(WEMA_VAS=VALIDATION, BANK_ACCOUNT_PROVIDER="wema_vas",
                   WEMA_PARTNERSHIP_MODE="archive", TESTING=True,
                   TXN_ALERTS={"EMAIL": True, "SMS": False, "WHATSAPP": False, "PUSH": False})
class SameProfileEnrollmentTests(TestCase):
    def setUp(self):
        self.raw = "12345678901"
        self.user = User.objects.create(
            username="same-profile", phone="+2348011111111", email="profile@zitch.test",
            first_name="Verified", last_name="Customer", phone_verified=True,
            email_verified=True, bvn_verified=True, bvn_hash=hash_identifier(self.raw),
        )
        self.wallet = Wallet.objects.create(user=self.user, account_number="1234567890")
        self.proof = record_identity_proof(
            self.user, "bvn", self.raw, source=IdentityProof.IDENTITY_PROVIDER_OTP,
            provider_reference="verified-ownership", verified_name="Verified Customer",
        )

    def enroll(self, **kwargs):
        return enroll_customer(self.user, bvn=self.raw, consent=True, **kwargs)

    def approve(self, account_number=None):
        return MigrationApproval.objects.create(
            user=self.user, legacy_account_number=account_number or self.wallet.account_number,
            reference="reviewed-legacy-cutover", approved_by="operator-evidence",
        )

    def test_zero_balance_legacy_profile_gets_one_validation_account_without_cutover(self):
        self.assertTrue(validation_enrollment_available(self.user))
        before = customer_account_payload(self.user)
        self.assertEqual(before["enrollment_status"], "ready")
        self.assertEqual(before["enrollment_blockers"], [])
        self.assertEqual(before["enrollment_mode"], "validation")
        self.assertEqual(before["consent_version"], VALIDATION_CONSENT_VERSION)
        self.assertTrue(before["re_registration_required"])
        self.assertTrue(before["enrollment_available"])
        self.assertFalse(VirtualAccount.objects.exists())
        account = self.enroll(consent_reference="same-profile-validation-consent",
                              expected_mode="validation", expected_consent_version=VALIDATION_CONSENT_VERSION)
        self.assertEqual(self.enroll().pk, account.pk)
        self.assertEqual(account.mode, VirtualAccount.VALIDATION)
        self.assertTrue(account.number.startswith("711"))
        self.assertEqual(account.cutover_reference, "")
        self.assertEqual(account.consent_reference, "same-profile-validation-consent")
        self.assertEqual(list(self.user.vas_accounts.values_list("pk", flat=True)), [account.pk])
        self.assertFalse(MigrationApproval.objects.exists())
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.account_number, "1234567890")
        self.assertEqual(self.wallet.balance, 0)
        self.assertEqual(wallet_expected_balance(self.user.pk), 0)
        self.assertFalse(Transaction.objects.exists())
        shown = customer_account_payload(self.user)
        self.assertEqual(shown["enrollment_status"], "enrolled")
        self.assertEqual(shown["enrollment_blockers"], [])
        self.assertFalse(shown["re_registration_required"])
        self.assertEqual(shown["validation_account_number"], account.number)
        self.assertEqual(shown["account_number"], "")
        self.assertFalse(shown["available"])
        self.assertFalse(shown["spending_available"])

    def test_only_literal_true_self_service_bypasses_invitation(self):
        settings_without_flag = {key: value for key, value in VALIDATION.items()
                                 if key != "VALIDATION_SELF_SERVICE"}
        choices = [settings_without_flag] + [
            {**VALIDATION, "VALIDATION_SELF_SERVICE": value}
            for value in (False, None, "true", "True", 1, [True])
        ]
        for values in choices:
            with self.subTest(flag=values.get("VALIDATION_SELF_SERVICE")), override_settings(WEMA_VAS=values):
                self.assertFalse(validation_enrollment_available(self.user))
                with self.assertRaises(ValidationError):
                    self.enroll()
        self.assertFalse(VirtualAccount.objects.exists())
        with override_settings(WEMA_VAS={**VALIDATION, "VALIDATION_SELF_SERVICE": False,
                                       "VALIDATION_USER_IDS": [self.user.pk]}):
            self.assertTrue(validation_enrollment_available(self.user))
            self.assertEqual(self.enroll().mode, VirtualAccount.VALIDATION)

    def test_self_service_does_not_bypass_closed_validation_policy(self):
        changes = (
            {"ENABLED": False}, {"ENABLE_VALIDATION_ENROLLMENT": False},
            {"ENABLE_VALIDATION_ENROLLMENT": "true"}, {"ENABLE_ENROLLMENT": True},
            {"RELEASE_PHASE": "pilot"}, {"RELEASE_PHASE": "general"},
            {"MODE": "live"}, {"PREFIX": "712"}, {"TOKEN": "short"}, {"IDENTITY_KEYS": []},
        )
        for change in changes:
            with self.subTest(change=change), override_settings(WEMA_VAS={**VALIDATION, **change}):
                self.assertFalse(validation_enrollment_available(self.user))
                with self.assertRaises(ValidationError):
                    self.enroll()
        with override_settings(BANK_ACCOUNT_PROVIDER="partnership"):
            self.assertFalse(validation_enrollment_available(self.user))
            with self.assertRaises(ValidationError):
                self.enroll()
        self.assertFalse(VirtualAccount.objects.exists())

    def test_self_service_rechecks_contact_revocation_from_database(self):
        for field in ("phone_verified", "email_verified", "is_active"):
            with self.subTest(field=field):
                self.assertTrue(customer_enrollment_available(self.user))
                User.objects.filter(pk=self.user.pk).update(**{field: False})
                with self.assertRaises(ValidationError):
                    self.enroll()
                User.objects.filter(pk=self.user.pk).update(**{field: True})
        self.assertFalse(VirtualAccount.objects.exists())

    def test_self_service_still_requires_exact_identity_and_boolean_consent(self):
        for consent in (False, None, "true", 1):
            with self.subTest(consent=consent), self.assertRaises(ValidationError):
                enroll_customer(self.user, bvn=self.raw, consent=consent)
        with self.assertRaises(ValidationError):
            enroll_customer(self.user, bvn="11111111111", consent=True)
        self.proof.delete()
        shown = customer_account_payload(self.user)
        self.assertEqual(shown["enrollment_status"], "verification_required")
        self.assertEqual(shown["enrollment_blockers"], ["identity_verification"])
        self.assertFalse(shown["enrollment_available"])
        self.assertTrue(shown["enrollment_message"])
        with self.assertRaises(ValidationError):
            self.enroll()
        self.assertFalse(VirtualAccount.objects.exists())

    def test_validation_reuse_rejects_nonzero_cached_wallet_balance(self):
        Wallet.objects.filter(pk=self.wallet.pk).update(balance=Decimal("0.01"))
        self.assertEqual(wallet_expected_balance(self.user.pk), 0)
        shown = customer_account_payload(self.user)
        self.assertEqual(shown["enrollment_status"], "review_required")
        self.assertEqual(shown["enrollment_blockers"], ["balance_review"])
        self.assertFalse(shown["enrollment_available"])
        self.assertTrue(shown["enrollment_message"])
        with self.assertRaises(ValidationError):
            self.enroll()
        self.assertFalse(VirtualAccount.objects.exists())

    def test_blank_historical_name_does_not_shadow_refreshed_proof_or_reset_identity(self):
        IdentityProof.objects.filter(pk=self.proof.pk).update(
            source=IdentityProof.WEMA_WALLET_OTP, verified_name=" \t\n\u00a0 ")
        self.assertFalse(customer_account_payload(self.user)["enrollment_available"])
        identity_before = (self.user.bvn_verified, self.user.bvn_hash,
                           self.user.nin_verified, self.user.nin_hash)
        refreshed = record_identity_proof(self.user, "bvn", self.raw,
            source=IdentityProof.IDENTITY_PROVIDER_OTP, provider_reference="fresh-ownership-proof",
            verified_name="Verified Customer")
        self.assertTrue(customer_account_payload(self.user)["enrollment_available"])
        account = self.enroll()
        self.assertEqual(account.display_name, "Zitch/Verified Customer")
        self.assertEqual(account.verification_reference, f"IdentityProof:{refreshed.pk}")
        self.proof.refresh_from_db()
        self.assertEqual(self.proof.verified_name, " \t\n\u00a0 ")
        self.assertEqual(IdentityProof.objects.filter(user=self.user).count(), 2)
        self.user.refresh_from_db()
        self.assertEqual((self.user.bvn_verified, self.user.bvn_hash,
                          self.user.nin_verified, self.user.nin_hash), identity_before)

    def test_refreshed_name_still_requires_substantive_cross_identity_match(self):
        IdentityProof.objects.filter(pk=self.proof.pk).update(
            source=IdentityProof.WEMA_WALLET_OTP, verified_name=" \n ")
        record_identity_proof(self.user, "bvn", self.raw,
            source=IdentityProof.IDENTITY_PROVIDER_OTP, provider_reference="fresh-ownership-proof",
            verified_name="Verified Customer")
        nin = "98765432109"
        self.user.nin_verified, self.user.nin_hash = True, hash_identifier(nin)
        self.user.save(update_fields=["nin_verified", "nin_hash"])
        record_identity_proof(self.user, "nin", nin,
            source=IdentityProof.IDENTITY_PROVIDER_OTP, provider_reference="nin-ownership-proof",
            verified_name="Different Person")
        with self.assertRaisesMessage(ValidationError, "verified identity names need review"):
            enroll_customer(self.user, bvn=self.raw, nin=nin, consent=True)
        self.assertFalse(VirtualAccount.objects.exists())
        self.user.refresh_from_db()
        self.assertTrue(self.user.bvn_verified)
        self.assertTrue(self.user.nin_verified)

    def test_validation_reuse_rejects_nonzero_ledger_with_zero_cached_balance(self):
        Transaction.objects.create(
            user=self.user, amount="7.00", service="funding", reference="unreconciled-credit",
            direction=Transaction.IN, transaction_status=Transaction.SUCCESS,
        )
        self.assertEqual(self.wallet.balance, 0)
        self.assertEqual(wallet_expected_balance(self.user.pk), Decimal("7.00"))
        shown = customer_account_payload(self.user)
        self.assertEqual(shown["enrollment_status"], "review_required")
        self.assertIn("balance_review", shown["enrollment_blockers"])
        self.assertFalse(shown["enrollment_available"])
        with self.assertRaises(ValidationError):
            self.enroll()
        self.assertFalse(VirtualAccount.objects.exists())

    def test_validation_reuse_rejects_pending_financial_activity(self):
        Transaction.objects.create(
            user=self.user, amount="1.00", service="transfer", reference="pending-transfer",
            direction=Transaction.OUT, transaction_status=Transaction.PENDING,
        )
        with self.assertRaises(ValidationError):
            self.enroll()
        self.assertFalse(VirtualAccount.objects.exists())

    def test_validation_reuse_rejects_pending_legacy_issuance(self):
        WemaProvisioningAttempt.objects.create(
            user=self.user, tracking_id="outstanding-issuance", identity_type="bvn",
            identity_hash=hash_identifier(self.raw), identity_last4=self.raw[-4:],
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        shown = customer_account_payload(self.user)
        self.assertEqual(shown["enrollment_status"], "review_required")
        self.assertEqual(shown["enrollment_blockers"], ["pending_bank_setup"])
        self.assertFalse(shown["enrollment_available"])
        self.assertTrue(shown["enrollment_message"])
        with self.assertRaises(ValidationError):
            self.enroll()
        self.assertFalse(VirtualAccount.objects.exists())

    def test_validation_reuse_rejects_unresolved_face_callback(self):
        WemaFaceSession.objects.create(
            user=self.user, state="unresolved-callback", identity_type="bvn",
            identity_hash=hash_identifier(self.raw), status=WemaFaceSession.VERIFIED,
            account_state="awaiting_callback", expires_at=timezone.now() - timedelta(minutes=10),
        )
        with self.assertRaises(ValidationError):
            self.enroll()
        self.assertFalse(VirtualAccount.objects.exists())

    def test_retained_validation_allows_distinct_live_account_only_after_cutover(self):
        validation = self.enroll()
        process_notification(payload(validation))
        validation.refresh_from_db()
        original = (validation.number, validation.mode, validation.prefix,
                    validation.validation_balance, validation.consent_reference)
        with override_settings(WEMA_VAS=LIVE):
            with self.assertRaises(ValidationError):
                self.enroll()
            self.assertEqual(self.user.vas_accounts.count(), 1)
            approval = self.approve()
            live = self.enroll(consent_reference="separate-live-consent")
            self.assertEqual(self.enroll().pk, live.pk)
            shown = customer_account_payload(self.user)
        self.assertNotEqual(live.pk, validation.pk)
        self.assertEqual(live.mode, VirtualAccount.LIVE)
        self.assertTrue(live.number.startswith("712"))
        self.assertEqual(live.cutover_reference, approval.reference)
        self.assertEqual(live.consent_reference, "separate-live-consent")
        self.assertEqual(live.validation_balance, 0)
        self.assertEqual(self.user.vas_accounts.count(), 2)
        validation.refresh_from_db()
        self.assertEqual((validation.number, validation.mode, validation.prefix,
                          validation.validation_balance, validation.consent_reference), original)
        self.assertEqual(shown["account_number"], live.number)
        self.assertEqual(shown["validation_account_number"], "")
        self.assertTrue(shown["available"])
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.account_number, "1234567890")
        self.assertEqual(self.wallet.balance, 0)
        self.assertFalse(Transaction.objects.exists())
        self.assertEqual(Receipt.objects.get().account_id, validation.pk)

    def test_retained_validation_does_not_make_mismatched_cutover_approval_valid(self):
        validation = self.enroll()
        self.approve(account_number="9876543210")
        with override_settings(WEMA_VAS=LIVE), self.assertRaises(ValidationError):
            self.enroll()
        self.assertEqual(list(self.user.vas_accounts.values_list("pk", flat=True)), [validation.pk])

    def test_stale_mode_or_consent_cannot_allocate_live_account_after_configuration_changes(self):
        validation = self.enroll()
        self.approve()
        with override_settings(WEMA_VAS=LIVE):
            for expected in (
                {"expected_mode": "validation", "expected_consent_version": VALIDATION_CONSENT_VERSION},
                {"expected_mode": "live", "expected_consent_version": VALIDATION_CONSENT_VERSION},
                {"expected_mode": "validation", "expected_consent_version": CONSENT_VERSION},
            ):
                with self.subTest(expected=expected), self.assertRaises(ValidationError):
                    self.enroll(**expected)
                self.assertEqual(list(self.user.vas_accounts.values_list("pk", flat=True)), [validation.pk])
            live = self.enroll(expected_mode="live", expected_consent_version=CONSENT_VERSION)
        self.assertEqual(live.mode, VirtualAccount.LIVE)
        self.assertNotEqual(live.pk, validation.pk)

    def test_new_live_account_rechecks_financial_state_after_validation(self):
        validation = self.enroll()
        self.approve()
        Transaction.objects.create(
            user=self.user, amount="3.00", service="funding", reference="late-legacy-credit",
            direction=Transaction.IN, transaction_status=Transaction.SUCCESS,
        )
        with override_settings(WEMA_VAS=LIVE), self.assertRaises(ValidationError):
            self.enroll()
        self.assertEqual(list(self.user.vas_accounts.values_list("pk", flat=True)), [validation.pk])

    def test_validation_receipts_cannot_credit_existing_live_wallet(self):
        validation = self.enroll()
        self.approve()
        with override_settings(WEMA_VAS=LIVE):
            live = self.enroll()
            live_body = payload(live, sessionid="LIVE-SESSION", paymentreference="LIVE-PAYMENT", amount="20.00")
            self.assertEqual(process_notification(live_body)[0]["status"], "00")
            self.assertIsNone(account_details(validation.number))
        ledger_ids = set(Transaction.objects.values_list("pk", flat=True))
        alerts_before = TransactionAlertDelivery.objects.count()
        body = payload(validation, sessionid="TEST-SESSION", paymentreference="TEST-PAYMENT", amount="99.00")
        first = process_notification(body)
        self.assertEqual(first, process_notification(body))
        self.assertEqual(first[0]["status"], "00")
        self.assertIsNone(account_details(live.number))
        self.wallet.refresh_from_db()
        validation.refresh_from_db()
        live.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("20.00"))
        self.assertEqual(wallet_expected_balance(self.user.pk), Decimal("20.00"))
        self.assertEqual(validation.validation_balance, Decimal("99.00"))
        self.assertEqual(live.validation_balance, 0)
        self.assertEqual(account_balance(live), Decimal("20.00"))
        self.assertEqual(set(Transaction.objects.values_list("pk", flat=True)), ledger_ids)
        self.assertEqual(TransactionAlertDelivery.objects.count(), alerts_before)
        test_receipt = Receipt.objects.get(account=validation)
        self.assertEqual(test_receipt.state, Receipt.VALIDATION)
        self.assertIsNone(test_receipt.transaction_id)
        shown = customer_account_payload(self.user)
        self.assertEqual(shown["validation_account_number"], validation.number)
        self.assertEqual(shown["account_number"], "")
        self.assertFalse(shown["spending_available"])

    def test_database_allows_two_modes_but_rejects_second_account_in_same_mode(self):
        validation = self.enroll()
        self.approve()
        with override_settings(WEMA_VAS=LIVE):
            live = self.enroll()
        for account, number in ((validation, "7119999999"), (live, "7129999999")):
            fields = {field: getattr(account, field) for field in (
                "user_id", "display_name", "encrypted_identity", "verification_reference",
                "consent_reference", "cutover_reference", "verified_at", "mode", "prefix",
            )}
            # Ensure the rejected insert tests the owner/mode uniqueness, not a
            # coincidental collision with the randomly allocated bank number.
            if account.number == number:
                number = account.prefix + "9999998"
            with self.subTest(mode=account.mode), self.assertRaises(IntegrityError), transaction.atomic():
                VirtualAccount.objects.create(number=number, **fields)
        self.assertEqual(self.user.vas_accounts.count(), 2)

    def test_model_cannot_convert_retained_validation_account_to_live(self):
        validation = self.enroll()
        validation.mode, validation.prefix, validation.number = "live", "712", "7120000001"
        with self.assertRaises(ValidationError):
            validation.save()
        validation.refresh_from_db()
        self.assertEqual((validation.mode, validation.prefix), ("validation", "711"))
        self.assertTrue(validation.number.startswith("711"))

    @skipUnless(connection.vendor == "postgresql", "PostgreSQL bank-evidence triggers")
    def test_postgres_rejects_valid_looking_mode_conversion_and_owner_reassignment(self):
        validation = self.enroll()
        other = User.objects.create(username="different-profile")
        original = (validation.number, validation.mode, validation.prefix, validation.user_id)
        changes = (
            {"mode": "live", "prefix": "712", "number": "7120000001"},
            {"user_id": other.pk},
        )
        for change in changes:
            # No other account exists to cause a uniqueness collision, and the
            # replacement prefix/number is valid. Only immutability blocks this.
            with self.subTest(change=change), self.assertRaises(IntegrityError), transaction.atomic():
                VirtualAccount.objects.filter(pk=validation.pk).update(**change)
            validation.refresh_from_db()
            self.assertEqual((validation.number, validation.mode, validation.prefix, validation.user_id), original)
