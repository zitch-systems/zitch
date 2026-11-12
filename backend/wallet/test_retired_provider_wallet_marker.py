"""A retained 9PSB wallet NUBAN cannot be advertised after archive cleanup."""
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.db import connection
from django.test import TestCase

from wallet.models import Transaction, Wallet
from wallet.partnership import legacy_funding_blockers, partnership_allowed
from wallet.services import apply_wema_credit, customer_funding_account, wema_provisioned_wallets
from wallet.tests import make_user


class RetiredProviderWalletMarkerTests(TestCase):
    def setUp(self):
        self.user, _ = make_user(
            "08011116543", "orphaned-wallet-bank@example.test", balance="100")
        self.wallet = Wallet.objects.get(user=self.user)

    def test_retired_bank_label_blocks_funding_without_old_bank_table(self):
        for label in ("9PSB", "9 Payment Service Bank", "9-Payment Service Bank", "Wema Bank VAS"):
            with self.subTest(label=label):
                Wallet.objects.filter(pk=self.wallet.pk).update(
                    account_number="9012345678", bank_name=label)
                # A provider archive can be deleted or temporarily unavailable;
                # the wallet's old bank label must independently hold the NUBAN.
                self.assertIn("retired_provider_wallet_review",
                              legacy_funding_blockers(self.user))
                self.assertFalse(partnership_allowed(self.user))
                funding = customer_funding_account(self.user)
                self.assertEqual(funding["account_number"], "")
                self.assertFalse(funding["can_fund"])
                self.assertEqual(funding["account_setup_state"], "legacy_funding_review")

    def test_old_reference_blocks_relabelled_account_on_both_funding_paths(self):
        Wallet.objects.filter(pk=self.wallet.pk).update(
            account_number="0451234567", bank_name="Wema Bank", account_reference="9PSB-1")
        self.wallet.refresh_from_db()
        self.assertFalse(partnership_allowed(self.user))
        self.assertFalse(wema_provisioned_wallets().filter(pk=self.wallet.pk).exists())
        self.assertIsNone(apply_wema_credit(self.wallet, {
            "referenceId": "retired-ref", "amount": "20", "creditType": "Credit", "status": "Successful",
        }))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 100)

    def test_wema_bank_label_is_not_incorrectly_marked_as_retired(self):
        Wallet.objects.filter(pk=self.wallet.pk).update(
            account_number="0123456789", bank_name="Wema Bank")
        self.assertEqual(legacy_funding_blockers(self.user), [])

    def test_inventory_separates_primary_mappings_from_retired_evidence(self):
        from utility.management.commands.partnership_diagnostics import account_inventory

        Wallet.objects.filter(pk=self.wallet.pk).update(
            account_number="0451234567", bank_name="Wema Bank", account_reference="9PSB-1")
        inventory = account_inventory("0451234567")
        self.assertEqual(inventory["wallets_with_primary_numbers"], 1)
        self.assertEqual(inventory["wallets_with_retired_markers"], 1)
        self.assertEqual(inventory["wallets_with_secondary_account_data"], 0)

    def test_detachment_preserves_the_ledger_and_only_clears_exact_resolved_mappings(self):
        from whatsapp.models import AuditLog

        other, _ = make_user("08011116544", "pending-9psb@example.test", balance="100")
        pending_wallet = Wallet.objects.get(user=other)
        old_numbers = ("0451234567", "0451234568")
        for wallet, number in zip((self.wallet, pending_wallet), old_numbers):
            Wallet.objects.filter(pk=wallet.pk).update(account_number=number, bank_name="9PSB")
        Transaction.objects.create(user=other, service="Retired payment", amount="10",
                                   reference="TEST-9PSB-PENDING")
        ledger_count = Transaction.objects.count()
        with connection.cursor() as cursor:
            cursor.execute("CREATE TABLE banking_bankaccount (id integer PRIMARY KEY, "
                           "wallet_id integer NOT NULL, number varchar(10) NOT NULL)")
            cursor.executemany("INSERT INTO banking_bankaccount VALUES (%s,%s,%s)", [
                (1, self.wallet.pk, old_numbers[0]), (2, pending_wallet.pk, old_numbers[1]),
            ])
        self.addCleanup(self._drop_retired_table)
        detach = import_module("wallet.migrations.0033_detach_9psb_wallet_mappings").detach_retired_mappings
        for _ in range(2):
            detach(apps, SimpleNamespace(connection=connection))
        self.wallet.refresh_from_db()
        pending_wallet.refresh_from_db()
        self.assertEqual(self.wallet.account_number, "")
        self.assertEqual(self.wallet.balance, 100)
        self.assertEqual(pending_wallet.account_number, old_numbers[1])
        self.assertEqual(Transaction.objects.count(), ledger_count)
        self.assertEqual(AuditLog.objects.filter(action="retired_9psb_mapping_detached").count(), 1)
        with connection.cursor() as cursor:
            cursor.execute("SELECT number FROM banking_bankaccount ORDER BY id")
            self.assertEqual([row[0] for row in cursor.fetchall()], list(old_numbers))

    def _drop_retired_table(self):
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE IF EXISTS banking_bankaccount")
