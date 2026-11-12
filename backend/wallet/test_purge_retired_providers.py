"""wallet.0034 drops every 9PSB/Wema VAS table and keeps customer money."""
from importlib import import_module
from types import SimpleNamespace

from django.apps import apps
from django.db import connection
from django.test import TransactionTestCase

from transfers.models import Bank
from wallet.models import Transaction, Wallet
from wallet.tests import make_user
from whatsapp.models import AuditLog

purge = import_module("wallet.migrations.0034_purge_retired_providers").purge


def _editor():
    return SimpleNamespace(connection=connection, quote_name=connection.ops.quote_name)


class PurgeRetiredProvidersTests(TransactionTestCase):
    def test_tables_dropped_rows_archived_encrypted_not_copied_balances_kept(self):
        user, _ = make_user("08011116545", "purge@example.test", balance="250")
        wallet = Wallet.objects.get(user=user)
        ledger = Transaction.objects.count()
        Bank.objects.create(code="9psb", name="9PSB", bank_code="120001", active=False)
        with connection.cursor() as cursor:
            cursor.execute("CREATE TABLE banking_previousbankarchive (id integer PRIMARY KEY, "
                           "wallet_id integer, account_number varchar(20), balance numeric(14,2))")
            cursor.execute("CREATE TABLE banking_bankaccount (id integer PRIMARY KEY, "
                           "wallet_id integer, number varchar(10), encrypted_profile text)")
            cursor.execute("CREATE TABLE wema_vas_virtualaccount (id integer PRIMARY KEY)")
            cursor.execute("INSERT INTO banking_previousbankarchive VALUES (1, %s, '0123456789', 250.00)",
                           [wallet.pk])
            cursor.execute("INSERT INTO banking_bankaccount VALUES (1, %s, '7110000001', 'x')",
                           [wallet.pk])

        purge(apps, _editor())

        tables = connection.introspection.table_names()
        self.assertFalse([t for t in tables if t.startswith(("banking_", "wema_vas_"))])
        self.assertFalse(Bank.objects.filter(code="9psb").exists())
        rows = AuditLog.objects.filter(action="retired_provider_row_archived")
        self.assertEqual(rows.count(), 2)
        archive = rows.get(target="banking_previousbankarchive:1").after
        self.assertEqual(archive["account_number"], "0123456789")
        self.assertEqual(float(archive["balance"]), 250.0)
        self.assertNotIn("encrypted_profile", rows.get(target="banking_bankaccount:1").after)
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 250)
        self.assertEqual(Transaction.objects.count(), ledger)

    def test_clean_database_is_a_no_op(self):
        purge(apps, _editor())
