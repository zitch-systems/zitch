from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, IntegrityError, transaction
from django.test import TestCase, override_settings

from wallet.models import RetiredFundingArchive, UnsettledFunding, Transaction, Wallet
from banking.audit import snapshot
from wallet.partnership import partnership_allowed, retired_funding_destination
from wallet.services import wallet_balance_payload, wallet_expected_balance
from wallet.tests import make_user


class RetiredBalanceArchiveTests(TestCase):
    def setUp(self):
        import hashlib
        self.user, _ = make_user("08044441234", "archive@example.test", balance="100")
        self.credit = Transaction.objects.get(user=self.user)
        self.hold = UnsettledFunding.objects.create(user=self.user, amount="100.00",
            destination_digest=hashlib.sha256(b"9081234567").hexdigest(),
            operator_reference="original-purge",
            evidence={"credits": [{"transaction_id": self.credit.pk, "amount": "100.00"}]})

    def run_archive(self, **opts):
        call_command("archive_retired_balances", expected_count=1,
            expected_amount=Decimal("100"), operator_reference="owner-approved-archive",
            stdout=StringIO(), **opts)

    def test_preview_preserves_all_money(self):
        self.run_archive()
        self.assertFalse(RetiredFundingArchive.objects.exists())
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("100"))

    def test_archive_zeroes_active_wallet_without_forgiving_liability(self):
        before = snapshot()
        with patch("utility.wema.transfer") as bank:
            self.run_archive(apply=True)
            self.run_archive(apply=True)
        bank.assert_not_called()
        self.assertEqual(RetiredFundingArchive.objects.count(), 1)
        self.assertEqual(wallet_expected_balance(self.user.pk), Decimal("0"))
        payload = wallet_balance_payload(self.user)
        self.assertEqual(payload["balance"], Decimal("0"))
        self.assertEqual(payload["legacy_funding_balance"], Decimal("100"))
        after = snapshot()
        self.assertEqual(after["ledger_liability"], before["ledger_liability"])
        self.assertEqual(after["stored_liability"], before["stored_liability"])
        self.assertEqual(Decimal(after["archived_liability"]), Decimal("100"))
        # Archiving the liability permits new account setup; it does not make
        # the retained money spendable or enable a provider connection.
        self.assertTrue(partnership_allowed(self.user))
        self.assertTrue(retired_funding_destination("9081234567"))
        self.credit.refresh_from_db()
        self.assertEqual(self.credit.amount, Decimal("100"))
        self.assertEqual(UnsettledFunding.objects.get(pk=self.hold.pk).amount, Decimal("100"))
        with patch("utility.wema.wema_live", return_value=True), \
             patch("utility.alerts.alert"), self.assertRaises(CommandError):
            call_command("settlement_report", fail_on_breach=True, stdout=StringIO())

    def test_partnership_balance_cannot_be_archived(self):
        Wallet.objects.filter(user=self.user).update(account_number="0451234567")
        with self.assertRaises(CommandError):
            self.run_archive(apply=True)
        self.assertFalse(RetiredFundingArchive.objects.exists())
        self.assertEqual(wallet_expected_balance(self.user.pk), Decimal("100"))

    def test_mixed_or_inconsistent_money_aborts_atomically(self):
        Wallet.objects.filter(user=self.user).update(balance=Decimal("101"))
        with self.assertRaises(CommandError):
            self.run_archive(apply=True)
        self.assertEqual(Transaction.objects.filter(user=self.user).count(), 1)

    def test_audit_failure_rolls_back_archive_and_balance(self):
        with patch("whatsapp.models.AuditLog.objects.create", side_effect=RuntimeError("audit down")), \
             self.assertRaises(RuntimeError):
            self.run_archive(apply=True)
        self.assertFalse(RetiredFundingArchive.objects.exists())
        self.assertEqual(wallet_expected_balance(self.user.pk), Decimal("100"))
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("100"))

    def test_archive_database_guard(self):
        self.run_archive(apply=True)
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL immutability trigger")
        with self.assertRaises(IntegrityError), transaction.atomic():
            RetiredFundingArchive.objects.all().delete()
        with self.assertRaises(IntegrityError), transaction.atomic():
            RetiredFundingArchive.objects.update(operator_reference="rewrite")
