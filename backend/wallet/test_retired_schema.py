"""Schema retirement refuses data loss and preserves active payment protections.

The wema_vas tombstone app that once described the retired tables is gone, so
migration 0031 works from table names alone. These tests build the leftovers by
hand, the way an old database still carries them.
"""
import importlib

from django.db import connection, IntegrityError, transaction
from django.test import TransactionTestCase, override_settings

from wallet.funding_test_fixtures import funding_hold
from wallet.models import BillFundingBinding, BillFundingRefund, Transaction, UnsettledFunding, Wallet
from wallet.services import debit, refund
from wallet.tests import make_user

remove = importlib.import_module("wallet.migrations.0031_drop_retired_account_schema")


@override_settings(WEMA_PARTNERSHIP_MODE="active", WEMA_BILLER_MODE="active")
class RetiredSchemaTests(TransactionTestCase):
    def build_leftovers(self):
        with connection.cursor() as cursor:
            for table in remove.RETIRED_TABLES:
                cursor.execute(f"CREATE TABLE {table} (id integer PRIMARY KEY)")
            cursor.execute("ALTER TABLE wallet_billfundingbinding ADD COLUMN vas_account_id integer NULL")
            if connection.vendor == "postgresql":
                for name in ("wema_vas_account_immutable_fn", "wema_vas_evidence_immutable_fn"):
                    cursor.execute(f"CREATE FUNCTION {name}() RETURNS trigger AS $$ BEGIN "
                                   "RETURN NEW; END; $$ LANGUAGE plpgsql")

    def tearDown(self):
        tables = set(connection.introspection.table_names())
        with connection.cursor() as cursor:
            for table in remove.RETIRED_TABLES:
                if table in tables:
                    cursor.execute(f"DELETE FROM {table}")
        self.remove_schema()
        super().tearDown()

    def remove_schema(self):
        with connection.schema_editor() as editor:
            remove.drop_empty_schema(None, editor)

    def binding_columns(self):
        with connection.cursor() as cursor:
            return {c.name for c in connection.introspection.get_table_description(
                cursor, "wallet_billfundingbinding")}

    def test_a_fresh_database_has_nothing_to_drop(self):
        self.remove_schema()
        self.assertFalse(any(t.startswith("wema_vas_") for t in connection.introspection.table_names()))
        self.assertNotIn("vas_account_id", self.binding_columns())

    def test_nonempty_schema_stops_without_deleting_records(self):
        self.build_leftovers()
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO wema_vas_virtualaccount (id) VALUES (1)")
        with self.assertRaisesRegex(RuntimeError, "not empty"):
            self.remove_schema()
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM wema_vas_virtualaccount")
            self.assertEqual(cursor.fetchone()[0], 1)
        self.assertIn("vas_account_id", self.binding_columns())

    def test_empty_schema_removal_preserves_money_and_partnership_bill_refunds(self):
        self.build_leftovers()
        user, _ = make_user("08077770002", "schema-money@example.test", balance="1000", tier=3)
        Wallet.objects.filter(user=user).update(account_number="0457777002")
        bill = debit(user, "100", "Airtime — MTN")
        held_user, _ = make_user("08077770003", "schema-hold@example.test")
        hold = funding_hold(held_user, "100")
        ledger = list(Transaction.objects.order_by("pk").values())
        balances = list(Wallet.objects.order_by("pk").values_list("pk", "balance"))
        self.remove_schema()
        self.assertFalse(any(t.startswith("wema_vas_") for t in connection.introspection.table_names()))
        self.assertNotIn("vas_account_id", self.binding_columns())
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
