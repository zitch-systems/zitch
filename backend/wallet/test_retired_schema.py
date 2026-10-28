"""Schema retirement refuses data loss and preserves active payment protections."""
import importlib

from django.apps import apps
from django.db import connection, IntegrityError, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from wallet.funding_test_fixtures import funding_hold
from wallet.models import BillFundingBinding, BillFundingRefund, Transaction, UnsettledFunding, Wallet
from wallet.services import debit, refund
from wallet.tests import make_user

remove = importlib.import_module("wallet.migrations.0031_drop_retired_account_schema")
detach = importlib.import_module("wallet.migrations.0030_detach_retired_funding")
old_guards = importlib.import_module("legacy_funding.migrations.0003_durable_bank_evidence")
bill_guards = importlib.import_module("wallet.migrations.0026_bill_funding_immutable")


class FixtureRollback(Exception):
    pass


@override_settings(WEMA_PARTNERSHIP_MODE="active", WEMA_BILLER_MODE="active")
class RetiredSchemaTests(TransactionTestCase):
    def setUp(self):
        self.old = remove.historical_apps(connection)
        with connection.schema_editor() as editor:
            for name in ("MigrationApproval", "VirtualAccount", "Receipt"):
                editor.create_model(self.old.get_model("wema_vas", name))
            binding = self.old.get_model("wallet", "BillFundingBinding")
            editor.add_field(binding, binding._meta.get_field("vas_account"))
            old_guards.install(self.old, editor)
            bill_guards.remove(self.old, editor)
            bill_guards.install(self.old, editor)

    def tearDown(self):
        if "wema_vas_virtualaccount" in connection.introspection.table_names():
            self.remove_schema()
        super().tearDown()

    def remove_schema(self):
        with connection.schema_editor() as editor:
            remove.drop_empty_schema(apps, editor)

    def test_nonempty_schema_stops_both_stages_without_deleting_records(self):
        user, _ = make_user("08077770001", "schema-gate@example.test")
        Account = self.old.get_model("wema_vas", "VirtualAccount")
        with self.assertRaises(FixtureRollback), transaction.atomic():
            account = Account.objects.create(user_id=user.pk, number="9990000001", prefix="999",
                display_name="Zitch/Test", encrypted_identity="test", verification_reference="proof",
                consent_reference="consent", verified_at=timezone.now(), mode="live")
            # The gate runs before any schema mutation in either migration.
            for operation in (detach.require_empty, remove.drop_empty_schema):
                with self.assertRaisesRegex(RuntimeError, "not empty"):
                    operation(self.old, connection.schema_editor())
                self.assertTrue(Account.objects.filter(pk=account.pk).exists())
            raise FixtureRollback

    def test_empty_schema_removal_preserves_money_and_partnership_bill_refunds(self):
        user, _ = make_user("08077770002", "schema-money@example.test", balance="1000", tier=3)
        Wallet.objects.filter(user=user).update(account_number="0457777002")
        bill = debit(user, "100", "Airtime — MTN")
        held_user, _ = make_user("08077770003", "schema-hold@example.test")
        hold = funding_hold(held_user, "100")
        ledger = list(Transaction.objects.order_by("pk").values())
        balances = list(Wallet.objects.order_by("pk").values_list("pk", "balance"))
        self.remove_schema()
        tables = connection.introspection.table_names()
        self.assertFalse(any(table.startswith("wema_vas_") for table in tables))
        self.assertEqual(list(apps.get_app_config("wema_vas").get_models()), [])
        with connection.cursor() as cursor:
            columns = connection.introspection.get_table_description(cursor, "wallet_billfundingbinding")
        self.assertNotIn("vas_account_id", {column.name for column in columns})
        self.assertEqual(list(Transaction.objects.order_by("pk").values()), ledger)
        self.assertEqual(list(Wallet.objects.order_by("pk").values_list("pk", "balance")), balances)
        self.assertTrue(UnsettledFunding.objects.filter(pk=hold.pk, amount="100").exists())
        self.assertTrue(refund(bill))
        self.assertFalse(refund(bill))
        self.assertEqual(BillFundingRefund.objects.count(), 1)
        self.assertEqual(Wallet.objects.get(user=user).balance, 1000)
        # New reservations and the below-ORM guards still work after the drop.
        new_bill = debit(user, "100", "Data — MTN")
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SELECT proname FROM pg_proc WHERE proname LIKE 'wema_vas_%%'")
                self.assertEqual(cursor.fetchall(), [])
            for action in (lambda: BillFundingBinding.objects.all().delete(),
                           lambda: UnsettledFunding.objects.update(amount="1"),
                           lambda: BillFundingRefund.objects.bulk_create([
                               BillFundingRefund(binding=new_bill.bill_funding)])):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    action()
