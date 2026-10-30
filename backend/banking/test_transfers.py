from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase, override_settings
from transfers.models import Bank, Beneficiary
from transfers.services import execute_payout, PayoutError
from .client import Reply
from .models import Instruction
from .test_client import CONFIG
from .test_money import MoneyTests
from .test_fixtures import make_bank_user


@override_settings(NINEPSB={**CONFIG, "TRANSFER_FEE_TOTAL": "25.00"})
class TransferTests(TestCase):
    def setUp(self):
        MoneyTests.setUp(self)
        self.destination = Bank.objects.create(code="confirmed-bank", name="Confirmed Bank", bank_code="120001")
        self.enterContext(patch("banking.payments.update_account", return_value=self.bank))

    def pay(self, fee):
        return execute_payout(self.user, Decimal("100"), "1100000002", self.destination,
            "John Doe", idempotency_key="payout-once", expected_fee=fee)

    def test_fee_must_match_confirmed_total_before_debit(self):
        for fee in (None, "0", "24.99"):
            with self.subTest(fee=fee), patch("banking.payments._invoke") as send, self.assertRaises(PayoutError):
                self.pay(fee)
            send.assert_not_called()
        self.assertFalse(Instruction.objects.exists())
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 500)

    def test_principal_and_fee_reserve_together_but_bank_receives_principal(self):
        with patch("banking.payments._invoke", return_value=Reply({"status": "SUCCESS", "responseCode": "00"}, 200)) as send:
            txn = self.pay("25")
        self.assertEqual(txn.amount, 125)
        self.assertEqual(txn.meta["principal"], "100")
        self.assertEqual(send.call_args.args[1]["amount"], "100.00")
        self.assertEqual(send.call_args.args[1]["fee"], "25.00")
        self.assertEqual(Beneficiary.objects.get().bank_code, self.destination.code)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 375)

    def test_explicit_bank_rejection_refunds_the_entire_reservation_once(self):
        with patch("banking.payments._invoke", return_value=Reply({"status": "FAILED", "responseCode": "51"}, 200)) as send:
            with self.assertRaises(PayoutError):
                self.pay("25")
            with self.assertRaises(PayoutError):
                self.pay("25")
        self.assertEqual(send.call_count, 1)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 500)

    def test_directory_preserves_saved_slug_but_replaces_provider_routing_code(self):
        from django.core.management import call_command
        bank = Bank.objects.create(code="nine-payment", name="9 Payment Service Bank", bank_code="OLD")
        with patch("banking.management.commands.sync_bank_directory.get_banks", return_value={"success": True,
            "banks": [{"bank_name": "9PAYMENT SERVICE BANK", "bank_code": "120001"}]}):
            call_command("sync_bank_directory", verbosity=0)
        bank.refresh_from_db()
        self.assertEqual(bank.code, "nine-payment")
        self.assertEqual(bank.bank_code, "120001")
        self.assertTrue(bank.active)
        self.destination.refresh_from_db()
        self.assertFalse(self.destination.active)

    def test_peer_transfer_uses_confirmed_directory_alias_not_an_old_slug(self):
        from wallet.services import transfer
        bank = Bank.objects.create(code="nine-payment", name="9 Payment Service Bank", bank_code="120001")
        recipient, _ = make_bank_user("08010009999", "bank-recipient@zitch.test")
        with patch("banking.adapter.resolve_account", return_value={"success": True, "name": recipient.get_full_name()}), \
             patch("transfers.services.execute_payout", return_value="outgoing") as pay:
            result = transfer(self.user, recipient, 100, expected_fee="25")
        self.assertEqual(result, ("outgoing", None))
        self.assertEqual(pay.call_args.args[3].pk, bank.pk)

    def test_ambiguous_directory_cannot_route_peer_money(self):
        from wallet.services import transfer
        Bank.objects.create(code="nine-payment", name="9 Payment Service Bank", bank_code="120001")
        Bank.objects.create(code="another-nine", name="9PSB", bank_code="120002")
        recipient, _ = make_bank_user("08010009999", "bank-recipient@zitch.test")
        with patch("banking.adapter.resolve_account") as enquiry, self.assertRaises(PayoutError):
            transfer(self.user, recipient, 100, expected_fee="25")
        enquiry.assert_not_called()
