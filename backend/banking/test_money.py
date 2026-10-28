from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User, record_identity_proof
from wallet.models import Wallet, Transaction
from wallet.services import debit, InsufficientFunds, wallet_expected_balance

from .accounts import spendable, update_account
from .client import ProviderError, Reply
from .models import BankAccount, Instruction, Notification, PreviousBankArchive
from .notifications import ingest, process
from .payments import bind_instruction, dispatch, requery
from .test_client import CONFIG


@override_settings(NINEPSB=CONFIG)
class MoneyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="nine", first_name="Jane", last_name="Doe", phone="07041267880",
            email="jane@example.test", email_verified=True, phone_verified=True, tier=1, bvn_verified=True)
        self.wallet = Wallet.objects.create(user=self.user, balance=500, account_number="1100000001", account_name="Jane Doe", bank_name="9PSB")
        Transaction.objects.create(user=self.user, reference="INITIAL", amount=500, direction="in", transaction_status="Successful", service="Opening test balance")
        self.bank = BankAccount.objects.create(wallet=self.wallet, number="1100000001", name="Jane Doe",
            environment="production", tier=1, pnd=False, frozen=False, status="active", checked_at=timezone.now(),
            available_balance=500, ledger_balance=500, opening_review_required=False)
        record_identity_proof(self.user, "bvn", "12345678901", source="9psb_otp")
        self.user.recompute_tier()
        self.user.save(update_fields=["tier"])

    def instruction(self):
        txn = debit(self.user, Decimal("100"), "Transfer to John Doe", meta={"bank": "9PSB", "reconcile": True}, idempotency_key="payment-1")
        return bind_instruction(txn, "waas", "OTHER_BANKS", {"amount": "100.00", "source": self.bank.number,
            "destination": "1100000002", "destination_name": "John Doe", "bank_code": "120001", "narration": "Test"})

    def test_old_balance_is_not_spendable_on_new_bank(self):
        PreviousBankArchive.objects.create(wallet=self.wallet, balance=450, account_number="old", review_required=True)
        self.assertEqual(spendable(self.wallet), Decimal("50"))
        with self.assertRaises(InsufficientFunds):
            debit(self.user, Decimal("100"), "Transfer")
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("500"))

    def test_restricted_new_funds_are_not_labelled_historical(self):
        from wallet.services import wallet_balance_payload
        PreviousBankArchive.objects.create(wallet=self.wallet, balance=200, account_number="old")
        self.bank.pnd = True
        self.bank.save()
        result = wallet_balance_payload(self.user, self.wallet)
        self.assertEqual(result["available_balance"], Decimal("0"))
        self.assertEqual(result["historical_balance"], Decimal("200"))
        self.assertEqual(result["restricted_balance"], Decimal("300"))

    def test_bank_available_balance_caps_local_spending(self):
        from wallet.services import wallet_balance_payload
        self.bank.available_balance = 20
        self.bank.save()
        self.assertEqual(wallet_balance_payload(self.user, self.wallet)["available_balance"], Decimal("20"))
        with self.assertRaises(InsufficientFunds):
            debit(self.user, Decimal("25"), "Transfer")

    def test_contradictory_account_readback_keeps_prior_checked_time(self):
        before = self.bank.checked_at
        data = {"number": self.bank.number, "name": self.bank.name, "status": "Active", "isSuccessful": False}
        with self.assertRaises(ProviderError):
            update_account(self.bank, Reply({"status": "SUCCESS", "data": data}, 200))
        self.bank.refresh_from_db()
        self.assertEqual(before, self.bank.checked_at)

    def test_historical_refund_never_becomes_new_bank_money(self):
        from wallet.services import refund
        PreviousBankArchive.objects.create(wallet=self.wallet, balance=500, account_number="old")
        old = Transaction.objects.create(user=self.user, reference="OLD-OUT", direction="out",
            transaction_status=Transaction.PENDING, amount=100, service="Old transfer", meta={"provider": "previous"})
        self.assertTrue(refund(old))
        self.assertFalse(refund(old))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("600"))
        self.assertEqual(spendable(self.wallet), Decimal("0"))

    def test_unknown_post_is_not_repeated_or_refunded(self):
        instruction = self.instruction()
        with patch("banking.payments._invoke", side_effect=ProviderError("network_unavailable", uncertain=True)) as send:
            self.assertTrue(dispatch(instruction)["pending"])
            self.assertTrue(dispatch(instruction)["pending"])
            self.assertEqual(send.call_count, 1)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("400"))
        self.assertEqual(wallet_expected_balance(self.user.pk), self.wallet.balance)

    def test_explicit_failure_refunds_once(self):
        instruction = self.instruction()
        with patch("banking.payments._invoke", return_value=Reply({"status": "FAILED", "responseCode": "51"}, 200)) as send:
            self.assertFalse(dispatch(instruction)["success"])
            dispatch(instruction)
            self.assertEqual(send.call_count, 1)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("500"))
        self.assertEqual(wallet_expected_balance(self.user.pk), self.wallet.balance)

    def test_auth_failure_before_send_refunds(self):
        instruction = self.instruction()
        with patch("banking.payments._invoke", side_effect=ProviderError("authentication_failed")):
            result = dispatch(instruction)
        self.assertFalse(result["pending"])
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("500"))

    def test_successful_query_does_not_settle_pending_payment(self):
        instruction = self.instruction()
        Instruction.objects.filter(pk=instruction.pk).update(state="pending")
        instruction.refresh_from_db()
        with patch("banking.client.Client.transaction_status", return_value=Reply({"status": "SUCCESS", "data": {}}, 200)):
            self.assertTrue(requery(instruction)["pending"])
        instruction.transaction.refresh_from_db()
        self.assertEqual(instruction.transaction.transaction_status, Transaction.PENDING)

    def test_query_with_another_reference_cannot_settle(self):
        instruction = self.instruction()
        Instruction.objects.filter(pk=instruction.pk).update(state="pending")
        instruction.refresh_from_db()
        response = Reply({"status": "SUCCESS", "data": {"transactionStatus": "SUCCESS", "transactionId": "OTHER"}}, 200)
        with patch("banking.client.Client.transaction_status", return_value=response):
            self.assertTrue(requery(instruction)["pending"])
        instruction.refresh_from_db()
        self.assertEqual(instruction.last_error, "status_reference_mismatch")

    def payload(self, amount="50.00", ref="SESSION-1"):
        return {"code": "00", "message": "SUCCESS", "accountnumber": self.bank.number,
                "amount": amount, "nipsessionid": ref, "narration": "Deposit"}

    def test_verified_duplicate_inflow_credits_only_once(self):
        row = ingest("transfer", self.payload())
        duplicate = ingest("transfer", self.payload())
        self.assertEqual(row.pk, duplicate.pk)
        with patch("banking.client.Client.notification", return_value=Reply({"status": "SUCCESS", "data": self.payload()}, 200)) as query:
            self.assertTrue(process(row))
            duplicate.refresh_from_db()
            self.assertTrue(process(duplicate))
            self.assertEqual(query.call_count, 1)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("550"))
        self.assertEqual(wallet_expected_balance(self.user.pk), self.wallet.balance)

    def test_inflow_amount_mismatch_cannot_credit(self):
        row = ingest("transfer", self.payload())
        with patch("banking.client.Client.notification", return_value=Reply({"status": "SUCCESS", "data": self.payload("5000")}, 200)):
            self.assertFalse(process(row))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("500"))

    def test_failed_bank_read_cannot_credit_authenticated_callback(self):
        row = ingest("transfer", self.payload())
        with patch("banking.client.Client.notification", side_effect=ProviderError("network_unavailable")):
            self.assertFalse(process(row))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("500"))

    def test_reversal_is_not_an_ordinary_deposit(self):
        payload = {**self.payload(), "narration": "RVSL-original-transfer"}
        row = ingest("transfer", payload)
        self.assertEqual(row.event, "reversal")
        with patch("banking.client.Client.notification", return_value=Reply({"status": "SUCCESS", "data": payload}, 200)):
            self.assertFalse(process(row))
        self.wallet.refresh_from_db()
        row.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("500"))
        self.assertEqual(row.state, "review")

    def test_restriction_readback_uses_observed_bank_values(self):
        payload = {"number": self.bank.number, "name": self.bank.name, "status": "Active", "tier": "1",
            "pndstatus": "InActive", "freezeStatus": "Active", "availableBalance": 20, "ledgerBalance": 20}
        update_account(self.bank, Reply({"status": "SUCCESS", "data": payload}, 200))
        self.assertFalse(self.bank.pnd)
        self.assertTrue(self.bank.frozen)
        self.assertIsNone(self.bank.maximum_deposit)
