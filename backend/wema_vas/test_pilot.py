"""Live-pilot eligibility never changes durable bank notification handling."""
import json
from decimal import Decimal

from cryptography.fernet import Fernet
from django.core.checks import ERROR
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from accounts.models import IdentityProof, User, hash_identifier, record_identity_proof
from wallet.models import Transaction, Wallet

from .checks import vas_configuration
from .config import enrollment_release_policy
from .enrollment import customer_account_payload, enroll_verified, enrollment_available
from .models import Receipt, VirtualAccount
from .tests import payload

PILOT = {
    "ENABLED": True, "MODE": "live", "PREFIX": "712", "TOKEN": "pilot-test-bank-token-" + "a" * 48,
    "IDENTITY_KEYS": [Fernet.generate_key().decode()], "REQUIRE_HTTPS": True,
    "ENABLE_ENROLLMENT": True, "LIVE_APPROVAL_REFERENCE": "test-bank-pilot-approval",
    "COLLECTION_ACCOUNT": "1234567890", "RELEASE_PHASE": "pilot", "PILOT_USER_IDS": [],
}


@override_settings(WEMA_VAS=PILOT, BANK_ACCOUNT_PROVIDER="wema_vas", TESTING=True,
                   RATELIMIT_ENABLE=False, SECURE_SSL_REDIRECT=False,
                   TXN_ALERTS={"EMAIL": False, "SMS": False, "WHATSAPP": False, "PUSH": False})
class ControlledPilotTests(TestCase):
    def setUp(self):
        self.raw = "12345678901"
        self.user = User.objects.create(username="pilot-member", phone="2348011111111",
            first_name="Pilot", last_name="Member", phone_verified=True, bvn_verified=True,
            bvn_hash=hash_identifier(self.raw))
        self.wallet = Wallet.objects.create(user=self.user)
        record_identity_proof(self.user, "bvn", self.raw, source=IdentityProof.IDENTITY_PROVIDER_OTP,
                              provider_reference="test-proof", verified_name="Pilot Member")
        self.other = User.objects.create(username="not-a-pilot-member", phone="2348011111112",
            phone_verified=True)
        self.allowed = {**PILOT, "PILOT_USER_IDS": [self.user.pk]}

    def enroll(self):
        return enroll_verified(self.user, bvn=self.raw, consent=True)

    def notify(self, account, **changes):
        return self.client.post("/vas/transaction-notification", json.dumps(payload(account, **changes)),
            content_type="application/json", secure=True,
            HTTP_AUTHORIZATION="Bearer " + PILOT["TOKEN"])

    def test_old_enable_flag_cannot_open_general_enrollment(self):
        old = {key: value for key, value in PILOT.items() if key not in {"RELEASE_PHASE", "PILOT_USER_IDS"}}
        with override_settings(WEMA_VAS=old):
            self.assertFalse(enrollment_available(self.user))
            with self.assertRaises(ValidationError):
                self.enroll()
        self.assertFalse(VirtualAccount.objects.exists())

    def test_allowlisted_verified_consented_user_can_enroll_and_see_their_account(self):
        with override_settings(WEMA_VAS=self.allowed):
            self.assertTrue(enrollment_available(self.user))
            self.assertFalse(enrollment_available())
            self.assertFalse(enrollment_available(self.other))
            account = self.enroll()
            self.assertEqual(account.pk, self.enroll().pk)
            self.assertEqual(customer_account_payload(self.user)["account_number"], account.number)
            self.assertFalse(customer_account_payload(self.user)["enrollment_available"])
            self.assertFalse(customer_account_payload(self.user)["spending_available"])
            self.assertFalse(customer_account_payload(self.other)["enrollment_available"])
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 0)
        self.assertEqual(self.wallet.account_number, "")

    def test_nonmember_cannot_bypass_pilot_by_calling_enrollment_directly(self):
        with override_settings(WEMA_VAS={**PILOT, "PILOT_USER_IDS": [self.other.pk]}):
            with self.assertRaises(ValidationError):
                self.enroll()
        self.assertFalse(VirtualAccount.objects.exists())

    def test_empty_or_malformed_allowlist_fails_closed_as_a_whole(self):
        for ids in ([], "", [self.user.pk, "bad"], [self.user.pk, 0], [self.user.pk, True],
                    [self.user.pk, 1.5], f"{self.user.pk},", {"user": self.user.pk}):
            with self.subTest(ids=ids), override_settings(WEMA_VAS={**PILOT, "PILOT_USER_IDS": ids}):
                self.assertFalse(enrollment_available(self.user))
                with self.assertRaises(ValidationError):
                    self.enroll()
        self.assertFalse(VirtualAccount.objects.exists())

    def test_csv_allowlist_is_supported_without_permissive_partial_parsing(self):
        with override_settings(WEMA_VAS={**PILOT, "PILOT_USER_IDS": f" {self.user.pk}, {self.other.pk} "}):
            self.assertEqual(enrollment_release_policy()["pilot_user_ids"], frozenset((self.user.pk, self.other.pk)))
            self.assertTrue(enrollment_available(self.user))

    def test_pilot_still_requires_real_bank_configuration_and_explicit_consent(self):
        for changes in ({"LIVE_APPROVAL_REFERENCE": ""}, {"LIVE_APPROVAL_REFERENCE": "pending"},
                        {"COLLECTION_ACCOUNT": ""}, {"PREFIX": "711"}, {"TOKEN": "short"},
                        {"IDENTITY_KEYS": []}, {"ENABLE_ENROLLMENT": False}, {"ENABLED": False}):
            with self.subTest(changes=tuple(changes)), override_settings(WEMA_VAS={**self.allowed, **changes}):
                self.assertFalse(enrollment_available(self.user))
        with override_settings(WEMA_VAS=self.allowed), self.assertRaises(ValidationError):
            enroll_verified(self.user, bvn=self.raw, consent=False)
        self.assertFalse(VirtualAccount.objects.exists())

    def test_general_phase_needs_separate_launch_approval(self):
        with override_settings(WEMA_VAS={**PILOT, "RELEASE_PHASE": "general"}):
            self.assertFalse(enrollment_available(self.user))
            with self.assertRaises(ValidationError):
                self.enroll()
        with override_settings(WEMA_VAS={**PILOT, "RELEASE_PHASE": "general",
                                         "GENERAL_APPROVAL_REFERENCE": "test-reviewed-launch-approval"}):
            self.assertTrue(enrollment_available(self.user))
            self.assertTrue(enrollment_available())
            self.enroll()

    def test_member_removal_and_closed_phase_hide_funding_without_disabling_notifications(self):
        with override_settings(WEMA_VAS=self.allowed):
            account = self.enroll()
            self.assertEqual(self.notify(account).json()["status"], "00")
        for index, changes in enumerate(({"PILOT_USER_IDS": []}, {"RELEASE_PHASE": "closed"},
                                        {"PILOT_USER_IDS": [self.user.pk, "invalid"]},
                                        {"ENABLE_ENROLLMENT": False})):
            with self.subTest(changes=tuple(changes)), override_settings(WEMA_VAS={**self.allowed, **changes}):
                visible = customer_account_payload(self.user)
                self.assertFalse(visible["available"])
                self.assertEqual(visible["account_number"], "")
                self.assertFalse(visible["enrollment_available"])
                with self.assertRaises(ValidationError):
                    self.enroll()
                # Earlier deliveries stay acknowledged and later genuine bank
                # notifications remain processable even when the pilot pauses.
                self.assertEqual(self.notify(account).json()["status"], "00")
                self.assertEqual(self.notify(account, sessionid=f"LATE-{index}",
                    paymentreference=f"LATE-PAYMENT-{index}").json()["status"], "00")
        self.assertEqual(Receipt.objects.count(), 5)
        self.assertEqual(Transaction.objects.count(), 5)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("6250"))
        account.refresh_from_db()
        self.assertTrue(account.active)

    def test_closed_empty_or_invalid_pilot_does_not_create_startup_errors(self):
        for changes in ({"RELEASE_PHASE": "closed"}, {"PILOT_USER_IDS": []},
                        {"RELEASE_PHASE": "typo"}, {"PILOT_USER_IDS": "invalid"}):
            with self.subTest(changes=changes), override_settings(WEMA_VAS={**self.allowed, **changes}):
                self.assertFalse(any(issue.level >= ERROR for issue in vas_configuration(None)))
