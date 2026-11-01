"""Sandbox-only testing shortcuts: auto-verified identity and float test funding."""
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import IdentityProof, User
from wallet.models import Transaction, Wallet

from .accounts import start_identity
from .client import Reply
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
    def test_any_eleven_digit_bvn_opens_a_sandbox_wallet_without_the_identity_check(self):
        with patch("banking.client.Client.initiate_identity") as identity, \
                patch("banking.client.Client.open_wallet", return_value=OPENED) as opened, \
                patch("banking.client.Client.wallet", return_value=readback()):
            result = start_identity(self.user, "bvn", "22222222222", PROFILE)
        identity.assert_not_called()
        opened.assert_called_once()
        self.assertTrue(result["success"])
        self.assertEqual(BankAccount.objects.get().number, "1100000001")
        proof = IdentityProof.objects.get()
        self.assertTrue(proof.provider_reference.startswith("SANDBOX-AUTO-"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.bvn_verified)

    def test_a_stuck_earlier_check_is_replaced(self):
        IdentityChallenge.objects.create(user=self.user, reference="ZISTUCK", kind="bvn", method="OTP",
            identity_hash="other", last4="1111", environment="sandbox", state="unconfirmed",
            encrypted_input="sealed", expires_at=timezone.now() + timedelta(minutes=5))
        with patch("banking.client.Client.open_wallet", return_value=OPENED), \
                patch("banking.client.Client.wallet", return_value=readback()):
            result = start_identity(self.user, "bvn", "22222222222", PROFILE)
        self.assertTrue(result["success"])
        stuck = IdentityChallenge.objects.get(reference="ZISTUCK")
        self.assertEqual((stuck.state, stuck.encrypted_input), ("expired", ""))

    def test_the_bank_reason_is_shown_when_the_sandbox_will_not_open_the_wallet(self):
        refused = Reply({"status": "FAILED", "responseCode": "99", "message": "Invalid BVN supplied"}, 200)
        with patch("banking.client.Client.open_wallet", return_value=refused), \
                self.assertLogs("banking.accounts", "WARNING") as logs:
            result = start_identity(self.user, "bvn", "22222222222", PROFILE)
        self.assertFalse(result["success"])
        self.assertIn("Invalid BVN supplied", result["message"])
        self.assertTrue(any("bank_wallet_open_not_confirmed" in line for line in logs.output))
        self.assertTrue(all("22222222222" not in line for line in logs.output))
        self.assertFalse(BankAccount.objects.exists())

    @override_settings(NINEPSB={**SANDBOX, "ENVIRONMENT": "production", "UAT_APPROVED": True})
    def test_the_flag_never_applies_outside_the_sandbox(self):
        with patch("banking.client.Client.initiate_identity",
                   return_value=Reply({"status": "SUCCESS", "message": "PENDING"}, 200)) as identity, \
                patch("banking.client.Client.open_wallet") as opened:
            result = start_identity(self.user, "bvn", "22222222222", PROFILE)
        identity.assert_called_once()
        opened.assert_not_called()
        self.assertTrue(result["otp_required"])

    @override_settings(NINEPSB={**SANDBOX, "SANDBOX_AUTO_VERIFY": False})
    def test_without_the_flag_the_sandbox_uses_the_bank_check(self):
        with patch("banking.client.Client.initiate_identity",
                   return_value=Reply({"status": "SUCCESS", "message": "PENDING"}, 200)) as identity:
            start_identity(self.user, "bvn", "22222222222", PROFILE)
        identity.assert_called_once()


@override_settings(NINEPSB=SANDBOX)
class SandboxFundingTests(Base):
    def setUp(self):
        super().setUp()
        self.wallet = Wallet.objects.get(user=self.user)
        self.bank = BankAccount.objects.create(wallet=self.wallet, number="1100000001", name="Jane Doe",
            environment="sandbox", tier=1, pnd=False, frozen=False, status="active", checked_at=timezone.now(),
            available_balance=0, ledger_balance=0, opening_review_required=False)

    def fund(self, amount="5000", reply=None):
        out = StringIO()
        reply = reply or Reply({"status": "SUCCESS", "responseCode": "00", "message": "Approved"}, 200)
        with patch("banking.client.Client.float_transfer", return_value=reply) as credit, \
                patch("banking.client.Client.wallet", return_value=readback(availableBalance="5000.00",
                                                                            ledgerBalance="5000.00")):
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

    def test_a_refused_or_unconfirmed_credit_records_nothing(self):
        for reply in (Reply({"status": "FAILED", "responseCode": "51", "message": "Insufficient float"}, 200),
                      Reply({}, 504)):
            with self.subTest(reply=reply.http_status), self.assertRaises(CommandError):
                self.fund(reply=reply)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("0"))
        self.assertFalse(Transaction.objects.exists())

    def test_limits_and_targets(self):
        for amount in ("0", "50000.01", "abc"):
            with self.subTest(amount=amount), self.assertRaises(CommandError):
                self.fund(amount=amount)
        User.objects.filter(pk=self.user.pk).update(phone_verified=False)
        with self.assertRaisesMessage(CommandError, "not an enrolled sandbox tester"):
            self.fund()
        self.assertFalse(Transaction.objects.exists())

    @override_settings(NINEPSB={**SANDBOX, "ENVIRONMENT": "production", "UAT_APPROVED": True})
    def test_never_available_in_production(self):
        with patch("banking.client.Client.float_transfer") as credit, \
                self.assertRaisesMessage(CommandError, "only with the bank sandbox"):
            call_command("bank_sandbox_fund", phone=PROFILE["phoneNo"], amount="5000", stdout=StringIO())
        credit.assert_not_called()
