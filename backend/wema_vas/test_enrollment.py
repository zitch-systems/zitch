import json
from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.core.cache import cache
from django.utils import timezone

from accounts.models import AccessToken, IdentityProof, User, hash_identifier, record_identity_proof
from wallet.models import Transaction, Wallet, WemaProvisioningAttempt

from .enrollment import customer_account_payload, enroll_verified
from .identity import decrypt_identity
from .models import MigrationApproval, VirtualAccount

SETTINGS = {"ENABLED": True, "MODE": "live", "PREFIX": "712", "TOKEN": "t" * 64,
            "IDENTITY_KEYS": [Fernet.generate_key().decode()], "REQUIRE_HTTPS": True,
            "ENABLE_ENROLLMENT": True, "LIVE_APPROVAL_REFERENCE": "bank-test-approval",
            "COLLECTION_ACCOUNT": "1234567890"}


@override_settings(WEMA_VAS=SETTINGS, BANK_ACCOUNT_PROVIDER="wema_vas", RATELIMIT_ENABLE=False)
class EnrollmentTests(TestCase):
    def setUp(self):
        self.raw = "12345678901"
        self.user = User.objects.create(username="vas-enroll", phone="+2348012345678",
            first_name="Ada", last_name="Eze", phone_verified=True, bvn_verified=True,
            bvn_hash=hash_identifier(self.raw))
        self.wallet = Wallet.objects.create(user=self.user)
        self.proof = record_identity_proof(self.user, "bvn", self.raw,
            source=IdentityProof.IDENTITY_PROVIDER_OTP, provider_reference="verified-otp-ref", verified_name="Ada Eze")
        self.token = AccessToken.issue(self.user).key

    def enroll(self, **kwargs):
        return enroll_verified(self.user, bvn=self.raw, consent=True, **kwargs)

    def test_verified_explicit_consent_allocates_once_without_changing_kyc_or_legacy_fields(self):
        account = self.enroll()
        self.assertEqual(account.pk, self.enroll().pk)
        self.assertTrue(account.number.startswith("712"))
        self.assertEqual(account.display_name, "Zitch/Ada Eze")
        self.assertNotIn(self.raw, account.encrypted_identity)
        self.assertEqual(decrypt_identity(account.encrypted_identity)["bvn"], self.raw)
        self.assertEqual(account.verification_reference, f"IdentityProof:{self.proof.pk}")
        self.wallet.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(self.wallet.account_number, "")
        self.assertEqual(self.wallet.balance, 0)
        self.assertEqual(self.user.bvn_hash, hash_identifier(self.raw))
        payload = customer_account_payload(self.user)
        self.assertTrue(payload["available"])
        self.assertFalse(payload["spending_available"])

    def test_verified_flag_without_proof_is_rejected(self):
        self.proof.delete()
        with self.assertRaises(ValidationError):
            self.enroll()

    def test_mutable_profile_name_cannot_change_bank_legal_name(self):
        User.objects.filter(pk=self.user.pk).update(first_name="Another", last_name="Person")
        self.assertEqual(self.enroll().display_name, "Zitch/Ada Eze")

    def test_old_proof_without_legal_name_requires_new_provider_verification(self):
        IdentityProof.objects.filter(pk=self.proof.pk).update(verified_name="")
        with self.assertRaises(ValidationError):
            self.enroll()

    def test_ownership_challenge_retains_encrypted_provider_name_after_sms_proof(self):
        from accounts.views import (_start_identity_ownership_challenge,
            _confirm_identity_ownership_challenge, _save_verified_identity)
        with patch("accounts.views._otp_code", return_value="987654"), patch(
                "accounts.views.send_sms", return_value={"success": True}), patch(
                "accounts.views.sms_live", return_value=True):
            response = _start_identity_ownership_challenge(self.user, "bvn", self.raw,
                {"success": True, "first_name": "Verified", "last_name": "Holder", "phone": self.user.phone})
        self.assertEqual(response.status_code, 200)
        pending = cache.get(f"kyc_identity:bvn:{self.user.id}")
        self.assertNotIn("Verified", pending["verified_name"])
        raw, error = _confirm_identity_ownership_challenge(self.user, "bvn", "987654")
        self.assertIsNone(error)
        self.assertTrue(_save_verified_identity(self.user, "bvn", raw, verified_name=self.user._provider_verified_name))
        self.assertEqual(self.enroll().display_name, "Zitch/Verified Holder")

    def test_wrong_or_unverified_identity_cannot_be_replaced(self):
        for raw in ("11111111111", "123", 12345678901, {"bvn": self.raw}):
            with self.subTest(raw=type(raw).__name__), self.assertRaises(ValidationError):
                enroll_verified(self.user, bvn=raw, consent=True)
        User.objects.filter(pk=self.user.pk).update(bvn_verified=False)
        with self.assertRaises(ValidationError):
            self.enroll()

    def test_explicit_boolean_consent_required(self):
        for value in (False, None, "true", 1):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                enroll_verified(self.user, bvn=self.raw, consent=value)

    def test_existing_money_or_pending_transactions_require_reconciliation(self):
        self.wallet.balance = Decimal("1")
        self.wallet.save()
        with self.assertRaises(ValidationError):
            self.enroll()
        self.wallet.balance = 0
        self.wallet.save()
        Transaction.objects.create(user=self.user, amount=1, service="transfer", reference="pending-legacy", direction="OUT")
        with self.assertRaises(ValidationError):
            self.enroll()

    def test_legacy_account_requires_exact_durable_cutover_evidence(self):
        self.wallet.account_number = "1234567890"
        self.wallet.save()
        with self.assertRaises(ValidationError):
            self.enroll()
        MigrationApproval.objects.create(user=self.user, legacy_account_number=self.wallet.account_number,
            reference="bank-cutover-evidence-123", approved_by="operator-review-123")
        account = self.enroll()
        self.assertEqual(account.cutover_reference, "bank-cutover-evidence-123")
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.account_number, "1234567890")

    def test_pending_legacy_account_issuance_blocks_parallel_vas_creation(self):
        WemaProvisioningAttempt.objects.create(user=self.user, tracking_id="pending-issue",
            identity_type="bvn", identity_hash=hash_identifier(self.raw), identity_last4=self.raw[-4:],
            expires_at=timezone.now() + timedelta(minutes=10))
        with self.assertRaises(ValidationError):
            self.enroll()
        self.assertFalse(VirtualAccount.objects.exists())

    def test_zero_cached_balance_cannot_hide_a_nonzero_ledger_at_cutover(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        self.wallet.account_number = "1234567890"
        self.wallet.save()
        Transaction.objects.create(user=self.user, amount="7.00", service="funding",
            reference="unreconciled-legacy", direction=Transaction.IN, transaction_status=Transaction.SUCCESS)
        with self.assertRaises(ValidationError):
            self.enroll()
        with self.assertRaises(CommandError):
            call_command("vas_approve_cutover", user_id=self.user.pk,
                legacy_account=self.wallet.account_number, evidence_reference="bank-evidence",
                reviewer_reference="reviewer")
        self.assertFalse(MigrationApproval.objects.exists())

    def test_prefix_collisions_retry_without_duplicating_account(self):
        other = User.objects.create(username="old-collision")
        Wallet.objects.create(user=other, account_number="7120000001")
        with patch("wema_vas.enrollment.secrets.randbelow", side_effect=[1, 2]):
            self.assertEqual(self.enroll().number, "7120000002")

    def test_rollout_gates_hide_legacy_number_and_reject_enrollment(self):
        with override_settings(WEMA_VAS={**SETTINGS, "ENABLE_ENROLLMENT": False}):
            with self.assertRaises(ValidationError):
                self.enroll()
            payload = customer_account_payload(self.user)
            self.assertFalse(payload["has_account"])
            self.assertFalse(payload["enrollment_available"])

    def test_validation_uses_dedicated_accounts_without_customer_funding_or_money(self):
        with override_settings(WEMA_VAS={**SETTINGS, "MODE": "validation", "PREFIX": "711", "ENABLE_ENROLLMENT": False}):
            with self.assertRaises(ValidationError):
                self.enroll()
            account = self.enroll(validation=True)
            self.assertTrue(account.number.startswith("711"))
            self.assertEqual(customer_account_payload(self.user)["account_number"], "")
            self.assertFalse(customer_account_payload(self.user)["available"])

    def test_blocked_account_never_shows_funding_instructions(self):
        account = self.enroll()
        VirtualAccount.objects.filter(pk=account.pk).update(active=False)
        payload = customer_account_payload(self.user)
        self.assertEqual(payload["account_setup_state"], "restricted")
        self.assertEqual(payload["account_number"], "")
        self.assertFalse(payload["enrollment_available"])

    def test_customer_api_requires_customer_token_https_and_returns_no_identifiers(self):
        payload = json.dumps({"bvn": self.raw, "consent": True})
        url = "/api/wallet/vas/enroll/"
        self.assertEqual(self.client.post(url, data=payload, content_type="application/json", secure=True).status_code, 401)
        headers = {"HTTP_AUTHORIZATION": f"Bearer {self.token}"}
        result = self.client.post(url, data=payload, content_type="application/json", secure=True, **headers)
        self.assertEqual(result.status_code, 200, result.content)
        self.assertNotIn(self.raw, result.content.decode())
        self.assertEqual(result["Cache-Control"], "no-store")
        self.assertTrue(self.client.get("/api/wallet/vas/status/", secure=True, **headers).json()["available"])

    @override_settings(BANK_ACCOUNT_PROVIDER="partnership")
    def test_default_provider_does_not_override_legacy_ui(self):
        self.assertIsNone(customer_account_payload(self.user))
