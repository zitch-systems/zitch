import importlib
from types import SimpleNamespace
from unittest import skipUnless

from django.apps import apps
from django.db import connection, transaction, DatabaseError
from django.test import TestCase

from accounts.models import User
from wallet.models import Wallet, Transaction
from .models import PreviousBankArchive, HistoricalBalanceAdjustment


class ArchiveTests(TestCase):
    def test_retained_statement_rows_cannot_credit_an_archived_or_current_bank_wallet(self):
        from wallet.services import apply_wema_credit
        from .models import BankAccount
        row = {"referenceId": "OLD-STATEMENT", "amount": "100.00",
               "creditType": "Credit", "status": "Successful"}
        for archived in (True, False):
            with self.subTest(archived=archived):
                user = User.objects.create_user(username=f"statement-{archived}")
                wallet = Wallet.objects.create(user=user, balance=500, account_number="1234567890" if archived else "1234567891")
                if archived:
                    PreviousBankArchive.objects.create(wallet=wallet, balance=500, account_number=wallet.account_number)
                else:
                    BankAccount.objects.create(wallet=wallet, number=wallet.account_number, name="Test Customer")
                self.assertIsNone(apply_wema_credit(wallet, row))
                wallet.refresh_from_db()
                self.assertEqual(wallet.balance, 500)
                self.assertFalse(Transaction.objects.filter(user=user).exists())

    def test_snapshot_removes_funding_destination_without_deleting_liability(self):
        user = User.objects.create_user(username="historical", tier=2, bvn_verified=True, nin_verified=True, face_verified=True)
        wallet = Wallet.objects.create(user=user, balance=500, account_number="1234567890", account_name="Historical User", bank_name="Previous bank", bank_tier=2)
        txn = Transaction.objects.create(user=user, reference="old-ledger", amount=500, direction=Transaction.IN, transaction_status=Transaction.SUCCESS)
        migration = importlib.import_module("banking.migrations.0002_archive_previous_bank")
        with connection.cursor() as cursor:
            migration.archive(apps, SimpleNamespace(connection=connection, execute=cursor.execute))
        wallet.refresh_from_db()
        user.refresh_from_db()
        archive = PreviousBankArchive.objects.get(wallet=wallet)
        self.assertEqual(archive.account_number, "1234567890")
        self.assertEqual(archive.balance, 500)
        self.assertEqual(wallet.balance, 500)
        self.assertEqual(wallet.account_number, "")
        self.assertEqual(wallet.bank_name, "")
        self.assertEqual(user.tier, 0)
        self.assertFalse(user.face_verified)
        self.assertTrue(Transaction.objects.filter(pk=txn.pk).exists())

    @skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL evidence triggers")
    def test_archived_evidence_cannot_be_changed_through_bulk_sql(self):
        user = User.objects.create_user(username="immutable")
        wallet = Wallet.objects.create(user=user)
        row = PreviousBankArchive.objects.create(wallet=wallet, account_number="1234567890", balance=100)
        with self.assertRaises(DatabaseError), transaction.atomic():
            PreviousBankArchive.objects.filter(pk=row.pk).update(balance=0)
        with self.assertRaises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("DELETE FROM banking_previousbankarchive WHERE id=%s", [row.pk])

    @skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL evidence triggers")
    def test_historical_adjustment_cannot_be_rewritten(self):
        user = User.objects.create_user(username="immutable-refund")
        wallet = Wallet.objects.create(user=user)
        archive = PreviousBankArchive.objects.create(wallet=wallet, account_number="1234567890", balance=100)
        txn = Transaction.objects.create(user=user, reference="retained-refund", amount=10, direction=Transaction.IN, transaction_status=Transaction.SUCCESS)
        row = HistoricalBalanceAdjustment.objects.create(archive=archive, transaction=txn, amount=10)
        with self.assertRaises(DatabaseError), transaction.atomic():
            HistoricalBalanceAdjustment.objects.filter(pk=row.pk).update(amount=-10)
