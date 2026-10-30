"""Customer NUBAN transfers must move bank cash before recipient wallet credit."""
import json
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings

from transfers.models import Bank
from wallet.models import BankHistoryCheckpoint, Transaction, Wallet
from wallet.services import apply_wema_credit, settle_payout
from banking.test_fixtures import make_bank_user as make_user, mock_bank_io
from banking.test_client import CONFIG
from banking.models import BankAccount
from banking.client import Reply


@override_settings(NINEPSB={**CONFIG, "TRANSFER_FEE_TOTAL": "0"}, DEBUG=False, TESTING=False, WEMA={
    "CHANNEL_ID": "test-channel", "KEYS": {"wallet": "test-key"},
    "SIMULATION": False,
})
class PartnerBankP2PTests(TestCase):
    def setUp(self):
        self.sender, self.token = make_user("08010002001", "sender-p2p@zitch.test", balance="5000")
        self.recipient, _ = make_user("08010002002", "recipient-p2p@zitch.test")
        mock_bank_io(self)
        self.sender_account = BankAccount.objects.get(wallet__user=self.sender)
        self.recipient_account = BankAccount.objects.get(wallet__user=self.recipient)
        self.bank = Bank.objects.create(code="9psb", name="9 Payment Service Bank", bank_code="120001")
        self.payload = {
            "identifier": self.recipient.phone, "amount": "1000",
            "transaction_pin": "1234", "idempotency_key": "partner-p2p-1", "expected_fee": "0.00",
        }

    def post(self, **changes):
        return self.client.post(
            "/api/transfer/send/", json.dumps({**self.payload, **changes}),
            content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

    def balance(self, user):
        return Wallet.objects.get(user=user).balance

    def _send(self, result, **changes):
        with patch("banking.adapter.resolve_account", return_value={"success": True, "name": "ADA EZE"}), \
                patch("banking.client.Client.transfer", return_value=Reply({"status": result.get("status", "PENDING"), "responseCode": "00" if result.get("success") else "09"}, 200)) as provider:
            response = self.post(**changes)
        return response, provider

    def test_success_moves_sender_bank_cash_without_minting_recipient_local_cash(self):
        response, provider = self._send({"success": True, "status": "SUCCESS"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        self.assertEqual(self.balance(self.sender), Decimal("4000"))
        self.assertEqual(self.balance(self.recipient), Decimal("0"))
        self.assertFalse(Transaction.objects.filter(user=self.recipient).exists())
        call = provider.call_args
        self.assertEqual(call.args[3:6], (self.recipient_account.number, "120001", "ADA EZE"))
        self.assertEqual(call.args[2], self.sender_account.number)
        outgoing = Transaction.objects.get(reference=response.json()["reference"])
        self.assertEqual(outgoing.transaction_status, Transaction.SUCCESS)
        # Authenticated notification/history owns this inbound posting. The
        # transfer response itself never posts it and repeated history is deduped.
        from banking.notifications import ingest, process
        history = {"code": "00", "message": "SUCCESS", "accountnumber": self.recipient_account.number,
                   "amount": "1000", "nipsessionid": outgoing.reference, "narration": "Deposit"}
        row = ingest("transfer", history)
        with patch("banking.client.Client.notification", return_value=Reply({"status": "SUCCESS", "data": history}, 200)) as query:
            self.assertTrue(process(row))
            self.assertTrue(process(ingest("transfer", history)))
        query.assert_called_once()
        self.assertEqual(self.balance(self.recipient), Decimal("1000"))
        self.assertEqual(Transaction.objects.filter(user=self.recipient).count(), 1)

    def test_ambiguous_result_holds_sender_and_replays_without_resending(self):
        with patch("banking.adapter.resolve_account", return_value={"success": True, "name": "ADA EZE"}) as enquiry, \
                patch("banking.client.Client.transfer", return_value=Reply({"status": "PENDING", "responseCode": "09"}, 200)) as provider:
            first = self.post()
            Wallet.objects.filter(user=self.recipient).update(account_number="")
            replay = self.post(transaction_pin="wrong")
        self.assertTrue(first.json()["pending"])
        self.assertNotIn("success", first.json())
        self.assertTrue(replay.json()["pending"])
        self.assertTrue(replay.json()["duplicate"])
        self.assertEqual(first.json()["reference"], replay.json()["reference"])
        provider.assert_called_once()
        enquiry.assert_called_once()
        self.assertEqual(self.balance(self.sender), Decimal("4000"))
        self.assertEqual(self.balance(self.recipient), Decimal("0"))
        settle_payout(first.json()["reference"])
        self.assertEqual(self.balance(self.recipient), Decimal("0"))

    def test_definitive_failure_refunds_only_sender_and_retry_cannot_resend(self):
        with patch("banking.adapter.resolve_account", return_value={"success": True, "name": "ADA EZE"}), \
                patch("banking.client.Client.transfer", return_value=Reply({"status": "FAILED", "responseCode": "51", "message": "Transfer rejected"}, 200)) as provider:
            first = self.post()
            replay = self.post()
        self.assertEqual(first.status_code, 422)
        self.assertTrue(first.json()["refunded"])
        self.assertFalse(replay.json().get("success", False))
        provider.assert_called_once()
        self.assertEqual(self.balance(self.sender), Decimal("5000"))
        self.assertEqual(self.balance(self.recipient), Decimal("0"))

    def test_unavailable_recipient_never_debits(self):
        for condition in ("review", "unverified", "inactive"):
            with self.subTest(condition=condition):
                BankAccount.objects.filter(wallet__user=self.recipient).update(status="review" if condition == "review" else "active")
                type(self.recipient).objects.filter(pk=self.recipient.pk).update(phone_verified=condition != "unverified", is_active=condition != "inactive")
                response, provider = self._send({"success": True, "status": "SUCCESS"})
                self.assertEqual(response.status_code, 422)
                self.assertTrue(response.json()["not_charged"])
                provider.assert_not_called()
                self.assertEqual(self.balance(self.sender), Decimal("5000"))
        self.assertFalse(Transaction.objects.filter(user=self.sender, direction=Transaction.OUT).exists())

    def test_nin_only_sender_and_recipient_use_their_confirmed_bank_accounts(self):
        type(self.sender).objects.filter(pk__in=(self.sender.pk, self.recipient.pk)).update(
            bvn_verified=False, nin_verified=True)
        from accounts.models import IdentityProof, record_identity_proof
        for user in (self.sender, self.recipient):
            IdentityProof.objects.filter(user=user, identity_type="bvn").delete()
            record_identity_proof(user, "nin", str(20000000000 + user.pk), source="9psb_otp")
        response, provider = self._send({"success": True, "status": "SUCCESS"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["success"])
        self.assertEqual(provider.call_args.args[2], self.sender_account.number)
        self.assertEqual(self.balance(self.sender), Decimal("4000"))
        self.assertEqual(self.balance(self.recipient), Decimal("0"))

    def test_recipient_identity_revoked_during_enquiry_never_debits(self):
        def resolve(*args):
            type(self.recipient).objects.filter(pk=self.recipient.pk).update(
                phone_verified=False)
            return {"success": True, "name": "ADA EZE"}

        with patch("banking.adapter.resolve_account", side_effect=resolve), \
                patch("banking.client.Client.transfer") as provider:
            response = self.post()
        self.assertEqual(response.status_code, 422)
        provider.assert_not_called()
        self.assertEqual(self.balance(self.sender), Decimal("5000"))

    def test_recipient_name_mismatch_never_debits(self):
        with patch("banking.adapter.resolve_account", return_value={"success": True, "name": "SOMEONE ELSE"}), \
                patch("banking.client.Client.transfer") as provider:
            response = self.post()
        self.assertEqual(response.status_code, 422)
        provider.assert_not_called()
        self.assertEqual(self.balance(self.sender), Decimal("5000"))

    def test_mock_name_enquiry_cannot_authorize_real_transfer(self):
        with patch("banking.adapter.resolve_account", return_value={"success": True, "name": "ADA EZE", "mock": True}), \
                patch("banking.client.Client.transfer") as provider:
            response = self.post()
        self.assertEqual(response.status_code, 422)
        provider.assert_not_called()
        self.assertEqual(self.balance(self.sender), Decimal("5000"))

    def test_enquiry_for_a_different_bank_cannot_authorize_transfer(self):
        with patch("banking.adapter.resolve_account", return_value={"success": True, "name": "ADA EZE", "bank_code": "999"}), \
                patch("banking.client.Client.transfer") as provider:
            response = self.post()
        self.assertEqual(response.status_code, 422)
        provider.assert_not_called()
        self.assertEqual(self.balance(self.sender), Decimal("5000"))

    def test_account_detached_during_name_enquiry_never_debits(self):
        def resolve(*args):
            BankAccount.objects.filter(wallet__user=self.recipient).update(status="review")
            return {"success": True, "name": "ADA EZE"}
        with patch("banking.adapter.resolve_account", side_effect=resolve), \
                patch("banking.client.Client.transfer") as provider:
            response = self.post()
        self.assertEqual(response.status_code, 422)
        provider.assert_not_called()
        self.assertEqual(self.balance(self.sender), Decimal("5000"))

    def test_restricted_sender_account_never_falls_back_to_pool(self):
        BankAccount.objects.filter(wallet__user=self.sender).update(pnd=True)
        with override_settings(WEMA={"CHANNEL_ID": "test", "KEYS": {"wallet": "test"}, "SOURCE_ACCOUNT": "0999999999"}):
            response, provider = self._send({"success": True, "status": "SUCCESS"})
        self.assertEqual(response.status_code, 403)
        provider.assert_not_called()
        self.assertEqual(self.balance(self.sender), Decimal("5000"))

    def test_unavailable_bank_rail_has_no_production_local_fallback(self):
        with override_settings(NINEPSB={**CONFIG, "ENABLED": False}), patch("banking.client.Client.transfer") as provider:
            response = self.post()
        self.assertEqual(response.status_code, 403)
        provider.assert_not_called()
        self.assertEqual(self.balance(self.sender), Decimal("5000"))
        self.assertEqual(self.balance(self.recipient), Decimal("0"))

    def test_missing_active_bank_configuration_never_debits(self):
        self.bank.active = False
        self.bank.save(update_fields=["active"])
        response, provider = self._send({"success": True, "status": "SUCCESS"})
        self.assertEqual(response.status_code, 422)
        provider.assert_not_called()
        self.assertEqual(self.balance(self.sender), Decimal("5000"))

    def test_changed_amount_on_same_key_cannot_instruct_second_bank_transfer(self):
        with patch("banking.adapter.resolve_account", return_value={"success": True, "name": "ADA EZE"}), \
                patch("banking.client.Client.transfer", return_value=Reply({"status": "SUCCESS", "responseCode": "00"}, 200)) as provider:
            first = self.post()
            second = self.post(amount="900")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json()["code"], "idempotency_conflict")
        provider.assert_called_once()
        self.assertEqual(self.balance(self.sender), Decimal("4000"))

    def test_retired_simulation_does_not_create_local_recipient_credit(self):
        with override_settings(WEMA={"SIMULATION": True}):
            response, provider = self._send({"success": True, "status": "SUCCESS"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        provider.assert_called_once()
        self.assertEqual(self.balance(self.sender), Decimal("4000"))
        self.assertEqual(self.balance(self.recipient), Decimal("0"))
