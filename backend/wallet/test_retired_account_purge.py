from decimal import Decimal
from io import StringIO
from unittest.mock import patch
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, IntegrityError, transaction
from django.test import TestCase, override_settings
from legacy_funding.models import Receipt, VirtualAccount
from wallet.legacy_test_fixtures import account_fixture, retained_receipt
from wallet.legacy_funding import reconciliation_scope
from wallet.models import Transaction, Wallet, UnsettledFunding
from wallet.partnership import partnership_account_number, partnership_allowed
from wallet.services import wallet_balance_payload, wallet_expected_balance, debit, LimitExceeded
from whatsapp.models import AuditLog


@override_settings(WEMA_PARTNERSHIP_MODE="active", WEMA_BILLER_MODE="active")
class RetiredAccountPurgeTests(TestCase):
    def setUp(self):
        self.user, self.account = account_fixture("1")
        self.receipt = retained_receipt(self.account, "100")
        self.other, self.validation = account_fixture("2", mode="validation")
        self.wallet = Wallet.objects.get(user=self.other)
        self.wallet.account_number = "0450000002"
        self.wallet.save(update_fields=["account_number"])

    def run_purge(self, **kwargs):
        opts = dict(expected_accounts=2, expected_receipts=1,
                    expected_unsettled=Decimal("100"), operator_reference="owner-approved-test",
                    stdout=StringIO())
        opts.update(kwargs)
        call_command("purge_retired_accounts", **opts)

    def test_preview_does_not_mutate(self):
        self.run_purge()
        self.assertEqual(VirtualAccount.objects.count(), 2)
        self.assertEqual(Receipt.objects.count(), 1)
        self.assertFalse(UnsettledFunding.objects.exists())

    def test_delete_accounts_preserves_money_and_partnership_wallet(self):
        before = list(Transaction.objects.order_by("pk").values())
        self.run_purge(apply=True)
        self.assertFalse(VirtualAccount.objects.exists())
        self.assertFalse(Receipt.objects.exists())
        self.assertEqual(list(Transaction.objects.order_by("pk").values()), before)
        self.assertEqual(wallet_expected_balance(self.user.pk), Decimal("100"))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.account_number, "0450000002")
        self.assertTrue(partnership_allowed(self.other))
        self.assertFalse(partnership_allowed(self.user))
        self.assertFalse(partnership_account_number(self.account.number))
        hold = UnsettledFunding.objects.get(user=self.user)
        self.assertEqual(hold.amount, Decimal("100"))
        self.assertEqual(hold.evidence["credits"][0]["transaction_id"], self.receipt.transaction_id)
        payload = wallet_balance_payload(self.user)
        self.assertEqual(payload["balance"], Decimal("100"))
        self.assertEqual(payload["available_balance"], Decimal("0"))
        self.assertEqual(payload["legacy_funding_balance"], Decimal("100"))
        self.assertTrue(reconciliation_scope()["legacy_funding_required"])
        for service in ("Transfer to Ada", "Airtime — MTN"):
            with self.assertRaises(LimitExceeded):
                debit(self.user, "1", service)
        self.run_purge(apply=True)
        self.assertEqual(UnsettledFunding.objects.count(), 1)

    def test_changed_inventory_or_amount_fails_closed(self):
        for opts in ({"expected_accounts": 3}, {"expected_receipts": 2},
                     {"expected_unsettled": Decimal("0")}):
            with self.assertRaises(CommandError):
                self.run_purge(apply=True, **opts)
        self.assertEqual(VirtualAccount.objects.count(), 2)
        self.assertFalse(UnsettledFunding.objects.exists())

    def test_held_receipt_blocks_purge(self):
        retained_receipt(self.account, "10", state=Receipt.HELD)
        with self.assertRaises(CommandError):
            self.run_purge(apply=True, expected_receipts=2)
        self.assertEqual(Receipt.objects.count(), 2)

    def test_wallet_using_old_destination_blocks_purge(self):
        Wallet.objects.filter(user=self.user).update(account_number=self.account.number)
        with self.assertRaises(CommandError):
            self.run_purge(apply=True)
        self.assertFalse(UnsettledFunding.objects.exists())

    def test_failure_rolls_back_deletion_and_hold(self):
        with patch.object(AuditLog.objects, "create", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.run_purge(apply=True)
        self.assertEqual(VirtualAccount.objects.count(), 2)
        self.assertEqual(Receipt.objects.count(), 1)
        self.assertFalse(UnsettledFunding.objects.exists())
        self.assert_triggers_enabled()

    def assert_triggers_enabled(self):
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SELECT tgname, tgenabled FROM pg_trigger WHERE tgname IN "
                               "('wema_vas_account_immutable', 'wema_vas_receipt_immutable')")
                self.assertEqual(dict(cursor.fetchall()), {
                    "wema_vas_account_immutable": "O", "wema_vas_receipt_immutable": "O"})

    def test_database_guards_survive_purge(self):
        self.run_purge(apply=True)
        self.assert_triggers_enabled()
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL trigger enforcement")
        with self.assertRaises(IntegrityError), transaction.atomic():
            UnsettledFunding.objects.all().delete()
        with self.assertRaises(IntegrityError), transaction.atomic():
            UnsettledFunding.objects.update(amount=Decimal("1"))
