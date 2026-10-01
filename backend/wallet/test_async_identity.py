"""Accepted OTP -> delayed callback -> saved BVN -> usable airtime, with no replay."""
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import IdentityProof, hash_identifier, rehydrate_verified_identity_flags
from common.http import unverified_error
from whatsapp import router

from .identity import finish_accepted_identity
from .models import Transaction, WemaProvisioningAttempt
from .services import get_or_create_wallet
from .tests import make_user
from .views import complete_wema_provisioning

TOKEN = "async-identity-test-callback"
BVN = "22222222222"
LIVE = {"CHANNEL_ID": "test-channel", "KEYS": {"wallet": "test-wallet"},
        "SIMULATION": False, "CALLBACK_TOKEN": TOKEN, "CALLBACK_ENFORCE_IPS": False,
        "CALLBACK_IPS": ["135.236.18.76"]}


@override_settings(WEMA=LIVE, PAYMENT_PROVIDER="wema", KYC_PROVIDER="wema")
class AsyncIdentityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user, self.token = make_user("08030000031", "async@example.test",
                                          pin="123456", balance="500", tier=0,
                                          identity_verified=False)
        self.wallet = get_or_create_wallet(self.user)
        self.attempt = WemaProvisioningAttempt.objects.create(
            user=self.user, tracking_id="BANK-OTP-31", identity_type="bvn",
            identity_hash=hash_identifier(BVN), identity_last4=BVN[-4:],
            expires_at=timezone.now() + timedelta(minutes=10))
        blocker = patch("requests.sessions.Session.request", side_effect=AssertionError("Unexpected network"))
        blocker.start()
        self.addCleanup(blocker.stop)

    def accept_otp(self):
        with patch("utility.wema.validate_wallet_otp", return_value={"success": True}) as validate, \
                patch("utility.wema.get_account_details", return_value={"success": False}):
            result, status = complete_wema_provisioning(self.user, "123456", self.attempt.tracking_id)
        self.assertEqual(status, 202)
        self.assertTrue(result["pending"])
        validate.assert_called_once()
        self.attempt.refresh_from_db()
        self.assertIsNotNone(self.attempt.otp_verified_at)
        self.assertFalse(self.user.bvn_verified)

    def callback(self, bank_name="EZE ADA"):
        with patch("utility.wema.lift_debit_restriction", return_value={"success": True}), \
                patch("utility.wema.get_kyc_status", return_value={"success": True, "name": bank_name}), \
                patch("whatsapp.router.reply"), patch("whatsapp.router.reply_buttons"):
            return self.client.post(f"/webhooks/wema/account/{TOKEN}", {
                "requestType": 2, "data": {"nuban": "0100000031", "nubanName": "",
                "phoneNumber": self.user.phone, "email": self.user.email,
                "nubanStatus": "Active", "type": 1}}, content_type="application/json")

    def attach(self):
        self.wallet.account_number = "0100000031"
        self.wallet.account_reference = "WEMA-ASYNC-31"
        self.wallet.save(update_fields=["account_number", "account_reference"])

    def test_delayed_callback_completes_bvn_and_airtime_debits_once(self):
        self.accept_otp()
        self.assertEqual(self.callback().status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.bvn_verified)
        self.assertEqual(self.user.tier, 1)
        self.assertIsNone(unverified_error(self.user))
        status = self.client.post("/api/kyc/status/", {"access_token": self.token},
                                  content_type="application/json").json()
        self.assertTrue(status["bvn_verified"])
        self.assertFalse(status.get("pending", False))
        with patch("utility.views.vtu_purchase", return_value={"success": True}), \
                patch("utility.providers.vas_can_settle", return_value=True):
            payload = {"access_token": self.token, "amount": "100", "network": "1",
                       "phone": self.user.phone, "transaction_pin": "123456",
                       "idempotency_key": "async-airtime-31"}
            first = self.client.post("/api/utility/buyairtime/", payload, content_type="application/json")
            second = self.client.post("/api/utility/buyairtime/", payload, content_type="application/json")
        self.assertEqual(first.status_code, 200, first.content)
        self.assertIn(second.status_code, (200, 409), second.content)
        self.assertEqual(get_or_create_wallet(self.user).balance, Decimal("400"))
        self.assertEqual(Transaction.objects.filter(user=self.user, direction=Transaction.OUT).count(), 1)

    def test_repeated_confirmation_never_replays_accepted_otp(self):
        self.accept_otp()
        with patch("utility.wema.validate_wallet_otp") as bank, \
                patch("utility.wema.get_account_details", return_value={"success": False}):
            result, status = complete_wema_provisioning(self.user, "123456", self.attempt.tracking_id)
        bank.assert_not_called()
        self.assertEqual(status, 202)
        self.assertFalse(result["otp_required"])

    def test_confirmation_reloads_acceptance_after_attempt_lock(self):
        # Model the waiting request acquiring its lock after another request
        # accepted the credential. The locked read, not an earlier snapshot,
        # decides whether the bank OTP needs to be submitted.
        select_for_update = WemaProvisioningAttempt.objects.select_for_update

        def acceptance_before_lock(*args, **kwargs):
            WemaProvisioningAttempt.objects.filter(pk=self.attempt.pk).update(otp_verified_at=timezone.now())
            return select_for_update(*args, **kwargs)

        with patch.object(WemaProvisioningAttempt.objects, "select_for_update", side_effect=acceptance_before_lock), \
                patch("utility.wema.validate_wallet_otp") as bank, \
                patch("utility.wema.get_account_details", return_value={"success": False}):
            result, status = complete_wema_provisioning(self.user, "123456", self.attempt.tracking_id)
        bank.assert_not_called()
        self.assertEqual(status, 202)
        self.assertFalse(result["otp_required"])

    def test_account_callback_alone_never_grants_identity(self):
        self.callback()
        self.user.refresh_from_db()
        self.assertFalse(self.user.bvn_verified)
        self.assertFalse(IdentityProof.objects.filter(user=self.user).exists())

    def test_duplicate_callback_records_one_proof(self):
        self.accept_otp()
        self.callback()
        self.callback()
        self.assertEqual(IdentityProof.objects.filter(user=self.user).count(), 1)

    def test_real_name_mismatch_never_rehydrates(self):
        self.accept_otp()
        self.callback("JOHN DOE")
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.status, WemaProvisioningAttempt.FAILED)
        self.assertEqual(rehydrate_verified_identity_flags(self.user), [])

    def test_missing_bank_name_is_retryable_without_using_wallet_name(self):
        self.accept_otp()
        self.callback("")
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.status, WemaProvisioningAttempt.PENDING)
        self.assertFalse(IdentityProof.objects.filter(user=self.user).exists())
        self.callback()
        self.user.refresh_from_db()
        self.assertTrue(self.user.bvn_verified)

    def test_polling_completes_identity_when_callback_name_lookup_failed(self):
        self.accept_otp()
        self.callback("")
        with patch("utility.wema.get_kyc_status", return_value={"success": True, "name": "ADA EZE"}), \
                patch("utility.wema.get_transactions", return_value={"success": True, "transactions": []}):
            call_command("reconcile_wema", stdout=StringIO(), stderr=StringIO())
        self.user.refresh_from_db()
        self.assertTrue(self.user.bvn_verified)

    def test_accepted_attempt_survives_original_otp_expiry(self):
        self.accept_otp()
        with patch("django.utils.timezone.now", return_value=self.attempt.expires_at + timedelta(minutes=1)):
            self.callback()
        self.user.refresh_from_db()
        self.assertTrue(self.user.bvn_verified)

    def test_successful_bank_response_after_deadline_preserves_valid_submission(self):
        submitted_at = self.attempt.expires_at - timedelta(seconds=1)
        with patch("django.utils.timezone.now", return_value=submitted_at) as clock:
            def delayed_accept(*args, **kwargs):
                clock.return_value = self.attempt.expires_at + timedelta(seconds=1)
                return {"success": True}

            with patch("utility.wema.validate_wallet_otp", side_effect=delayed_accept) as bank, \
                    patch("utility.wema.get_account_details", return_value={"success": False}):
                response = self.client.post("/api/kyc/bvn/confirm/", {
                    "access_token": self.token, "otp": "123456",
                    "tracking_id": self.attempt.tracking_id}, content_type="application/json")
            self.assertEqual(response.status_code, 202)
            self.attempt.refresh_from_db()
            self.assertEqual(self.attempt.otp_verified_at, submitted_at)
            bank.assert_called_once()
            self.callback()
        self.user.refresh_from_db()
        self.assertTrue(self.user.bvn_verified)
        self.assertTrue(IdentityProof.objects.filter(user=self.user, identity_type="bvn").exists())

    def test_invalid_acceptance_time_cannot_verify(self):
        self.attach()
        self.attempt.otp_verified_at = self.attempt.expires_at + timedelta(seconds=1)
        self.attempt.save(update_fields=["otp_verified_at"])
        self.assertEqual(finish_accepted_identity(self.attempt, holder_name="ADA EZE"), "ignored")

    def test_chat_and_app_do_not_restart_consumed_otp(self):
        self.accept_otp()
        with patch.object(router, "reply") as reply, patch.object(router, "_send_identity_flow") as flow:
            router._start_kyc(self.user, "2348030000031")
        flow.assert_not_called()
        self.assertIn("do not need to submit", reply.call_args.args[1])
        with patch("utility.wema.create_wallet_request") as bank:
            for path in ("/api/wallet/account/create/", "/api/kyc/bvn/start/"):
                response = self.client.post(path, {"access_token": self.token, "bvn": BVN},
                                            content_type="application/json")
                self.assertTrue(response.json()["pending"], response.content)
                self.assertFalse(response.json()["otp_required"])
        bank.assert_not_called()

    def test_unaccepted_attempt_is_never_verified_by_reconciliation(self):
        self.attach()
        with patch("utility.wema.get_kyc_status") as name, \
                patch("utility.wema.get_transactions", return_value={"success": True, "transactions": []}), \
                patch("utility.wema.lift_debit_restriction", return_value={"success": True}):
            call_command("reconcile_wema", stdout=StringIO(), stderr=StringIO())
        name.assert_not_called()
        self.assertFalse(IdentityProof.objects.filter(user=self.user).exists())

    def test_verified_identity_cannot_be_replaced(self):
        self.accept_otp()
        self.attach()
        self.user.bvn_hash = hash_identifier("33333333333")
        self.user.bvn_verified = True
        self.user.save(update_fields=["bvn_hash", "bvn_verified"])
        self.assertEqual(finish_accepted_identity(self.attempt, holder_name="ADA EZE"), "conflict")
        self.user.refresh_from_db()
        self.assertEqual(self.user.bvn_hash, hash_identifier("33333333333"))

    def test_callback_completion_exception_is_acknowledged_and_retryable(self):
        self.accept_otp()
        with patch("wallet.identity.finish_pending_identities", side_effect=RuntimeError("temporary")):
            self.assertEqual(self.callback().status_code, 200)
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.status, WemaProvisioningAttempt.PENDING)
