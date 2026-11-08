"""Changing credentials or runtime mode cannot move another environment's money."""
from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch

from django.db import connection, transaction, DatabaseError
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from wallet.models import Wallet
from .accounts import complete_account, confirm_identity, start_identity, update_account
from .client import ProviderError, Reply
from .models import BankAccount, IdentityChallenge, Instruction
from .notifications import ingest, process
from .payments import dispatch, requery
from .reconciliation import sweep
from .test_client import CONFIG
from .test_identity import PROFILE
from . import test_money
from .upgrades import refresh_upgrade


@override_settings(NINEPSB=CONFIG)
class PaymentEnvironmentTests(TestCase):
    def setUp(self):
        test_money.MoneyTests.setUp(self)
        self.instruction = test_money.MoneyTests.instruction(self)
        self.notice = ingest("transfer", test_money.MoneyTests.payload(self))

    def changed_environment(self):
        return override_settings(NINEPSB={**CONFIG, "ENVIRONMENT": "sandbox",
            "SANDBOX_CUSTOMERS_ENABLED": True, "SANDBOX_USER_IDS": [str(self.user.pk)]})

    def test_direct_recovery_cannot_call_bank_or_change_another_environment(self):
        operations = [lambda: dispatch(self.instruction), lambda: requery(self.instruction),
            lambda: update_account(self.bank), lambda: process(self.notice),
            lambda: refresh_upgrade(self.bank)]
        with self.changed_environment(), patch("banking.client.Client._send") as send:
            for operation in operations:
                with self.subTest(operation=operation), self.assertRaisesRegex(ProviderError, "environment"):
                    operation()
        send.assert_not_called()
        self.instruction.refresh_from_db()
        self.notice.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual(self.instruction.state, "prepared")
        self.assertEqual(self.instruction.attempts, 0)
        self.assertEqual(self.notice.attempts, 0)
        self.assertEqual(self.wallet.balance, 400)

    def test_scheduler_does_not_pick_records_from_another_environment(self):
        BankAccount.objects.filter(pk=self.bank.pk).update(checked_at=timezone.now()-timedelta(hours=1))
        with self.changed_environment(), patch("banking.client.Client._send") as send:
            result = sweep()
        self.assertTrue(all(count == 0 for count in result.values()), result)
        send.assert_not_called()
        self.assertEqual(Instruction.objects.get().state, "prepared")

    def test_existing_account_cannot_be_reopened_in_changed_environment(self):
        with self.changed_environment(), patch("banking.client.Client._send") as send, \
                self.assertRaisesRegex(ProviderError, "environment"):
            start_identity(self.user, "bvn", "12345678901", PROFILE)
        send.assert_not_called()
        self.assertFalse(IdentityChallenge.objects.exists())

    @skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL binding guard")
    def test_account_environment_is_immutable_in_bulk_updates(self):
        for change in ({"environment": "sandbox"}, {"number": "1100000099"}):
            with self.subTest(change=change), self.assertRaises(DatabaseError), transaction.atomic():
                BankAccount.objects.filter(pk=self.bank.pk).update(**change)


@override_settings(NINEPSB=CONFIG)
class IdentityEnvironmentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="environment-identity",
            first_name="Jane", last_name="Doe", phone=PROFILE["phoneNo"], email=PROFILE["email"],
            phone_verified=True, email_verified=True)
        Wallet.objects.create(user=self.user)
        with patch("banking.client.Client.initiate_identity",
                return_value=Reply({"message": "PENDING", "status": "SUCCESS"}, 200)):
            result = start_identity(self.user, "bvn", "12345678901", PROFILE)
        self.row = IdentityChallenge.objects.get(reference=result["reference"])

    def test_otp_and_opening_cannot_cross_environment_after_creation(self):
        self.assertEqual(self.row.environment, "production")
        with override_settings(NINEPSB={**CONFIG, "ENVIRONMENT": "sandbox",
                "SANDBOX_CUSTOMERS_ENABLED": True, "SANDBOX_USER_IDS": [str(self.user.pk)]}), \
                patch("banking.client.Client._send") as send:
            for action in (lambda: confirm_identity(self.user, self.row.reference, "123456"),
                           lambda: complete_account(self.row),
                           lambda: start_identity(self.user, "bvn", "12345678901", PROFILE)):
                with self.subTest(action=action), self.assertRaisesRegex(ProviderError, "environment"):
                    action()
            result = sweep()
        send.assert_not_called()
        self.assertEqual(result["identities"], 0)
        self.row.refresh_from_db()
        self.assertEqual(self.row.state, "pending")

    def test_recovery_refuses_a_foreign_account_attached_after_identity_started(self):
        BankAccount.objects.create(wallet=Wallet.objects.get(user=self.user),
            number="1100000099", name="Jane Doe", environment="sandbox")
        self.row.state, self.row.verified_at = "verified", timezone.now()
        self.row.save(update_fields=["state", "verified_at"])
        with patch("banking.client.Client._send") as send, \
                self.assertRaisesRegex(ProviderError, "environment"):
            complete_account(self.row)
        send.assert_not_called()

    @skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL binding guard")
    def test_challenge_environment_is_immutable_in_bulk_updates(self):
        with self.assertRaises(DatabaseError), transaction.atomic():
            IdentityChallenge.objects.filter(pk=self.row.pk).update(environment="sandbox")
