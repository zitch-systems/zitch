"""The bank's transfer fee is debited with the transfer and follows it."""
import json
from decimal import Decimal
from unittest.mock import patch

from django.test import Client, TestCase, override_settings

from transfers.models import Bank
from transfers.services import PayoutError, execute_payout
from wallet.alerts import _detail_lines
from wallet.models import Transaction, TransactionAlertDelivery
from wallet.services import (
    get_or_create_wallet, is_bank_payout, pending_bank_payouts, reverse_transfer,
    settle_payout, wallet_expected_balance,
)
from wallet.tests import make_user
from wallet.transfer_fees import (
    FEE_FOR_KEY, fee_reference, reconcile_transfer_fees, settle_transfer_fee, transfer_fee,
)

SENT = {"success": True, "status": "SUCCESS"}
QUEUED = {"success": True, "status": "PENDING"}
REFUSED = {"success": False, "message": "bank declined"}


class TransferFeeQuoteTests(TestCase):
    """₦10/₦25/₦50 NIP charge by band, plus 7.5% VAT; nothing to another Wema account."""

    def test_the_published_tariff_applies_when_the_schedule_cannot_be_read(self):
        with patch("utility.providers.payout_charge", return_value=None):
            self.assertEqual(transfer_fee(56, "058", "GTBank"), Decimal("10.75"))
            self.assertEqual(transfer_fee(3900, "058"), Decimal("10.75"))
            self.assertEqual(transfer_fee(5000, "058"), Decimal("10.75"))
            self.assertEqual(transfer_fee(5001, "058"), Decimal("26.88"))
            self.assertEqual(transfer_fee(50000, "058"), Decimal("26.88"))
            self.assertEqual(transfer_fee(50001, "058"), Decimal("53.75"))

    def test_the_banks_own_schedule_wins_when_it_can_be_read(self):
        with patch("utility.providers.payout_charge", return_value=Decimal("12.00")):
            self.assertEqual(transfer_fee(1000, "058"), Decimal("12.00"))

    def test_a_schedule_that_already_includes_vat_is_not_taxed_again(self):
        # What the bank debits, and what its schedule quotes: charging ₦11.56 for
        # a ₦10.75 fee left the ledger ₦0.81 below the bank on every transfer.
        for amount, quoted in ((1000, "10.75"), (20000, "26.88"), (80000, "53.75")):
            with patch("utility.providers.payout_charge", return_value=Decimal(quoted)):
                self.assertEqual(transfer_fee(amount, "058"), Decimal(quoted))

    def test_a_schedule_listing_the_bare_tariff_still_gets_vat(self):
        for amount, bare, charged in ((1000, "10", "10.75"), (20000, "25.00", "26.88"),
                                      (80000, "50", "53.75")):
            with patch("utility.providers.payout_charge", return_value=Decimal(bare)):
                self.assertEqual(transfer_fee(amount, "058"), Decimal(charged))

    def test_a_transfer_to_another_wema_account_is_free(self):
        with patch("utility.providers.payout_charge", return_value=Decimal("10.00")):
            self.assertEqual(transfer_fee(1000, "035"), Decimal("0.00"))
            self.assertEqual(transfer_fee(1000, "000017"), Decimal("0.00"))
            self.assertEqual(transfer_fee(1000, "999", "Wema Bank"), Decimal("0.00"))
            self.assertEqual(transfer_fee(1000, "999", "ALAT by WEMA"), Decimal("0.00"))

    @override_settings(TRANSFER_FEE_ENABLED=False)
    def test_the_fee_can_be_switched_off(self):
        self.assertEqual(transfer_fee(1000, "058"), Decimal("0.00"))


@override_settings(PAYMENT_PROVIDER="wema")
class TransferFeeLedgerTests(TestCase):
    def setUp(self):
        self.user, self.token = make_user("08071240001", "fees@zitch.test", balance="10000")
        self.bank = Bank.objects.create(code="gtb", name="GTBank", bank_code="058")

    def _send(self, amount="3900", result=SENT, key="fee-1", fee=None):
        with patch("transfers.services.payout_send", return_value=result):
            return execute_payout(self.user, Decimal(amount), "0123456789", self.bank,
                                  "JOHN DOE", idempotency_key=key, fee=fee)

    def _fee_row(self, payout):
        return Transaction.objects.get(reference=fee_reference(payout.reference))

    def _balance(self):
        return get_or_create_wallet(self.user).balance

    def _assert_ledger_agrees(self):
        self.assertEqual(wallet_expected_balance(self.user.pk), self._balance())

    def test_a_sent_transfer_debits_its_fee_as_a_separate_silent_row(self):
        payout = self._send()

        fee = self._fee_row(payout)
        self.assertEqual(payout.transaction_status, Transaction.SUCCESS)
        self.assertEqual(fee.transaction_status, Transaction.SUCCESS)
        self.assertEqual(fee.amount, Decimal("10.75"))
        self.assertEqual(fee.service, "Transfer fee")
        self.assertEqual(fee.meta[FEE_FOR_KEY], payout.reference)
        self.assertEqual(payout.meta["transfer_fee"], "10.75")
        self.assertEqual(self._balance(), Decimal("6089.25"))  # 10,000 - 3,900 - 10.75
        self._assert_ledger_agrees()
        # Never mistaken for a bank payout, and never a second alert.
        self.assertFalse(is_bank_payout(fee))
        self.assertFalse(TransactionAlertDelivery.objects.filter(transaction=fee).exists())
        self.assertIn("Bank fee: ₦10.75", _detail_lines(payout))

    def test_a_refused_transfer_returns_its_fee_too(self):
        with self.assertRaises(PayoutError):
            self._send(result=REFUSED)

        payout = Transaction.objects.get(user=self.user, service__startswith="Transfer to")
        self.assertEqual(payout.transaction_status, Transaction.FAILED)
        self.assertEqual(self._fee_row(payout).transaction_status, Transaction.FAILED)
        self.assertEqual(self._balance(), Decimal("10000.00"))
        self._assert_ledger_agrees()

    def test_a_queued_transfers_fee_waits_then_settles_with_it(self):
        payout = self._send(result=QUEUED)
        self.assertEqual(self._fee_row(payout).transaction_status, Transaction.PENDING)
        self.assertEqual(list(pending_bank_payouts(payout.created)), [payout])  # fee row is not swept

        settle_payout(payout.reference)
        self.assertEqual(self._fee_row(payout).transaction_status, Transaction.SUCCESS)
        self.assertEqual(self._balance(), Decimal("6089.25"))
        self._assert_ledger_agrees()

    def test_a_transfer_the_bank_later_reverses_returns_its_fee(self):
        payout = self._send(result=QUEUED)

        reverse_transfer(payout.reference)

        self.assertEqual(self._fee_row(payout).transaction_status, Transaction.FAILED)
        self.assertEqual(self._balance(), Decimal("10000.00"))
        self._assert_ledger_agrees()

    def test_a_balance_that_covers_the_transfer_but_not_the_fee_refuses_both(self):
        with self.assertRaises(PayoutError) as refused:
            self._send(amount="10000")

        self.assertEqual(refused.exception.kind, "insufficient")
        self.assertIn("₦26.88 bank fee", refused.exception.message)  # ₦10,000 is the ₦25 band
        self.assertFalse(Transaction.objects.filter(user=self.user).exclude(
            direction=Transaction.IN).exists())
        self.assertEqual(self._balance(), Decimal("10000.00"))

    def test_a_retried_request_never_charges_the_fee_twice(self):
        self._send(key="same-key")
        with self.assertRaises(PayoutError) as again:
            self._send(key="same-key")

        self.assertEqual(again.exception.kind, "duplicate")
        self.assertEqual(Transaction.objects.filter(meta__has_key=FEE_FOR_KEY).count(), 1)
        self.assertEqual(self._balance(), Decimal("6089.25"))

    def test_the_fee_the_customer_confirmed_is_the_fee_charged(self):
        payout = self._send(fee=Decimal("10.75"), amount="6000")  # quote would say 26.88
        self.assertEqual(self._fee_row(payout).amount, Decimal("10.75"))

    def test_a_transfer_to_another_wema_account_writes_no_fee_row(self):
        wema = Bank.objects.create(code="wema", name="Wema Bank", bank_code="035")
        with patch("transfers.services.payout_send", return_value=SENT):
            payout = execute_payout(self.user, Decimal("3900"), "0123456789", wema,
                                    "JOHN DOE", idempotency_key="on-us")
        self.assertNotIn("transfer_fee", payout.meta)
        self.assertFalse(Transaction.objects.filter(meta__has_key=FEE_FOR_KEY).exists())
        self.assertEqual(self._balance(), Decimal("6100.00"))

    def test_a_transfer_resolved_by_another_route_has_its_fee_settled_by_the_sweep(self):
        refunded = self._send(result=QUEUED, key="swept-1")
        sent = self._send(result=QUEUED, key="swept-2", amount="100")
        # As operator tooling might: change the transfers without the fee step.
        wallet = get_or_create_wallet(self.user)
        wallet.balance += refunded.amount
        wallet.save(update_fields=["balance"])
        Transaction.objects.filter(pk=refunded.pk).update(transaction_status=Transaction.FAILED)
        Transaction.objects.filter(pk=sent.pk).update(transaction_status=Transaction.SUCCESS)

        self.assertEqual(reconcile_transfer_fees(), {"confirmed": 1, "released": 1})
        self.assertEqual(self._fee_row(refunded).transaction_status, Transaction.FAILED)
        self.assertEqual(self._fee_row(sent).transaction_status, Transaction.SUCCESS)
        self._assert_ledger_agrees()
        self.assertEqual(reconcile_transfer_fees(), {"confirmed": 0, "released": 0})

    def test_a_transfer_held_for_review_keeps_its_fee_until_the_review_ends(self):
        payout = self._send(result=QUEUED)
        Transaction.objects.filter(pk=payout.pk).update(
            meta={**payout.meta, "wema_reversal_quarantine": {"active": True}},
            transaction_status=Transaction.FAILED)

        self.assertEqual(settle_transfer_fee(payout), "held")
        self.assertEqual(self._fee_row(payout).transaction_status, Transaction.PENDING)


@override_settings(PAYMENT_PROVIDER="wema")
class AppTransferFeeTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08071240002", "appfee@zitch.test", balance="10000")
        Bank.objects.create(code="gtb", name="GTBank", bank_code="058")

    def test_the_app_send_reports_the_fee_it_charged(self):
        with patch("transfers.services.payout_send", return_value=SENT):
            body = self.client.post("/api/transfers/send/", data=json.dumps({
                "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
                "name": "John Doe", "amount": "3900", "transaction_pin": "1234",
                "idempotency_key": "app-fee-1",
            }), content_type="application/json").json()
        self.assertTrue(body["success"])
        self.assertEqual(body["fee"], "10.75")
        self.assertEqual(Decimal(body["wallet"]), Decimal("6089.25"))

    def test_the_app_is_told_when_the_fee_is_what_it_cannot_cover(self):
        with patch("transfers.services.payout_send", return_value=SENT):
            res = self.client.post("/api/transfers/send/", data=json.dumps({
                "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
                "name": "John Doe", "amount": "10000", "transaction_pin": "1234",
                "idempotency_key": "app-fee-2",
            }), content_type="application/json")
        self.assertEqual(res.status_code, 402)
        self.assertIn("bank fee", res.json()["message"])


class FeeCorrectionCommandTests(TestCase):
    """The one-off correction for fees the bank took before Zitch debited them."""

    def setUp(self):
        self.user, _ = make_user("08071240003", "feefix@zitch.test", balance="344.00")

    def _run(self, *extra):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("post_transfer_fee_correction", "--user-id", str(self.user.pk),
                     "--amount", "21.50", "--operator-reference",
                     "two transfers on 13 Nov per the bank statement", *extra, stdout=out)
        return json.loads(out.getvalue())

    def _balance(self):
        return get_or_create_wallet(self.user).balance

    def test_a_dry_run_changes_nothing(self):
        result = self._run()
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["balance_after"], "322.50")
        self.assertEqual(self._balance(), Decimal("344.00"))

    def test_applying_brings_the_ledger_to_the_bank_balance_once(self):
        from whatsapp.models import AuditLog

        result = self._run("--apply")
        self.assertTrue(result["posted"])
        self.assertTrue(result["ledger_agrees"])
        self.assertEqual(self._balance(), Decimal("322.50"))
        row = Transaction.objects.get(reference=result["reference"])
        self.assertEqual((row.transaction_status, row.service), (Transaction.SUCCESS, "Bank transfer fees"))
        self.assertTrue(AuditLog.objects.filter(action="wallet.transfer_fee_correction").exists())

        again = self._run("--apply")
        self.assertTrue(again["already_posted"])
        self.assertEqual(self._balance(), Decimal("322.50"))

    def test_a_correction_larger_than_the_balance_changes_nothing(self):
        from django.core.management import CommandError, call_command

        with self.assertRaises(CommandError):
            call_command("post_transfer_fee_correction", "--user-id", str(self.user.pk),
                         "--amount", "500.00", "--operator-reference", "too much", "--apply")
        self.assertEqual(self._balance(), Decimal("344.00"))
