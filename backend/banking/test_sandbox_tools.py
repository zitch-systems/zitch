"""Sandbox-only testing shortcuts: auto-verified identity and float test funding."""
import json
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import IdentityProof, User
from wallet.models import Transaction, Wallet

from .accounts import complete_account, confirm_identity, start_identity
from .client import ProviderError, Reply
from .models import BankAccount, IdentityChallenge
from .notifications import ingest, process
from .test_client import CONFIG
from .test_identity import PROFILE

SANDBOX = {**CONFIG, "ENVIRONMENT": "sandbox", "UAT_APPROVED": False, "SANDBOX_CUSTOMERS_ENABLED": True,
           "SANDBOX_PHONES": [PROFILE["phoneNo"]], "SANDBOX_AUTO_VERIFY": True}
OPENED = Reply({"status": "SUCCESS", "data": {"accountNumber": "1100000001", "fullName": "Jane Doe"}}, 200)


def readback(**changes):
    data = {"number": "INTERNAL-WALLET", "nuban": "1100000001", "name": "Jane Doe", "status": "Active",
            "tier": "1", "pndstatus": "InActive", "freezeStatus": "InActive", "ledgerBalance": "0.00",
            "availableBalance": "0.00", **changes}
    return Reply({"status": "SUCCESS", "data": data}, 200)


class Base(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="sandbox", first_name="Jane", last_name="Doe",
            phone=PROFILE["phoneNo"], email=PROFILE["email"], phone_verified=True, email_verified=True)
        Wallet.objects.create(user=self.user)


@override_settings(NINEPSB=SANDBOX)
class AutoVerifyTests(Base):
    def start(self, identifier="22222222222", sms=None):
        with patch("banking.client.Client.initiate_identity") as identity, \
                patch("utility.providers.send_sms", return_value=sms or {"success": True}) as sent, \
                patch("banking.client.Client.open_wallet") as opened:
            started = start_identity(self.user, "bvn", identifier, PROFILE)
        identity.assert_not_called()
        opened.assert_not_called()
        return started, sent

    def test_any_bvn_gets_a_real_code_and_any_code_opens_the_wallet(self):
        started, sent = self.start()
        self.assertTrue(started["otp_required"])
        self.assertEqual(started["account_setup_state"], "pending")
        sent.assert_called_once()
        self.assertEqual(sent.call_args.args[0], self.user.phone)
        self.assertRegex(sent.call_args.args[1], r"^Your Zitch verification code is [0-9]{6}$")
        with patch("banking.client.Client.verify_identity") as verify, \
                patch("banking.client.Client.open_wallet", return_value=OPENED) as opened, \
                patch("banking.client.Client.wallet", return_value=readback()):
            result = confirm_identity(self.user, started["reference"], "000000")
        verify.assert_not_called()
        opened.assert_called_once()
        self.assertTrue(result["success"])
        self.assertEqual(BankAccount.objects.get().number, "1100000001")
        self.assertTrue(IdentityProof.objects.get().provider_reference.startswith("SANDBOX-AUTO-"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.bvn_verified)

    def test_the_code_must_still_be_six_digits(self):
        started, _ = self.start()
        with patch("banking.client.Client.open_wallet") as opened, \
                self.assertRaisesMessage(ValueError, "Enter the six-digit code"):
            confirm_identity(self.user, started["reference"], "12ab")
        opened.assert_not_called()

    def test_an_undelivered_sms_does_not_block_testing(self):
        started, _ = self.start(sms={"success": False})
        self.assertTrue(started["otp_required"])

    def test_face_verification_needs_no_code(self):
        from io import BytesIO
        from base64 import b64encode
        from PIL import Image
        raw = BytesIO()
        Image.new("RGB", (8, 8), "white").save(raw, "PNG")
        with patch("banking.client.Client.initiate_identity") as identity, \
                patch("utility.providers.send_sms") as sent, \
                patch("banking.client.Client.open_wallet", return_value=OPENED) as opened, \
                patch("banking.client.Client.wallet", return_value=readback()):
            result = start_identity(self.user, "bvn", "22222222222", PROFILE, method="FACIAL",
                                    image=b64encode(raw.getvalue()).decode())
        identity.assert_not_called()
        sent.assert_not_called()
        opened.assert_called_once()
        self.assertTrue(result["success"])

    def test_a_stuck_earlier_check_is_replaced(self):
        IdentityChallenge.objects.create(user=self.user, reference="ZISTUCK", kind="bvn", method="OTP",
            identity_hash="other", last4="1111", environment="sandbox", state="unconfirmed",
            encrypted_input="sealed", expires_at=timezone.now() + timedelta(minutes=5))
        started, _ = self.start()
        self.assertTrue(started["otp_required"])
        stuck = IdentityChallenge.objects.get(reference="ZISTUCK")
        self.assertEqual((stuck.state, stuck.encrypted_input), ("expired", ""))

    def test_the_bank_reason_is_shown_when_the_sandbox_will_not_open_the_wallet(self):
        started, _ = self.start()
        refused = Reply({"status": "FAILED", "responseCode": "99", "message": "Invalid BVN supplied"}, 200)
        with patch("banking.client.Client.open_wallet", return_value=refused), \
                self.assertLogs("banking.accounts", "WARNING") as logs:
            result = confirm_identity(self.user, started["reference"], "123456")
        self.assertFalse(result["success"])
        self.assertIn("Invalid BVN supplied", result["message"])
        self.assertTrue(any("bank_wallet_open_not_confirmed" in line for line in logs.output))
        self.assertTrue(all("22222222222" not in line for line in logs.output))
        self.assertFalse(BankAccount.objects.exists())

    def test_the_sandbox_wallet_is_accepted_in_the_name_the_bank_gives_it(self):
        started, _ = self.start()
        dummy = Reply({"status": "SUCCESS", "data": {"accountNumber": "1100000001", "fullName": "Sandbox Test Person"}}, 200)
        with patch("banking.client.Client.open_wallet", return_value=dummy), \
                patch("banking.client.Client.wallet", return_value=readback(name="Sandbox Test Person")):
            result = confirm_identity(self.user, started["reference"], "000000")
        self.assertTrue(result["success"])
        self.assertEqual(BankAccount.objects.get().name, "Sandbox Test Person")

    def test_the_read_back_must_still_match_the_banks_own_name(self):
        started, _ = self.start()
        dummy = Reply({"status": "SUCCESS", "data": {"accountNumber": "1100000001", "fullName": "Sandbox Test Person"}}, 200)
        with patch("banking.client.Client.open_wallet", return_value=dummy), \
                patch("banking.client.Client.wallet", return_value=readback(name="Someone Else")):
            result = confirm_identity(self.user, started["reference"], "000000")
        self.assertFalse(result["success"])
        self.assertFalse(BankAccount.objects.exists())

    def test_an_unfinished_attempt_expires_and_the_tester_can_start_again(self):
        stuck = IdentityChallenge.objects.create(user=self.user, reference="ZIOPENING", kind="bvn", method="OTP",
            identity_hash="other", last4="1111", environment="sandbox", state="opening", verified_at=timezone.now(),
            encrypted_input="sealed", expires_at=timezone.now() - timedelta(minutes=1))
        with patch("banking.client.Client.lookup_wallet") as lookup:
            payload = complete_account(stuck)
        lookup.assert_not_called()
        self.assertEqual(payload["account_setup_state"], "expired")
        started, _ = self.start()
        self.assertTrue(started["otp_required"])

    @override_settings(NINEPSB={**SANDBOX, "ENVIRONMENT": "production", "UAT_APPROVED": True})
    def test_the_flag_never_applies_outside_the_sandbox(self):
        with patch("banking.client.Client.initiate_identity",
                   return_value=Reply({"status": "SUCCESS", "message": "PENDING"}, 200)) as identity, \
                patch("utility.providers.send_sms") as sent, \
                patch("banking.client.Client.open_wallet") as opened:
            result = start_identity(self.user, "bvn", "22222222222", PROFILE)
            with patch("banking.client.Client.verify_identity",
                       return_value=Reply({"message": "FAILED"}, 200)) as verify:
                confirm_identity(self.user, result["reference"], "000000")
        identity.assert_called_once()
        verify.assert_called_once()
        sent.assert_not_called()
        opened.assert_not_called()
        self.assertFalse(BankAccount.objects.exists())

    @override_settings(NINEPSB={**SANDBOX, "SANDBOX_AUTO_VERIFY": False})
    def test_without_the_flag_the_sandbox_uses_the_bank_check(self):
        with patch("banking.client.Client.initiate_identity",
                   return_value=Reply({"status": "SUCCESS", "message": "PENDING"}, 200)) as identity, \
                patch("utility.providers.send_sms") as sent:
            start_identity(self.user, "bvn", "22222222222", PROFILE)
        identity.assert_called_once()
        sent.assert_not_called()


    def test_a_wallet_an_earlier_attempt_opened_is_found_with_that_attempts_identity(self):
        # The first attempt opened the test wallet but never attached it.
        first, _ = self.start(identifier="33333333333")
        IdentityChallenge.objects.filter(reference=first["reference"]).update(
            state="review", verified_at=timezone.now())
        started, _ = self.start()
        exists = Reply({"status": "FAILED", "message": "A Wallet Already Exists For This User"}, 400)
        found = Reply({"status": "SUCCESS", "data": {"accountNumber": "1100000001", "fullName": "Jane Doe"}}, 200)
        missing = Reply({"status": "FAILED", "message": "An error occurred"}, 500)
        lookups = []

        def lookup(kind, identifier):
            lookups.append(identifier)
            return found if identifier == "33333333333" else missing
        with patch("banking.client.Client.open_wallet", return_value=exists), \
                patch("banking.client.Client.lookup_wallet", side_effect=lookup), \
                patch("banking.client.Client.wallet", return_value=readback()):
            result = confirm_identity(self.user, started["reference"], "123456")
        self.assertTrue(result["success"], result)
        self.assertEqual(lookups, ["22222222222", "33333333333"])
        self.assertEqual(BankAccount.objects.get().number, "1100000001")

    def test_resend_texts_a_new_code_for_the_same_check(self):
        from .accounts import resend_code
        started, _ = self.start()
        with patch("utility.providers.send_sms", return_value={"success": True}) as sent:
            again = resend_code(self.user, started["reference"])
        sent.assert_called_once()
        self.assertTrue(again["otp_required"])
        self.assertNotEqual(again["reference"], started["reference"])
        self.assertEqual(IdentityChallenge.objects.get(reference=started["reference"]).state, "expired")
        with self.assertRaisesRegex(ValueError, "again"):
            resend_code(self.user, started["reference"])

    def test_the_bank_lists_wallets_and_the_one_for_this_bvn_is_attached(self):
        # get_wallet answers with data.wallet: a list, names prefixed with the merchant id.
        started, _ = self.start()
        exists = Reply({"status": "FAILED", "message": "A Wallet Already Exists For This User"}, 400)
        listed = Reply({"status": "SUCCESS", "message": "Wallet Information Request Successful", "data": {"wallet": [
            {"accountName": "ZITCH/Jane Doe", "accountNumber": "1100000001", "merchantId": "ZITCH",
             "bvn": "22222222222", "phoneNumber": PROFILE["phoneNo"]}]}}, 200)
        with patch("banking.client.Client.open_wallet", return_value=exists), \
                patch("banking.client.Client.request", return_value=listed) as request, \
                patch("banking.client.Client.wallet", return_value=readback()):
            result = confirm_identity(self.user, started["reference"], "123456")
        self.assertTrue(result["success"], result)
        self.assertEqual(request.call_args.args[0], "get_wallet")
        bank = BankAccount.objects.get()
        self.assertEqual((bank.number, bank.name), ("1100000001", "Jane Doe"))

    def test_a_listed_wallet_for_another_identity_is_not_attached(self):
        from .client import found_wallet
        listed = Reply({"status": "SUCCESS", "data": {"wallet": [{"accountNumber": "1100000001", "bvn": "99999999999"}]}}, 200)
        self.assertEqual(found_wallet(listed, "bvn", "22222222222").data, {})

    def test_a_test_wallet_is_attached_by_its_account_number(self):
        started, _ = self.start()
        exists = Reply({"status": "FAILED", "message": "A Wallet Already Exists For This User"}, 400)
        missing = Reply({"status": "FAILED", "message": "An error occurred"}, 500)
        with patch("banking.client.Client.open_wallet", return_value=exists), \
                patch("banking.client.Client.lookup_wallet", return_value=missing):
            self.assertFalse(confirm_identity(self.user, started["reference"], "123456")["success"])
        out = StringIO()
        with patch("banking.client.Client.wallet", return_value=readback()):
            call_command("bank_sandbox_attach", phone=PROFILE["phoneNo"], account="1100000001", stdout=out)
        self.assertIn('"attached": true', out.getvalue())
        self.assertEqual(BankAccount.objects.get().number, "1100000001")
        with self.assertRaisesRegex(CommandError, "already"):
            call_command("bank_sandbox_attach", phone=PROFILE["phoneNo"], account="1100000001", stdout=StringIO())

    @override_settings(NINEPSB={**CONFIG, "ENVIRONMENT": "production"})
    def test_attaching_a_known_wallet_is_refused_outside_the_sandbox(self):
        with self.assertRaisesRegex(CommandError, "sandbox"):
            call_command("bank_sandbox_attach", phone=PROFILE["phoneNo"], account="1100000001", stdout=StringIO())


@override_settings(NINEPSB=SANDBOX)
class SandboxFundingTests(Base):
    def setUp(self):
        super().setUp()
        self.wallet = Wallet.objects.get(user=self.user)
        self.bank = BankAccount.objects.create(wallet=self.wallet, number="1100000001", name="Jane Doe",
            environment="sandbox", tier=1, pnd=False, frozen=False, status="active", checked_at=timezone.now(),
            available_balance=0, ledger_balance=0, opening_review_required=False)

    def fund(self, amount="5000", reply=None, balance="5000.00"):
        out = StringIO()
        reply = reply or Reply({"status": "SUCCESS", "responseCode": "00", "message": "Approved"}, 200)
        with patch("banking.client.Client.float_transfer", return_value=reply) as credit, \
                patch("banking.client.Client.wallet", return_value=readback(availableBalance=balance,
                                                                            ledgerBalance=balance)):
            call_command("bank_sandbox_fund", phone=PROFILE["phoneNo"], amount=amount, stdout=out)
        return credit, out.getvalue()

    def test_credit_is_made_at_the_bank_and_recorded_once_in_zitch(self):
        credit, out = self.fund()
        self.assertEqual(credit.call_args.args[0], "credit")
        self.assertEqual(credit.call_args.args[2], Decimal("5000.00"))
        self.assertEqual(credit.call_args.args[3], "1100000001")
        self.wallet.refresh_from_db()
        self.bank.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("5000.00"))
        self.assertEqual(self.bank.available_balance, Decimal("5000.00"))
        self.assertIn('"recorded_in_zitch": true', out)

    def test_the_observed_nested_success_records_the_100_naira_credit(self):
        reply = Reply({"status": "SUCCESS", "message": "Approved by Financial Institution",
                       "data": {"responseCode": "00"}}, 200)
        sent, out = self.fund(amount="100", reply=reply)
        sent.assert_called_once()
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("100.00"))
        self.assertEqual(Transaction.objects.get().amount, Decimal("100.00"))
        self.assertTrue(json.loads(out)["recorded_in_zitch"])

    def test_funding_records_only_the_balance_backed_credit_after_a_bank_charge(self):
        _, out = self.fund(amount="100", balance="90.00")
        report = json.loads(out)
        self.assertEqual(report["requested_amount"], "100.00")
        self.assertEqual(report["amount"], "90.00")
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("90.00"))
        self.assertEqual(Transaction.objects.get().meta["evidence"], "bank_credit_and_balance_enquiry")

    def test_an_approved_credit_without_a_fresh_balance_is_not_overcredited_or_retried(self):
        out = StringIO()
        with patch("banking.client.Client.float_transfer", return_value=Reply({"status": "SUCCESS",
                "data": {"responseCode": "00"}}, 200)) as sent, \
                patch("banking.client.Client.wallet", side_effect=ProviderError("network_unavailable")), \
                self.assertRaisesRegex(CommandError, "Do not fund again"):
            call_command("bank_sandbox_fund", phone=PROFILE["phoneNo"], amount="100", stdout=out)
        sent.assert_called_once()
        self.assertFalse(json.loads(out.getvalue())["recorded_in_zitch"])
        self.assertFalse(Transaction.objects.exists())

    def test_an_approved_credit_with_no_balance_gap_does_not_create_unbacked_money(self):
        with self.assertRaisesRegex(CommandError, "Do not fund again"):
            self.fund(amount="100", balance="0.00")
        self.assertFalse(Transaction.objects.exists())

    def test_a_later_bank_notice_for_the_credit_does_not_count_twice(self):
        self.fund()
        body = {"code": "00", "message": "SUCCESS", "accountnumber": "1100000001", "amount": "5000.00",
                "nipsessionid": "SANDBOX-SESSION", "narration": "Zitch sandbox test funding"}
        notice = ingest("transfer", body)
        with patch("banking.client.Client.notification", return_value=Reply({"status": "SUCCESS", "data": body}, 200)):
            self.assertTrue(process(notice))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("5000.00"))
        self.assertEqual(Transaction.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Transaction.objects.get().meta["bank_notification"], "SANDBOX-SESSION")

    def test_a_later_notice_for_the_requested_amount_does_not_repeat_a_net_credit(self):
        self.fund(amount="100", balance="90.00")
        body = {"code": "00", "message": "SUCCESS", "accountnumber": self.bank.number, "amount": "100.00",
                "nipsessionid": "SANDBOX-NET-SESSION", "narration": "Zitch sandbox test funding"}
        notice = ingest("transfer", body)
        with patch("banking.client.Client.notification", return_value=Reply({"status": "SUCCESS", "data": body}, 200)):
            self.assertTrue(process(notice))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("90.00"))
        self.assertEqual(Transaction.objects.count(), 1)
        self.assertEqual(Transaction.objects.get().meta["bank_notification_amount"], "100.00")

    def test_a_refused_or_unconfirmed_credit_records_nothing(self):
        for reply in (Reply({"status": "FAILED", "responseCode": "51", "message": "Insufficient float"}, 200),
                      Reply({}, 504)):
            with self.subTest(reply=reply.http_status), self.assertRaises(CommandError):
                self.fund(reply=reply)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("0"))
        self.assertFalse(Transaction.objects.exists())

    def test_an_unsent_credit_is_reported_as_a_safe_retry(self):
        out = StringIO()
        with patch("banking.client.Client.float_transfer",
                   side_effect=ProviderError("authentication_failed")) as credit, \
                self.assertRaisesMessage(CommandError, "no money moved"):
            call_command("bank_sandbox_fund", phone=PROFILE["phoneNo"], amount="5000", stdout=out)
        credit.assert_called_once()
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("0"))
        self.assertFalse(Transaction.objects.exists())

    def test_a_lost_reply_is_unconfirmed_and_never_reported_as_a_refusal(self):
        out = StringIO()
        with patch("banking.client.Client.float_transfer",
                   side_effect=ProviderError("network_unavailable", uncertain=True)), \
                patch("banking.client.Client.wallet", return_value=readback(availableBalance="50000.00",
                                                                            ledgerBalance="50000.00")), \
                self.assertRaisesRegex(CommandError, "may already have been applied"):
            call_command("bank_sandbox_fund", phone=PROFILE["phoneNo"], amount="50000", stdout=out)
        report = out.getvalue()
        self.assertIn('"state": "unknown"', report)
        self.assertIn('"recorded_in_zitch": false', report)
        # The operator is shown the balance either side of the lost reply.
        self.assertIn('"last_known_balance": "0.00"', report)
        self.assertIn('"bank_available_balance": "50000.00"', report)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("0"))
        self.assertFalse(Transaction.objects.exists())

    def test_a_lost_reply_still_reports_when_the_balance_cannot_be_read(self):
        out = StringIO()
        with patch("banking.client.Client.float_transfer",
                   side_effect=ProviderError("network_unavailable", uncertain=True)), \
                patch("banking.client.Client.wallet", side_effect=ProviderError("network_unavailable")), \
                self.assertRaisesRegex(CommandError, "unconfirmed"):
            call_command("bank_sandbox_fund", phone=PROFILE["phoneNo"], amount="5000", stdout=out)
        self.assertIn('"bank_available_balance": "not refreshed"', out.getvalue())
        self.assertFalse(Transaction.objects.exists())

    def test_limits_and_targets(self):
        for amount in ("0", "50000.01", "abc"):
            with self.subTest(amount=amount), self.assertRaises(CommandError):
                self.fund(amount=amount)
        User.objects.filter(pk=self.user.pk).update(phone_verified=False)
        with self.assertRaisesMessage(CommandError, "not an enrolled sandbox tester"):
            self.fund()
        self.assertFalse(Transaction.objects.exists())

    def reconcile(self, apply=False, balance="149970.00"):
        out = StringIO()
        with patch("banking.client.Client.wallet", return_value=readback(availableBalance=balance,
                                                                         ledgerBalance=balance)):
            call_command("bank_sandbox_reconcile", phone=PROFILE["phoneNo"], apply=apply, stdout=out)
        return out.getvalue()

    def test_a_dry_run_reports_the_shortfall_and_records_nothing(self):
        report = self.reconcile()
        self.assertIn('"shortfall": "149970.00"', report)
        self.assertIn('"applied": false', report)
        self.assertIn('"reason": "dry_run"', report)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("0"))
        self.assertFalse(Transaction.objects.exists())

    def test_applying_credits_the_shortfall_once_and_is_then_in_line(self):
        report = self.reconcile(apply=True)
        self.assertIn('"applied": true', report)
        applied = json.loads(report)
        self.assertEqual(applied["zitch_balance_before"], "0.00")
        self.assertEqual(applied["zitch_balance"], "149970.00")
        self.assertEqual(applied["credited_amount"], "149970.00")
        self.assertEqual(applied["shortfall"], "0.00")
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("149970.00"))
        txn = Transaction.objects.get()
        self.assertEqual(txn.amount, Decimal("149970.00"))
        self.assertEqual(txn.meta["evidence"], "bank_balance_enquiry")
        # Running it again finds nothing left to do rather than crediting twice.
        again = self.reconcile(apply=True)
        self.assertIn('"reason": "already_in_line"', again)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("149970.00"))
        self.assertEqual(Transaction.objects.count(), 1)

    def test_an_unconfirmed_balance_never_becomes_a_credit(self):
        with patch("banking.client.Client.wallet", side_effect=ProviderError("network_unavailable")), \
                self.assertRaisesMessage(CommandError, "did not confirm the balance"):
            call_command("bank_sandbox_reconcile", phone=PROFILE["phoneNo"], apply=True, stdout=StringIO())
        self.assertFalse(Transaction.objects.exists())

    def test_a_missing_bank_balance_is_reported_without_recording_a_credit(self):
        with patch("banking.client.Client.wallet", return_value=readback(availableBalance=None)), \
                self.assertRaisesRegex(CommandError, "account_balance_unconfirmed"):
            call_command("bank_sandbox_reconcile", phone=PROFILE["phoneNo"], apply=True, stdout=StringIO())
        self.assertFalse(Transaction.objects.exists())

    def test_zitch_ahead_of_the_bank_is_never_debited_here(self):
        self.wallet.balance = Decimal("200000.00")
        self.wallet.save(update_fields=["balance"])
        with self.assertRaisesMessage(CommandError, "needs review"):
            self.reconcile(apply=True)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("200000.00"))
        self.assertFalse(Transaction.objects.exists())

    @override_settings(NINEPSB={**SANDBOX, "ENVIRONMENT": "production", "UAT_APPROVED": True})
    def test_reconciliation_is_never_available_in_production(self):
        with patch("banking.client.Client.wallet") as enquiry, \
                self.assertRaisesMessage(CommandError, "only with the bank sandbox"):
            call_command("bank_sandbox_reconcile", phone=PROFILE["phoneNo"], stdout=StringIO())
        enquiry.assert_not_called()

    @override_settings(NINEPSB={**SANDBOX, "ENVIRONMENT": "production", "UAT_APPROVED": True})
    def test_never_available_in_production(self):
        with patch("banking.client.Client.float_transfer") as credit, \
                self.assertRaisesMessage(CommandError, "only with the bank sandbox"):
            call_command("bank_sandbox_fund", phone=PROFILE["phoneNo"], amount="5000", stdout=StringIO())
        credit.assert_not_called()
