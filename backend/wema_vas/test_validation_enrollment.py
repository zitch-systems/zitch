"""Invited 711 testing cannot open customer money movement or the live pilot."""
import json
from decimal import Decimal

from cryptography.fernet import Fernet
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from accounts.models import AccessToken, IdentityProof, User, hash_identifier, record_identity_proof
from wallet.models import Transaction, Wallet
from wallet.services import LimitExceeded, assert_customer_spending_available, biller_spending_available

from .enrollment import (CONSENT_VERSION, VALIDATION_CONSENT_VERSION,
                         customer_account_payload, customer_enrollment_available, enroll_customer,
                         enrollment_available, enroll_verified, validation_enrollment_available)
from .models import Receipt, VirtualAccount
from .services import process_notification
from .tests import payload


VALIDATION = {
    "ENABLED": True, "MODE": "validation", "PREFIX": "711", "TOKEN": "validation-token-" + "a" * 48,
    "IDENTITY_KEYS": [Fernet.generate_key().decode()], "REQUIRE_HTTPS": True,
    "ENABLE_ENROLLMENT": False, "RELEASE_PHASE": "closed",
    "ENABLE_VALIDATION_ENROLLMENT": True, "VALIDATION_USER_IDS": [],
}


@override_settings(WEMA_VAS=VALIDATION, BANK_ACCOUNT_PROVIDER="wema_vas", WEMA_PARTNERSHIP_MODE="archive",
                   WEMA_VAS_BILLER_ENABLED=False, TESTING=True, RATELIMIT_ENABLE=False,
                   SECURE_SSL_REDIRECT=False)
class ValidationEnrollmentTests(TestCase):
    def setUp(self):
        self.raw = "12345678901"
        self.user = User.objects.create(username="validation-invitee", phone="+2348011111111",
            first_name="Test", last_name="Participant", phone_verified=True, email_verified=True,
            bvn_verified=True, bvn_hash=hash_identifier(self.raw))
        self.wallet = Wallet.objects.create(user=self.user)
        self.proof = record_identity_proof(self.user, "bvn", self.raw,
            source=IdentityProof.IDENTITY_PROVIDER_OTP, provider_reference="ownership-proof",
            verified_name="Test Participant")
        self.allowed = {**VALIDATION, "VALIDATION_USER_IDS": [self.user.pk]}
        self.headers = {"HTTP_AUTHORIZATION": f"Bearer {AccessToken.issue(self.user).key}"}

    def enroll(self, **kwargs):
        return enroll_customer(self.user, bvn=self.raw, consent=True, **kwargs)

    def test_invited_customer_allocates_once_without_live_approval_or_address(self):
        with override_settings(WEMA_VAS=self.allowed):
            self.assertTrue(customer_enrollment_available(self.user))
            self.assertFalse(enrollment_available(self.user))
            self.assertFalse(customer_enrollment_available())
            before = customer_account_payload(self.user)
            self.assertTrue(before["test_mode"])
            self.assertTrue(before["enrollment_available"])
            account = self.enroll(consent_reference="vas-identity-v1:whatsapp:action:consent-time")
            self.assertEqual(self.enroll().pk, account.pk)
            self.assertEqual(account.consent_reference, "vas-identity-v1:whatsapp:action:consent-time")
            shown = customer_account_payload(self.user)
        self.assertEqual(account.mode, VirtualAccount.VALIDATION)
        self.assertTrue(account.number.startswith("711"))
        self.assertFalse(self.user.address_verified)
        self.assertEqual(shown["validation_account_number"], account.number)
        self.assertEqual(shown["validation_account_name"], "Zitch/Test Participant")
        self.assertEqual(shown["account_number"], "")
        self.assertEqual(shown["account_name"], "")
        self.assertEqual(shown["account_setup_state"], "vas_validation")
        for key in ("available", "has_account", "spending_available", "enrollment_available"):
            self.assertFalse(shown[key])
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("0"))
        self.assertEqual(self.wallet.account_number, "")
        self.assertFalse(Transaction.objects.exists())

    def test_validation_requires_exact_closed_policy_and_separate_switch(self):
        changes = ({"ENABLED": False}, {"ENABLE_VALIDATION_ENROLLMENT": False},
                   {"ENABLE_VALIDATION_ENROLLMENT": "true"}, {"ENABLE_ENROLLMENT": True},
                   {"RELEASE_PHASE": "pilot"}, {"RELEASE_PHASE": "general"},
                   {"RELEASE_PHASE": "invalid"}, {"MODE": "live"}, {"PREFIX": "712"},
                   {"TOKEN": "short"}, {"IDENTITY_KEYS": []})
        for change in changes:
            with self.subTest(change=change), override_settings(WEMA_VAS={**self.allowed, **change}):
                self.assertFalse(validation_enrollment_available(self.user))
                with self.assertRaises(ValidationError):
                    self.enroll()
        with override_settings(WEMA_VAS=self.allowed, BANK_ACCOUNT_PROVIDER="partnership"):
            self.assertFalse(validation_enrollment_available(self.user))
        self.assertFalse(VirtualAccount.objects.exists())

    def test_empty_or_partly_malformed_allowlist_admits_nobody(self):
        for ids in ([], "", [self.user.pk, "bad"], [self.user.pk, 0], [self.user.pk, True],
                    [self.user.pk, 1.5], f"{self.user.pk},", {"user": self.user.pk},
                    [self.user.pk, "9223372036854775808"]):
            with self.subTest(ids=ids), override_settings(WEMA_VAS={**self.allowed, "VALIDATION_USER_IDS": ids}):
                self.assertFalse(customer_enrollment_available(self.user))
                with self.assertRaises(ValidationError):
                    self.enroll()
        with override_settings(WEMA_VAS={**self.allowed, "VALIDATION_USER_IDS": f" {self.user.pk} "}):
            self.assertTrue(validation_enrollment_available(self.user))
        self.assertFalse(VirtualAccount.objects.exists())

    def test_both_phone_and_email_verification_are_required(self):
        for field in ("phone_verified", "email_verified", "is_active"):
            with self.subTest(field=field), override_settings(WEMA_VAS=self.allowed):
                setattr(self.user, field, False)
                self.assertFalse(customer_enrollment_available(self.user))
                with self.assertRaises(ValidationError):
                    self.enroll()
                setattr(self.user, field, True)
        self.assertFalse(VirtualAccount.objects.exists())

    def test_rechecks_durable_contact_revocation_after_acquiring_locks(self):
        # The request's user object remains verified; the database row no longer is.
        User.objects.filter(pk=self.user.pk).update(email_verified=False)
        with override_settings(WEMA_VAS=self.allowed):
            self.assertTrue(customer_enrollment_available(self.user))
            with self.assertRaises(ValidationError):
                self.enroll()
        self.assertFalse(VirtualAccount.objects.exists())

    def test_identity_and_explicit_consent_remain_required(self):
        with override_settings(WEMA_VAS=self.allowed):
            for consent in (False, None, "true", 1):
                with self.subTest(consent=consent), self.assertRaises(ValidationError):
                    enroll_customer(self.user, bvn=self.raw, consent=consent)
            with self.assertRaises(ValidationError):
                enroll_customer(self.user, bvn="11111111111", consent=True)
            self.proof.delete()
            with self.assertRaises(ValidationError):
                self.enroll()
        self.assertFalse(VirtualAccount.objects.exists())

    def test_legacy_profile_with_nonzero_balance_cannot_enter_validation(self):
        with override_settings(WEMA_VAS=self.allowed):
            self.wallet.account_number = "1234567890"
            self.wallet.balance = Decimal("1")
            self.wallet.save()
            with self.assertRaises(ValidationError):
                self.enroll()
        self.assertFalse(VirtualAccount.objects.exists())

    def test_revocation_or_block_hides_only_test_details(self):
        with override_settings(WEMA_VAS=self.allowed):
            account = self.enroll()
        for change in ({"VALIDATION_USER_IDS": []}, {"ENABLE_VALIDATION_ENROLLMENT": False},
                       {"RELEASE_PHASE": "pilot"}, {"PREFIX": "712"}):
            with self.subTest(change=change), override_settings(WEMA_VAS={**self.allowed, **change}):
                shown = customer_account_payload(self.user)
                self.assertEqual(shown["validation_account_number"], "")
                self.assertEqual(shown["validation_account_name"], "")
                self.assertEqual(shown["account_number"], "")
                self.assertFalse(shown["available"])
                with self.assertRaises(ValidationError):
                    self.enroll()
        VirtualAccount.objects.filter(pk=account.pk).update(active=False)
        with override_settings(WEMA_VAS=self.allowed):
            shown = customer_account_payload(self.user)
            self.assertEqual(shown["account_setup_state"], "restricted")
            self.assertEqual(shown["validation_account_number"], "")

    def test_validation_notifications_cannot_be_spent_on_bills_or_transfers(self):
        with override_settings(WEMA_VAS=self.allowed, WEMA_VAS_BILLER_ENABLED=True):
            account = self.enroll()
            response, status = process_notification(payload(account))
            self.assertEqual((response["status"], status), ("00", 200))
            self.assertFalse(biller_spending_available(self.user))
            with self.assertRaises(LimitExceeded):
                assert_customer_spending_available(self.user)
        self.wallet.refresh_from_db()
        account.refresh_from_db()
        self.assertGreater(account.validation_balance, 0)
        self.assertEqual(self.wallet.balance, 0)
        self.assertFalse(Transaction.objects.exists())
        self.assertEqual(Receipt.objects.get().state, Receipt.VALIDATION)

    def test_operator_validation_provisioning_does_not_require_customer_gate_or_email(self):
        User.objects.filter(pk=self.user.pk).update(email_verified=False)
        with override_settings(WEMA_VAS={**VALIDATION, "ENABLE_VALIDATION_ENROLLMENT": False}):
            account = enroll_verified(self.user, bvn=self.raw, consent=True, validation=True)
            self.assertEqual(account.mode, VirtualAccount.VALIDATION)
            self.assertEqual(customer_account_payload(self.user)["validation_account_number"], "")

    def test_customer_endpoint_derives_mode_and_returns_private_test_details(self):
        body = {"bvn": self.raw, "consent": True, "validation": False, "mode": "live",
                "enrollment_mode": "validation", "consent_version": VALIDATION_CONSENT_VERSION}
        with override_settings(WEMA_VAS=self.allowed):
            result = self.client.post("/api/wallet/vas/enroll/", json.dumps(body),
                content_type="application/json", secure=True, **self.headers)
            balance = self.client.post("/api/wallet_balance/", "{}",
                content_type="application/json", secure=True, **self.headers)
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(result["Cache-Control"], "no-store")
        self.assertNotIn(self.raw, result.content.decode())
        self.assertTrue(result.json()["test_mode"])
        self.assertTrue(result.json()["validation_account_number"].startswith("711"))
        self.assertEqual(result.json()["account_number"], "")
        self.assertFalse(result.json()["bill_payments_available"])
        self.assertEqual(VirtualAccount.objects.get().mode, VirtualAccount.VALIDATION)
        self.assertEqual(balance.status_code, 200, balance.content)
        self.assertIs(balance.json()["test_mode"], True)
        self.assertEqual(balance.json()["validation_account_number"], result.json()["validation_account_number"])
        self.assertEqual(Decimal(balance.json()["available_balance"]), 0)
        self.assertFalse(balance.json()["spending_available"])

    def test_client_cannot_opt_into_validation_without_an_invitation(self):
        body = {"bvn": self.raw, "consent": True, "validation": True, "user_id": self.user.pk,
                "enrollment_mode": "validation", "consent_version": VALIDATION_CONSENT_VERSION}
        result = self.client.post("/api/wallet/vas/enroll/", json.dumps(body),
            content_type="application/json", secure=True, **self.headers)
        self.assertEqual(result.status_code, 409)
        self.assertFalse(VirtualAccount.objects.exists())

    def test_customer_endpoint_requires_exact_mode_and_consent_version_before_allocation(self):
        base = {"bvn": self.raw, "consent": True, "enrollment_mode": "validation",
                "consent_version": VALIDATION_CONSENT_VERSION}
        cases = [{key: value for key, value in base.items() if key not in omitted}
                 for omitted in (("enrollment_mode",), ("consent_version",),
                                 ("enrollment_mode", "consent_version"))]
        cases += [{**base, **change} for change in (
            {"enrollment_mode": None}, {"consent_version": None},
            {"enrollment_mode": "live"}, {"consent_version": CONSENT_VERSION},
            {"enrollment_mode": True}, {"consent_version": True})]
        with override_settings(WEMA_VAS=self.allowed):
            for body in cases:
                with self.subTest(mode=body.get("enrollment_mode"), version=body.get("consent_version")):
                    result = self.client.post("/api/wallet/vas/enroll/", json.dumps(body),
                        content_type="application/json", secure=True, **self.headers)
                    self.assertEqual(result.status_code, 409)
                    self.assertEqual(result.json()["code"], "vas_consent_refresh_required")
                    self.assertIn("Refresh", result.json()["message"])
                    self.assertEqual(result["Cache-Control"], "no-store")
                    self.assertNotIn(self.raw, result.content.decode())
                    self.assertFalse(VirtualAccount.objects.exists())
        self.assertFalse(Transaction.objects.exists())

    def test_validation_consent_cannot_be_reused_after_server_switches_to_live(self):
        old_form = {"bvn": self.raw, "consent": True, "enrollment_mode": "validation",
                    "consent_version": VALIDATION_CONSENT_VERSION}
        live = {**self.allowed, "MODE": "live", "PREFIX": "712", "ENABLE_ENROLLMENT": True,
                "ENABLE_VALIDATION_ENROLLMENT": False, "RELEASE_PHASE": "general",
                "LIVE_APPROVAL_REFERENCE": "reviewed-bank-live-evidence",
                "GENERAL_APPROVAL_REFERENCE": "reviewed-customer-launch-evidence",
                "COLLECTION_ACCOUNT": "1234567890"}
        with override_settings(WEMA_VAS=live):
            stale = self.client.post("/api/wallet/vas/enroll/", json.dumps(old_form),
                content_type="application/json", secure=True, **self.headers)
            self.assertEqual(stale.status_code, 409)
            self.assertEqual(stale.json()["code"], "vas_consent_refresh_required")
            self.assertFalse(VirtualAccount.objects.exists())
            current = {**old_form, "enrollment_mode": "live", "consent_version": CONSENT_VERSION}
            no_consent = self.client.post("/api/wallet/vas/enroll/", json.dumps({**current, "consent": False}),
                content_type="application/json", secure=True, **self.headers)
            self.assertEqual(no_consent.status_code, 409)
            self.assertFalse(VirtualAccount.objects.exists())
            accepted = self.client.post("/api/wallet/vas/enroll/", json.dumps(current),
                content_type="application/json", secure=True, **self.headers)
            self.assertEqual(accepted.status_code, 200, accepted.content)
            self.assertEqual(VirtualAccount.objects.get().mode, VirtualAccount.LIVE)
