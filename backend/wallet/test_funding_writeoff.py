from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, IntegrityError, transaction
from django.test import TestCase

from wallet.funding_holds import funding_inventory, reconciliation_scope
from wallet.management.commands.settlement_report import Command as Settlement
from wallet.models import FundingHoldResolution, RetiredFundingArchive, Transaction, UnsettledFunding, Wallet
from wallet.partnership import retired_funding_destination
from wallet.services import wallet_expected_balance
from wallet.tests import make_user
from whatsapp.models import AuditLog


class FundingWriteOffTests(TestCase):
    def setUp(self):
        import hashlib
        self.user, _ = make_user("08044441234", "writeoff@example.test", balance="100")
        self.credit = Transaction.objects.get(user=self.user)
        self.hold = UnsettledFunding.objects.create(user=self.user, amount="100.00",
            destination_digest=hashlib.sha256(b"9081234567").hexdigest(),
            operator_reference="original-record",
            evidence={"credits": [{"transaction_id": self.credit.pk, "amount": "100.00"}]})
        call_command("archive_retired_balances", apply=True, expected_count=1,
                     expected_amount=Decimal("100"), operator_reference="archive-record", stdout=StringIO())

    def run_writeoff(self, **options):
        output = StringIO()
        kwargs = {"user_id": self.user.pk, "expected_amount": Decimal("100"),
                  "operator_reference": "OWNER-REQUEST-100", **options}
        call_command("write_off_retired_funding", stdout=output, **kwargs)
        return output.getvalue()

    def test_preview_does_not_cancel_a_customer_liability(self):
        before = Settlement()._owed()
        self.run_writeoff()
        self.assertFalse(FundingHoldResolution.objects.exists())
        self.assertEqual(Settlement()._owed(), before)

    def test_writeoff_changes_only_archived_liability_once_and_preserves_all_source_records(self):
        other, _ = make_user("08044441235", "other@example.test", balance="250")
        before = Settlement()._owed()
        ids = list(Transaction.objects.order_by("pk").values_list("pk", flat=True))
        with patch("utility.wema.transfer") as bank:
            first = self.run_writeoff(apply=True)
            second = self.run_writeoff(apply=True)
        bank.assert_not_called()
        self.assertIn('"bank_payment": false', first)
        self.assertIn('"bank_settlement_verified": false', first)
        self.assertIn('"already_resolved": true', second)
        self.assertEqual(FundingHoldResolution.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(action="retired_funding_written_off").count(), 1)
        after = Settlement()._owed()
        self.assertEqual(after["ledger_liability"], before["ledger_liability"] - Decimal("100"))
        self.assertEqual(after["stored_liability"], before["stored_liability"] - Decimal("100"))
        self.assertEqual(after["active_wallet_liability"], before["active_wallet_liability"])
        self.assertEqual(after["archived_liability"], Decimal("0"))
        self.assertEqual(wallet_expected_balance(self.user.pk), Decimal("0"))
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("0"))
        self.assertEqual(Wallet.objects.get(user=other).balance, Decimal("250"))
        self.assertEqual(list(Transaction.objects.order_by("pk").values_list("pk", flat=True)), ids)
        self.assertEqual(UnsettledFunding.objects.get(pk=self.hold.pk).amount, Decimal("100"))
        self.assertTrue(RetiredFundingArchive.objects.filter(funding=self.hold).exists())
        self.assertTrue(retired_funding_destination("9081234567"))
        inventory = funding_inventory()
        self.assertEqual(inventory["outstanding_holds"], 0)
        self.assertEqual(inventory["operator_write_offs"], 1)
        self.assertEqual(Decimal(inventory["operator_written_off_amount"]), Decimal("100"))
        self.assertFalse(reconciliation_scope()["legacy_funding_required"])

    def test_wrong_amount_or_customer_cannot_write_off_anything(self):
        for changes in ({"expected_amount": Decimal("99")}, {"user_id": self.user.pk + 1000}):
            with self.subTest(changes=changes), self.assertRaises(CommandError):
                self.run_writeoff(apply=True, **changes)
        self.assertFalse(FundingHoldResolution.objects.exists())

    def test_another_customers_outstanding_funding_stays_owed_and_blocks_release(self):
        other, _ = make_user("08044441235", "other@example.test", balance="250")
        UnsettledFunding.objects.create(user=other, amount="250", destination_digest="f" * 64,
                                        operator_reference="other-unresolved-funding")
        self.run_writeoff(apply=True)
        inventory = funding_inventory()
        self.assertEqual(inventory["outstanding_holds"], 1)
        self.assertEqual(Decimal(inventory["outstanding_amount"]), Decimal("250"))
        self.assertTrue(reconciliation_scope()["legacy_funding_required"])
        self.assertEqual(reconciliation_scope()["legacy_funding_accounts"], 1)

    def test_funding_that_was_never_archived_cannot_be_written_off(self):
        other, _ = make_user("08044441235", "other@example.test", balance="100")
        UnsettledFunding.objects.create(user=other, amount="100", destination_digest="f" * 64,
                                        operator_reference="unarchived-funding")
        with self.assertRaises(CommandError):
            self.run_writeoff(apply=True, user_id=other.pk)
        self.assertFalse(FundingHoldResolution.objects.exists())

    def test_active_or_mixed_funds_require_a_separate_review(self):
        for changes in ({"balance": Decimal("1")}, {"account_number": "0451234567"},
                        {"bank_accounts": [{"account_number": "0451234567"}]}):
            Wallet.objects.filter(user=self.user).update(balance=0, account_number="", bank_accounts=[])
            Wallet.objects.filter(user=self.user).update(**changes)
            with self.subTest(changes=changes), self.assertRaises(CommandError):
                self.run_writeoff(apply=True)
        self.assertFalse(FundingHoldResolution.objects.exists())

    def test_an_open_payment_prevents_writeoff(self):
        Transaction.objects.create(user=self.user, amount="1", direction=Transaction.IN,
            transaction_status=Transaction.PENDING, reference="not-finished", service="pending")
        with self.assertRaises(CommandError):
            self.run_writeoff(apply=True)

    def test_audit_failure_rolls_back_the_resolution(self):
        before = Settlement()._owed()
        with patch("whatsapp.models.AuditLog.objects.create", side_effect=RuntimeError("audit down")), \
                self.assertRaises(RuntimeError):
            self.run_writeoff(apply=True)
        self.assertFalse(FundingHoldResolution.objects.exists())
        self.assertEqual(Settlement()._owed(), before)

    def test_an_actual_bank_debit_is_not_an_internal_archive(self):
        debit = RetiredFundingArchive.objects.get(funding=self.hold).transaction
        debit.meta = {**debit.meta, "bank_payment": True}
        debit.save(update_fields=["meta"])
        with self.assertRaises(CommandError):
            self.run_writeoff(apply=True)
        self.assertFalse(FundingHoldResolution.objects.exists())

    def test_resolution_cannot_be_rewritten_or_deleted(self):
        self.run_writeoff(apply=True)
        row = FundingHoldResolution.objects.get()
        with self.assertRaises(ValueError):
            row.delete()
        with self.assertRaises(ValueError):
            row.save()
        if connection.vendor == "postgresql":
            with self.assertRaises(IntegrityError), transaction.atomic():
                FundingHoldResolution.objects.all().delete()
            with self.assertRaises(IntegrityError), transaction.atomic():
                FundingHoldResolution.objects.update(operator_reference="rewritten")
