"""Refunding the fees charged with VAT twice (refund_fee_overcharge).

On 13 November 2026 two transfers were charged ₦11.56 for the bank's ₦10.75
fee, and the balance check has reported the bank ₦1.62 above the ledger ever
since. The refund brings the ledger to the bank exactly once, and is taken
back if the transfer is later reversed and its whole fee returned.
"""
import io
import json
from decimal import Decimal
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings

from transfers.models import Bank
from transfers.services import execute_payout
from wallet.models import Transaction
from wallet.services import get_or_create_wallet, reverse_transfer, wallet_expected_balance
from wallet.tests import make_user
from wallet.transfer_fees import (
    REFUND_SERVICE, REFUND_TAKEN_BACK_SERVICE, fee_reference, overcharge_refund_reference,
)

SENT = {"success": True, "status": "SUCCESS"}
LATER = "2100-01-01"   # the rows below are made today, after the default cut-off


@override_settings(PAYMENT_PROVIDER="wema")
class FeeOverchargeRefundTests(TestCase):
    def setUp(self):
        self.user, _token = make_user("08071240009", "vat@zitch.test", balance="10000")
        self.bank = Bank.objects.create(code="gtb", name="GTBank", bank_code="058")

    def send(self, fee, key, amount="1000"):
        with patch("transfers.services.payout_send", return_value=SENT):
            return execute_payout(self.user, Decimal(amount), "0123456789", self.bank,
                                  "JOHN DOE", idempotency_key=key, fee=Decimal(fee))

    def run_refund(self, *args):
        out = io.StringIO()
        call_command("refund_fee_overcharge", *args, stdout=out)
        return json.loads(out.getvalue())

    def balance(self):
        return get_or_create_wallet(self.user).balance

    def assert_ledger_agrees(self):
        self.assertEqual(wallet_expected_balance(self.user.pk), self.balance())

    def test_a_dry_run_lists_the_overcharges_and_changes_nothing(self):
        self.send("11.56", "a")
        self.send("10.75", "b")            # charged correctly: not listed
        before = self.balance()
        report = self.run_refund("--before", LATER)
        self.assertTrue(report["summary"]["dry_run"])
        self.assertEqual([row["refund"] for row in report["fees"]], ["0.81"])
        self.assertEqual(self.balance(), before)

    def test_applying_refunds_each_overcharge_once(self):
        first, second = self.send("11.56", "a"), self.send("11.56", "b")
        before = self.balance()
        report = self.run_refund("--before", LATER, "--apply")
        self.assertEqual(report["summary"]["to_refund"], "1.62")
        self.assertEqual(self.balance(), before + Decimal("1.62"))
        self.assertEqual(report["summary"]["ledger_agrees"], {str(self.user.pk): True})
        for payout in (first, second):
            refund = Transaction.objects.get(
                reference=overcharge_refund_reference(fee_reference(payout.reference)))
            self.assertEqual((refund.service, refund.amount), (REFUND_SERVICE, Decimal("0.81")))
            self.assertTrue(refund.meta["suppress_transaction_alert"])
        again = self.run_refund("--before", LATER, "--apply")
        self.assertEqual(again["summary"]["to_refund"], "0")
        self.assertTrue(all(row["already_refunded"] for row in again["fees"]))
        self.assertEqual(self.balance(), before + Decimal("1.62"))
        self.assert_ledger_agrees()

    def test_each_band_refunds_its_own_difference(self):
        self.user, _token = make_user("08071240010", "bands@zitch.test", balance="200000")
        self.send("28.90", "b", amount="20000")
        self.send("57.78", "c", amount="20000")   # the command reads the fee, not the amount
        report = self.run_refund("--before", LATER)
        self.assertEqual(sorted(row["refund"] for row in report["fees"]), ["2.02", "4.03"])

    def test_fees_after_the_fix_are_left_alone_by_default(self):
        self.send("11.56", "a")            # made today, after 14 November 2026
        self.assertEqual(self.run_refund()["fees"], [])

    def test_a_transfer_reversed_after_its_refund_returns_the_fee_once(self):
        payout = self.send("11.56", "a")
        self.run_refund("--before", LATER, "--apply")
        reverse_transfer(payout.reference)
        self.assertEqual(self.balance(), Decimal("10000.00"))
        taken_back = Transaction.objects.get(
            reference=overcharge_refund_reference(fee_reference(payout.reference)) + "-REV")
        self.assertEqual((taken_back.service, taken_back.amount),
                         (REFUND_TAKEN_BACK_SERVICE, Decimal("0.81")))
        self.assertEqual(taken_back.transaction_status, Transaction.SUCCESS)
        self.assert_ledger_agrees()

    def test_a_reversal_without_a_refund_is_unchanged(self):
        payout = self.send("10.75", "a")
        reverse_transfer(payout.reference)
        self.assertEqual(self.balance(), Decimal("10000.00"))
        self.assertFalse(Transaction.objects.filter(service=REFUND_TAKEN_BACK_SERVICE).exists())
        self.assert_ledger_agrees()

    def test_only_the_named_customer(self):
        self.send("11.56", "a")
        self.assertEqual(self.run_refund("--before", LATER, "--user-id", str(self.user.pk + 99))["fees"], [])
