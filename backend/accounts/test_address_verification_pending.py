"""Tier 3 requires a completed bank address check, not submission acceptance."""

from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.views import verify_kyc_address
from wallet.models import Wallet
from wallet.services import get_or_create_wallet, sync_bank_tier


@override_settings(
    BANK_ACCOUNT_PROVIDER="partnership",
    WEMA_PARTNERSHIP_MODE="active",
    KYC_PROVIDER="wema",
    WEMA={"KEYS": {"wallet": "test-wallet", "upgrade": "test-upgrade"},
          "CHANNEL_ID": "test-channel", "SIMULATION": False},
)
class AddressVerificationPendingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="address-pending", phone="08038881234",
            email="address-pending@example.test", password="Test-only-password1!",
            email_verified=True, phone_verified=True, bvn_verified=True,
            nin_verified=True, face_verified=True, tier=2,
        )
        self.wallet = get_or_create_wallet(self.user)
        self.wallet.account_number = "0123456789"
        self.wallet.bank_tier = 2
        self.wallet.save(update_fields=["account_number", "bank_tier"])
        self.address = {"address": "12 Allen Avenue", "city": "Ikeja", "state": "Lagos"}
        # Isolate the shared submission service from KYC response enrichment.
        # Status refresh itself is exercised explicitly below.
        self.state = patch("accounts.views._kyc_state", return_value={})
        self.state.start()
        self.addCleanup(self.state.stop)
        self.live = patch("utility.wema.address_verify_live", return_value=True)
        self.live.start()
        self.addCleanup(self.live.stop)
        status_patch = patch("utility.wema.get_kyc_status", return_value={
            "success": True, "tier": "Tier 2", "address_verification": "Pending",
        })
        self.status = status_patch.start()
        self.addCleanup(status_patch.stop)

    def _mark_pending(self):
        self.user.address = "12 Allen Avenue, Ikeja, Lagos"
        self.user.address_verification_pending = True
        self.user.address_verification_requested_at = timezone.now()
        self.user.address_verification_account_number = self.wallet.account_number
        self.user.save(update_fields=[
            "address", "address_verification_pending", "address_verification_requested_at",
            "address_verification_account_number",
        ])

    def _assert_pending(self):
        self.user.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertTrue(self.user.address_verification_pending)
        self.assertIsNotNone(self.user.address_verification_requested_at)
        self.assertFalse(self.user.address_verified)
        self.assertEqual(self.user.tier, 2)
        self.assertEqual(self.wallet.bank_tier, 2)

    def _assert_verified(self):
        self.user.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertTrue(self.user.address_verified)
        self.assertFalse(self.user.address_verification_pending)
        self.assertEqual(self.user.tier, 3)
        self.assertEqual(self.wallet.bank_tier, 3)

    def _completed_result(self):
        return {"success": True, "tier": "Tier 3", "address_verification": "Completed"}

    def test_submission_is_durably_pending_before_bank_post(self):
        def inspect_submission(account_number, address):
            stored = get_user_model().objects.get(pk=self.user.pk)
            self.assertTrue(stored.address_verification_pending)
            self.assertIsNotNone(stored.address_verification_requested_at)
            self.assertEqual(stored.address_verification_account_number, self.wallet.account_number)
            self.assertIn("Allen Avenue", stored.address)
            self.assertFalse(stored.address_verified)
            self.assertEqual(account_number, self.wallet.account_number)
            self.assertIn("Allen Avenue", address["fullAddress"])
            return {"success": True}

        with patch("utility.wema.upgrade_tier3", side_effect=inspect_submission) as submit:
            response = verify_kyc_address(self.user, self.address)
        submit.assert_called_once()
        self.assertEqual(response.status_code, 202)
        self._assert_pending()

    def test_submission_acceptance_does_not_raise_either_tier(self):
        with patch("utility.wema.upgrade_tier3", return_value={"success": True}):
            response = verify_kyc_address(self.user, self.address)
        self.assertEqual(response.status_code, 202)
        self._assert_pending()

    def test_pending_submission_is_retained(self):
        with patch("utility.wema.upgrade_tier3", return_value={
                "success": False, "pending": True, "message": "Verification in progress"}):
            response = verify_kyc_address(self.user, self.address)
        self.assertEqual(response.status_code, 202)
        self._assert_pending()

    def test_transport_failure_keeps_durable_pending_for_later_recovery(self):
        # The provider wrapper returns this shape after a timeout; the bank may
        # already have accepted the request, so another POST is unsafe.
        with patch("utility.wema.upgrade_tier3", return_value={
                "success": False, "pending": False, "message": "Bank unavailable",
                "diagnostic": {"error_type": "ReadTimeout"}}):
            response = verify_kyc_address(self.user, self.address)
        self.assertEqual(response.status_code, 202)
        self._assert_pending()

    def test_repeated_pending_request_only_reads_status(self):
        self._mark_pending()
        requested_at = self.user.address_verification_requested_at
        with patch("utility.wema.upgrade_tier3") as submit:
            response = verify_kyc_address(self.user, self.address)
        self.assertEqual(response.status_code, 202)
        submit.assert_not_called()
        self.status.assert_called_once_with(self.wallet.account_number)
        self._assert_pending()
        self.assertEqual(self.user.address_verification_requested_at, requested_at)

    def test_duplicate_pending_request_can_complete_from_authenticated_readback(self):
        self._mark_pending()
        self.status.return_value = self._completed_result()
        with patch("utility.wema.upgrade_tier3") as submit:
            response = verify_kyc_address(self.user, self.address)
        self.assertEqual(response.status_code, 200)
        submit.assert_not_called()
        self._assert_verified()

    def test_completed_readback_promotes_pending_address(self):
        from wallet.address_verification import refresh_address_verification

        self._mark_pending()
        self.status.return_value = self._completed_result()
        self.assertTrue(refresh_address_verification(self.user, wallet=self.wallet))
        self.status.assert_called_once_with(self.wallet.account_number)
        self._assert_verified()

    def test_mock_failed_unknown_or_incomplete_readbacks_do_not_promote(self):
        from wallet.address_verification import refresh_address_verification

        self._mark_pending()
        results = [
            {"success": True, "mock": True, "tier": "Tier 3", "address_verification": "Completed"},
            {"success": False, "tier": "Tier 3", "address_verification": "Completed"},
            {"success": True, "tier": "Tier 2", "address_verification": "Completed"},
            {"success": True, "tier": "unknown", "address_verification": "Completed"},
            {"success": True, "tier": "Tier 3", "address_verification": "Pending"},
            {"success": True, "tier": "Tier 3", "address_verification": "unknown"},
            {"success": True, "tier": "Tier 3", "address_verification": ""},
            {"success": True, "tier": "Tier 3"},
        ]
        for result in results:
            with self.subTest(result=result):
                self.assertFalse(refresh_address_verification(
                    self.user, wallet=self.wallet, result=result))
                self._assert_pending()
        self.status.assert_not_called()

    def test_completed_bank_tier_alone_cannot_create_local_address_proof(self):
        from wallet.address_verification import refresh_address_verification

        self.assertFalse(refresh_address_verification(
            self.user, wallet=self.wallet, result=self._completed_result()))
        self.user.refresh_from_db()
        self.assertFalse(self.user.address_verified)
        self.assertEqual(self.user.tier, 2)

    def test_read_for_prior_address_cannot_complete_replaced_request(self):
        from wallet.address_verification import refresh_address_verification

        self._mark_pending()

        def complete_old_request(account_number):
            get_user_model().objects.filter(pk=self.user.pk).update(
                address="20 New Street, Ikeja, Lagos",
                address_verification_requested_at=timezone.now(),
            )
            return self._completed_result()

        self.status.side_effect = complete_old_request
        self.assertFalse(refresh_address_verification(self.user, wallet=self.wallet))
        self._assert_pending()
        self.assertEqual(self.user.address, "20 New Street, Ikeja, Lagos")

    def test_authenticated_address_rejection_allows_later_corrected_submission(self):
        from wallet.address_verification import refresh_address_verification

        self._mark_pending()
        self.status.return_value = {
            "success": True, "tier": "Tier 2", "address_verification": "Rejected",
        }
        self.assertFalse(refresh_address_verification(self.user, wallet=self.wallet))
        self.user.refresh_from_db()
        self.assertFalse(self.user.address_verification_pending)
        self.assertFalse(self.user.address_verified)
        self.assertEqual(self.user.tier, 2)

    def test_status_for_replaced_account_cannot_promote_current_account(self):
        from wallet.address_verification import refresh_address_verification

        self._mark_pending()
        Wallet.objects.filter(pk=self.wallet.pk).update(account_number="0987654321")
        # self.wallet is deliberately the stale object used to make the request.
        self.assertFalse(refresh_address_verification(
            self.user, wallet=self.wallet, result=self._completed_result()))
        self.user.refresh_from_db()
        self.assertFalse(self.user.address_verified)
        self.assertTrue(self.user.address_verification_pending)
        self.assertEqual(self.user.tier, 2)

    def test_replaced_account_is_not_queried_for_prior_accounts_pending_address(self):
        from wallet.address_verification import refresh_address_verification

        self._mark_pending()
        Wallet.objects.filter(pk=self.wallet.pk).update(account_number="0987654321")
        replacement = Wallet.objects.get(pk=self.wallet.pk)
        self.status.return_value = self._completed_result()
        self.assertFalse(refresh_address_verification(self.user, wallet=replacement))
        self.status.assert_not_called()
        self.user.refresh_from_db()
        self.assertFalse(self.user.address_verified)
        self.assertTrue(self.user.address_verification_pending)
        self.assertEqual(self.user.address_verification_account_number, "0123456789")

    def test_supplied_wallet_must_belong_to_pending_customer(self):
        from wallet.address_verification import refresh_address_verification

        self._mark_pending()
        other = get_user_model().objects.create_user(
            username="other-address", phone="08038881235", email="other-address@example.test")
        other_wallet = get_or_create_wallet(other)
        other_wallet.account_number = "0987654321"
        other_wallet.save(update_fields=["account_number"])
        self.assertFalse(refresh_address_verification(
            self.user, wallet=other_wallet, result=self._completed_result()))
        self._assert_pending()

    def test_bank_tier_sync_reuses_authenticated_read_for_address_completion(self):
        self._mark_pending()
        self.status.return_value = self._completed_result()
        self.assertEqual(sync_bank_tier(self.wallet), 3)
        self.status.assert_called_once_with(self.wallet.account_number)
        self._assert_verified()

    def test_authenticated_status_poll_completes_pending_address(self):
        from accounts.models import AccessToken

        self.state.stop()
        self._mark_pending()
        self.status.return_value = self._completed_result()
        token = AccessToken.issue(self.user).key
        response = self.client.post("/api/kyc/status/", {}, content_type="application/json",
                                    HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertTrue(body["address_verified"])
        self.assertFalse(body["address_verification_pending"])
        self.assertEqual(body["address_verification_state"], "verified")
        self.assertEqual(body["tier"], 3)
        self.assertEqual(body["bank_tier"], 3)
        self.status.assert_called_once_with(self.wallet.account_number)
        self._assert_verified()

    def test_authenticated_unknown_status_keeps_address_pending_without_generic_pending(self):
        from accounts.models import AccessToken

        self.state.stop()
        self._mark_pending()
        self.status.return_value = {"success": True, "tier": "Tier 2", "address_verification": "Unknown"}
        token = AccessToken.issue(self.user).key
        response = self.client.post("/api/kyc/status/", {}, content_type="application/json",
                                    HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertFalse(body["address_verified"])
        self.assertTrue(body["address_verification_pending"])
        self.assertEqual(body["address_verification_state"], "pending")
        self.assertNotIn("pending", body)
        self.assertFalse(body["identity_processing"])
        self.assertEqual(body["tier"], 2)
        self.status.assert_called_once_with(self.wallet.account_number)
        self._assert_pending()

    def test_reconciliation_only_refreshes_accounts_with_pending_addresses(self):
        self._mark_pending()
        self.wallet.pnd_lifted = True
        self.wallet.save(update_fields=["pnd_lifted"])
        other = get_user_model().objects.create_user(
            username="address-not-pending", phone="08038881236",
            email="address-not-pending@example.test")
        other_wallet = get_or_create_wallet(other)
        other_wallet.account_number = "0987654321"
        other_wallet.pnd_lifted = True
        other_wallet.save(update_fields=["account_number", "pnd_lifted"])
        self.status.return_value = self._completed_result()
        history = {"credited": 0, "windows": 1, "backlog": False,
                   "fetch_failed": False, "error_code": ""}
        command = "utility.management.commands.reconcile_wema"
        with patch(f"{command}.wema_provisioned_wallets",
                   return_value=[self.wallet, other_wallet]), \
                patch(f"{command}.reconcile_account_history", return_value=history):
            call_command("reconcile_wema", account_recovery_limit=0,
                         stdout=StringIO(), stderr=StringIO())
        self.status.assert_called_once_with(self.wallet.account_number)
        self._assert_verified()
        other.refresh_from_db()
        self.assertFalse(other.address_verified)
        self.assertFalse(other.address_verification_pending)

    def test_missing_bank_address_endpoint_cannot_fall_back_to_document_ocr(self):
        with patch("utility.wema.address_verify_live", return_value=False), \
                patch("accounts.views.kyc_verify_address", return_value={"success": True}) as ocr, \
                patch("utility.wema.upgrade_tier3") as submit:
            response = verify_kyc_address(self.user, {**self.address, "document": "ZmFrZQ=="})
        self.assertEqual(response.status_code, 503)
        ocr.assert_not_called()
        submit.assert_not_called()
        self.user.refresh_from_db()
        self.assertFalse(self.user.address_verified)
        self.assertFalse(self.user.address_verification_pending)

    def test_already_verified_request_is_idempotent(self):
        self.user.address_verified = True
        self.user.address = "12 Allen Avenue, Ikeja, Lagos"
        self.user.recompute_tier()
        self.user.save(update_fields=["address_verified", "address", "tier"])
        self.wallet.bank_tier = 3
        self.wallet.save(update_fields=["bank_tier"])
        with patch("utility.wema.upgrade_tier3") as submit:
            response = verify_kyc_address(self.user, self.address)
        self.assertEqual(response.status_code, 200)
        submit.assert_not_called()
        self._assert_verified()
