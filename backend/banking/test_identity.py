import base64
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings, Client as WebClient
from django.utils import timezone

from accounts.models import User, IdentityProof
from wallet.models import Wallet
from .accounts import start_identity, confirm_identity, complete_account, challenge_payload
from .client import Reply, ProviderError
from .models import BankAccount, IdentityChallenge, PreviousBankArchive
from .test_client import CONFIG

PROFILE = {"lastName": "Doe", "otherNames": "Jane", "phoneNo": "07041267880", "gender": 1,
           "dateOfBirth": "01/01/1990", "address": "1 Test Road", "email": "jane@example.test"}


@override_settings(NINEPSB=CONFIG)
class IdentityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="identity", first_name="Jane", last_name="Doe",
            phone=PROFILE["phoneNo"], email=PROFILE["email"], phone_verified=True, email_verified=True)
        self.wallet = Wallet.objects.create(user=self.user)

    def pending(self):
        with patch("banking.client.Client.initiate_identity", return_value=Reply({"status": "SUCCESS", "message": "PENDING"}, 200)):
            result = start_identity(self.user, "bvn", "12345678901", PROFILE)
        return IdentityChallenge.objects.get(reference=result["reference"])

    def bank_reply(self, **changes):
        data = {"number": "INTERNAL-WALLET", "nuban": "1100000001", "name": "Jane Doe", "status": "Active", "tier": "1",
                "pndstatus": "InActive", "freezeStatus": "InActive", "ledgerBalance": "0.00", "availableBalance": "0.00"}
        return Reply({"status": "SUCCESS", "data": {**data, **changes}}, 200)

    def test_pending_otp_does_not_grant_identity_or_bank_account(self):
        row = self.pending()
        self.assertEqual(row.state, "pending")
        self.assertFalse(IdentityProof.objects.exists())
        self.assertFalse(BankAccount.objects.exists())
        self.assertNotIn("12345678901", row.encrypted_input)

    def test_expired_otp_is_not_sent_and_raw_input_is_erased(self):
        row = self.pending()
        IdentityChallenge.objects.filter(pk=row.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
        with patch("banking.client.Client.verify_identity") as send, self.assertRaises(ValueError):
            confirm_identity(self.user, row.reference, "123456")
        send.assert_not_called()
        row.refresh_from_db()
        self.assertEqual(row.encrypted_input, "")
        self.assertEqual(row.state, "expired")

    def test_another_user_cannot_verify_a_challenge(self):
        row = self.pending()
        other = User.objects.create_user(username="other")
        with patch("banking.client.Client.verify_identity") as send, self.assertRaises(ValueError):
            confirm_identity(other, row.reference, "123456")
        send.assert_not_called()

    def test_account_opens_once_and_requires_readback(self):
        row = self.pending()
        with patch("banking.client.Client.verify_identity", return_value=Reply({"message": "SUCCESS"}, 200)), \
             patch("banking.client.Client.open_wallet", return_value=Reply({"status": "SUCCESS", "data": {"accountNumber": "1100000001", "fullName": "Jane Doe"}}, 200)) as opened, \
             patch("banking.client.Client.wallet", return_value=self.bank_reply()):
            result = confirm_identity(self.user, row.reference, "123456")
            confirm_identity(self.user, row.reference, "123456")
        self.assertTrue(result["success"])
        self.assertEqual(opened.call_count, 1)
        self.assertEqual(BankAccount.objects.get().number, "1100000001")
        self.assertEqual(IdentityProof.objects.get().source, "9psb_otp")
        self.user.refresh_from_db()
        self.assertEqual(self.user.tier, 1)
        row.refresh_from_db()
        self.assertEqual(row.encrypted_input, "")

    def test_lost_account_response_recovers_by_read_without_another_open(self):
        row = self.pending()
        with patch("banking.client.Client.verify_identity", return_value=Reply({"message": "SUCCESS"}, 200)), \
             patch("banking.client.Client.open_wallet", side_effect=ProviderError("network_unavailable", uncertain=True)) as opened:
            with self.assertRaises(ProviderError):
                confirm_identity(self.user, row.reference, "123456")
            row.refresh_from_db()
            with patch("banking.client.Client.lookup_wallet", return_value=Reply({"status": "SUCCESS", "data": {"number": "1100000001", "name": "Jane Doe"}}, 200)) as lookup, \
                 patch("banking.client.Client.wallet", return_value=self.bank_reply()):
                result = complete_account(row)
            self.assertTrue(result["success"])
            self.assertEqual(opened.call_count, 1)
            lookup.assert_called_once()

    def test_wrong_account_holder_cannot_attach(self):
        row = self.pending()
        with patch("banking.client.Client.verify_identity", return_value=Reply({"message": "SUCCESS"}, 200)), \
             patch("banking.client.Client.open_wallet", return_value=Reply({"status": "SUCCESS", "data": {"accountNumber": "1100000001", "fullName": "Someone Else"}}, 200)), \
             self.assertRaises(ValueError):
            confirm_identity(self.user, row.reference, "123456")
        self.assertFalse(BankAccount.objects.exists())
        self.assertFalse(IdentityProof.objects.exists())

    def test_nonzero_opening_balance_stays_in_review(self):
        row = self.pending()
        with patch("banking.client.Client.verify_identity", return_value=Reply({"message": "SUCCESS"}, 200)), \
             patch("banking.client.Client.open_wallet", return_value=Reply({"status": "SUCCESS", "data": {"accountNumber": "1100000001", "fullName": "Jane Doe"}}, 200)), \
             patch("banking.client.Client.wallet", return_value=self.bank_reply(ledgerBalance="100.00", availableBalance="100.00")):
            confirm_identity(self.user, row.reference, "123456")
        bank = BankAccount.objects.get()
        self.assertTrue(bank.opening_review_required)
        self.assertEqual(bank.status, "review")
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 0)

    def test_identity_start_is_idempotent_within_challenge_window(self):
        row = self.pending()
        with patch("banking.client.Client.initiate_identity") as send:
            result = start_identity(self.user, "bvn", "12345678901", PROFILE)
        send.assert_not_called()
        self.assertEqual(result["reference"], row.reference)

    def test_face_method_requires_a_valid_image_before_network(self):
        with patch("banking.client.Client.initiate_identity") as send, self.assertRaises(ValueError):
            start_identity(self.user, "bvn", "12345678901", PROFILE, method="FACIAL", image="not-an-image")
        send.assert_not_called()
        self.assertFalse(IdentityChallenge.objects.exists())

    def test_profile_cannot_use_a_different_phone(self):
        with patch("banking.client.Client.initiate_identity") as send, self.assertRaises(ValueError):
            start_identity(self.user, "bvn", "12345678901", {**PROFILE, "phoneNo": "09017746353"})
        send.assert_not_called()

    def test_webhook_requires_basic_credentials_and_does_not_trust_body(self):
        client = WebClient()
        self.assertEqual(client.post("/9psb/webhook?event=transfer", data={}, content_type="application/json").status_code, 401)
        with override_settings(NINEPSB={**CONFIG, "WEBHOOK_USERNAME": "hook", "WEBHOOK_PASSWORD": "secret"}):
            header = "Basic " + base64.b64encode(b"hook:wrong").decode()
            self.assertEqual(client.post("/9psb/webhook?event=transfer", data={}, content_type="application/json", HTTP_AUTHORIZATION=header).status_code, 401)
        self.assertEqual(self.wallet.balance, 0)

    def test_old_bank_routes_no_longer_resolve(self):
        from django.urls import resolve, Resolver404
        for path in ("/api/wallet/wema/create/", "/webhooks/wema/transaction/anything", "/api/kyc/face/start/"):
            with self.subTest(path=path), self.assertRaises(Resolver404):
                resolve(path)

    def test_status_recovers_only_the_current_customers_challenge(self):
        from accounts.models import AccessToken
        row = self.pending()
        token = AccessToken.issue(self.user).key
        result = self.client.post("/api/banking/status/", {}, content_type="application/json", HTTP_AUTHORIZATION="Bearer " + token).json()
        self.assertEqual(result["identity_challenge"]["tracking_id"], row.reference)
        self.assertEqual(result["identity_challenge"]["kind"], "bvn")
        self.assertNotIn("12345678901", str(result))
        other = User.objects.create_user(username="other-status")
        result = self.client.post("/api/banking/status/", {}, content_type="application/json", HTTP_AUTHORIZATION="Bearer " + AccessToken.issue(other).key).json()
        self.assertIsNone(result["identity_challenge"])
