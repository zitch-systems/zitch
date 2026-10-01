import os
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection, transaction
from django.test import TransactionTestCase

from wallet.models import Transaction, Wallet
from whatsapp.models import AuditLog, SystemSetting


User = get_user_model()


class PurgeTestCustomersTests(TransactionTestCase):
    # This command changes a PostgreSQL trigger inside its own transaction.
    # Test it with committed fixtures, as the standalone management command runs.
    def setUp(self):
        self.staff = User.objects.create_user(
            username="operator", email="operator@zitch.test", is_staff=True,
        )
        self.customer = User.objects.create_user(
            username="customer", email="customer@zitch.test", phone="08010000001",
        )
        Wallet.objects.create(user=self.customer, balance="100.00")
        Transaction.objects.create(
            user=self.customer,
            service="test credit",
            amount="100.00",
            direction=Transaction.IN,
            transaction_status=Transaction.SUCCESS,
            reference="purge-test-reference",
        )
        SystemSetting.objects.create(key="ai_enabled_global", value="false")
        AuditLog.objects.create(action="existing.audit", actor_type="system")

    def test_refuses_without_both_safety_gates(self):
        with self.assertRaises(CommandError):
            call_command("purge_test_customers", confirm="wrong")
        with mock.patch.dict(os.environ, {"ALLOW_TEST_DATA_PURGE": "true"}):
            with self.assertRaises(CommandError):
                call_command("purge_test_customers", confirm="wrong")
        self.assertTrue(User.objects.filter(pk=self.customer.pk).exists())

    def test_deletes_customers_and_ledger_but_preserves_staff(self):
        with mock.patch.dict(os.environ, {"ALLOW_TEST_DATA_PURGE": "true"}):
            call_command("purge_test_customers", confirm="DELETE-ALL-TEST-CUSTOMERS")

        self.assertFalse(User.objects.filter(pk=self.customer.pk).exists())
        self.assertFalse(Transaction.objects.filter(reference="purge-test-reference").exists())
        self.assertTrue(User.objects.filter(pk=self.staff.pk, is_staff=True).exists())
        self.assertTrue(SystemSetting.objects.filter(key="ai_enabled_global").exists())
        self.assertTrue(AuditLog.objects.filter(action="existing.audit").exists())
        purge_audit = AuditLog.objects.get(action="ops.purge_test_customers")
        self.assertEqual(purge_audit.after["customers"], 1)

    def test_preserves_operator_group_member_even_if_staff_flag_is_wrong(self):
        from django.contrib.auth.models import Group

        operator = User.objects.create_user(
            username="finance-operator", email="finance@zitch.test", is_staff=False,
        )
        operator.groups.add(Group.objects.create(name="finance"))

        with mock.patch.dict(os.environ, {"ALLOW_TEST_DATA_PURGE": "true"}):
            call_command("purge_test_customers", confirm="DELETE-ALL-TEST-CUSTOMERS")

        self.assertTrue(User.objects.filter(pk=operator.pk).exists())

    def test_audit_failure_rolls_back_deletion_and_restores_ledger_guard(self):
        with mock.patch.dict(os.environ, {"ALLOW_TEST_DATA_PURGE": "true"}), \
             mock.patch("whatsapp.ops.record_audit", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
                call_command("purge_test_customers", confirm="DELETE-ALL-TEST-CUSTOMERS")

        self.assertTrue(User.objects.filter(pk=self.customer.pk).exists())
        self.assertTrue(Transaction.objects.filter(reference="purge-test-reference").exists())
        self.assertFalse(AuditLog.objects.filter(action="ops.purge_test_customers").exists())
        if connection.vendor == "postgresql":
            with self.assertRaises(IntegrityError), transaction.atomic():
                Transaction.objects.filter(reference="purge-test-reference").update(amount="101.00")
