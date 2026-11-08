"""Uncorrelated bank account status cannot verify a customer's address request."""

import json
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import AccessToken
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
    ADDRESS_FIELDS = (
        "address", "address_verified", "address_verification_pending",
        "address_verification_requested_at", "address_verification_account_number", "tier",
    )

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="address-pending", phone="08038881234",
            email="address-pending@example.test", password="Test-only-password1!",
            email_verified=True, phone_verified=True, bvn_verified=True,
            nin_verified=True, face_verified=True, tier=2,
            address="Previous unverified profile address",
        )
        self.wallet = get_or_create_wallet(self.user)
        self.wallet.account_number = "0123456789"
        self.wallet.bank_tier = 2
        self.wallet.save(update_fields=["account_number", "bank_tier"])
        self.address = {"address": "12 Allen Avenue", "city": "Ikeja", "state": "Lagos"}
        self.state = patch("accounts.views._kyc_state", return_value={})
        self.state.start()
        self.addCleanup(self.state.stop)
        live = patch("utility.wema.address_verify_live", return_value=True)
        live.start()
        self.addCleanup(live.stop)
        status = patch("utility.wema.get_kyc_status", return_value=self._completed_result())
        self.status = status.start()
        self.addCleanup(status.stop)
        submit = patch("utility.wema.upgrade_tier3", return_value={"success": True})
        self.submit = submit.start()
        self.addCleanup(submit.stop)

    def _snapshot(self):
        self.user.refresh_from_db()
        return {field: getattr(self.user, field) for field in self.ADDRESS_FIELDS}

    def _mark_pending(self):
        self.user.address = "12 Allen Avenue, Ikeja, Lagos"
        self.user.address_verification_pending = True
        self.user.address_verification_requested_at = timezone.now()
        self.user.address_verification_account_number = self.wallet.account_number
        self.user.save(update_fields=list(self.ADDRESS_FIELDS))
        return self._snapshot()

    def _mark_verified(self):
        self.user.address_verified = True
        self.user.address = "12 Allen Avenue, Ikeja, Lagos"
        self.user.recompute_tier()
        self.user.save(update_fields=["address_verified", "address", "tier"])
        self.wallet.bank_tier = 3
        self.wallet.save(update_fields=["bank_tier"])
        return self._snapshot()

    @staticmethod
    def _completed_result():
        return {"success": True, "tier": "Tier 3", "address_verification": "Completed"}

    def _assert_no_address_provider_call(self):
        self.status.assert_not_called()
        self.submit.assert_not_called()

    def _authenticated_status(self):
        self.state.stop()
        token = AccessToken.issue(self.user).key
        response = self.client.post("/api/kyc/status/", {}, content_type="application/json",
                                    HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_new_tier3_capability_is_explicitly_unavailable(self):
        from wallet.address_verification import tier3_address_capability

        capability = tier3_address_capability()
        self.assertIs(capability["tier3_address_available"], False)
        self.assertIsInstance(capability["tier3_address_unavailable_reason"], str)
        self.assertTrue(capability["tier3_address_unavailable_reason"].strip())
        self._assert_no_address_provider_call()

    def test_new_submission_is_unavailable_before_mutation_or_provider_call(self):
        before = self._snapshot()
        # Even a bank account already at Tier 3 is not evidence for this address.
        self.wallet.bank_tier = 3
        self.wallet.save(update_fields=["bank_tier"])
        response = verify_kyc_address(self.user, self.address)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(json.loads(response.content)["code"], "address_verification_unavailable")
        self.assertEqual(self._snapshot(), before)
        self._assert_no_address_provider_call()

    def test_missing_bank_endpoint_cannot_fall_back_to_document_ocr(self):
        before = self._snapshot()
        with patch("utility.wema.address_verify_live", return_value=False), \
                patch("accounts.views.kyc_verify_address", return_value={"success": True}) as ocr:
            response = verify_kyc_address(self.user, {**self.address, "document": "ZmFrZQ=="})
        self.assertEqual(response.status_code, 503)
        ocr.assert_not_called()
        self.assertEqual(self._snapshot(), before)
        self._assert_no_address_provider_call()

    def test_partnership_kyc_provider_or_new_business_gate_cannot_open_document_bypass(self):
        before = self._snapshot()
        for provider, new_business_allowed in (("prembly", True), ("wema", False), ("prembly", False)):
            with self.subTest(provider=provider, new_business_allowed=new_business_allowed), \
                    override_settings(KYC_PROVIDER=provider), \
                    patch("utility.providers.partnership_new_business_allowed", return_value=new_business_allowed), \
                    patch("accounts.views.kyc_verify_address", return_value={"success": True}) as ocr:
                response = verify_kyc_address(self.user, {**self.address, "document": "ZmFrZQ=="})
                self.assertEqual(response.status_code, 503, response.content)
                self.assertEqual(json.loads(response.content)["code"], "address_verification_unavailable")
                ocr.assert_not_called()
                self.assertEqual(self._snapshot(), before)
                self._assert_no_address_provider_call()

    def test_duplicate_pending_request_preserves_original_intent_without_provider_call(self):
        before = self._mark_pending()
        response = verify_kyc_address(self.user, {
            "address": "20 Changed Street", "city": "Abuja", "state": "FCT",
        })
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self._snapshot(), before)
        self._assert_no_address_provider_call()

    def test_uncorrelated_rejection_does_not_release_pending_request_for_resubmission(self):
        before = self._mark_pending()
        self.status.return_value = {
            "success": True, "tier": "Tier 2", "address_verification": "Rejected",
        }
        response = verify_kyc_address(self.user, self.address)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self._snapshot(), before)
        self._assert_no_address_provider_call()

    def test_uncorrelated_results_neither_promote_nor_clear_pending_addresses(self):
        from wallet.address_verification import refresh_address_verification

        before = self._mark_pending()
        for status in ("Completed", "Verified", "Approved", "Successful", "Rejected", "Pending", "unknown", ""):
            for mock in (False, True):
                with self.subTest(status=status, mock=mock):
                    result = {"success": True, "mock": mock, "tier": "Tier 3",
                              "address_verification": status}
                    self.assertFalse(refresh_address_verification(
                        self.user, wallet=self.wallet, result=result))
                    self.assertEqual(self._snapshot(), before)
        self._assert_no_address_provider_call()

    def test_invented_request_correlation_fields_do_not_authorize_address_completion(self):
        from wallet.address_verification import refresh_address_verification

        before = self._mark_pending()
        forged = {**self._completed_result(), "accountNumber": self.wallet.account_number,
                  "address": self.user.address,
                  "requestedAt": self.user.address_verification_requested_at.isoformat(),
                  "requestId": "uncontracted-request-id", "authenticated": True}
        self.assertFalse(refresh_address_verification(self.user, wallet=self.wallet, result=forged))
        self.assertEqual(self._snapshot(), before)
        self._assert_no_address_provider_call()

    def test_refresh_without_result_only_restores_durable_local_state(self):
        from wallet.address_verification import refresh_address_verification

        before = self._mark_pending()
        # Transient in-memory values must not become address proof.
        self.user.address_verified = True
        self.user.address_verification_pending = False
        self.user.tier = 3
        self.assertFalse(refresh_address_verification(self.user, wallet=self.wallet))
        self.assertFalse(self.user.address_verified)
        self.assertTrue(self.user.address_verification_pending)
        self.assertEqual(self.user.tier, 2)
        self.assertEqual(self._snapshot(), before)
        self._assert_no_address_provider_call()

    def test_unrequested_address_cannot_be_granted_from_bank_tier3(self):
        from wallet.address_verification import refresh_address_verification

        before = self._snapshot()
        self.assertFalse(refresh_address_verification(
            self.user, wallet=self.wallet, result=self._completed_result()))
        self.assertEqual(self._snapshot(), before)
        self._assert_no_address_provider_call()

    def test_replaced_account_cannot_rebind_or_complete_prior_address_request(self):
        from wallet.address_verification import refresh_address_verification

        before = self._mark_pending()
        Wallet.objects.filter(pk=self.wallet.pk).update(account_number="0987654321")
        replacement = Wallet.objects.get(pk=self.wallet.pk)
        self.assertFalse(refresh_address_verification(
            self.user, wallet=replacement, result=self._completed_result()))
        self.assertEqual(self._snapshot(), before)
        self._assert_no_address_provider_call()

    def test_existing_verified_address_is_preserved_by_refresh(self):
        from wallet.address_verification import refresh_address_verification

        before = self._mark_verified()
        self.assertTrue(refresh_address_verification(
            self.user, wallet=self.wallet,
            result={"success": True, "tier": "Tier 2", "address_verification": "Rejected"}))
        self.assertEqual(self._snapshot(), before)
        self._assert_no_address_provider_call()

    def test_existing_verified_submission_remains_idempotent(self):
        before = self._mark_verified()
        response = verify_kyc_address(self.user, self.address)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._snapshot(), before)
        self._assert_no_address_provider_call()

    def test_bank_tier_sync_updates_bank_tier_without_granting_local_address_proof(self):
        before = self._mark_pending()
        self.assertEqual(sync_bank_tier(self.wallet), 3)
        self.status.assert_called_once_with(self.wallet.account_number)
        self.submit.assert_not_called()
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.bank_tier, 3)
        self.assertEqual(self._snapshot(), before)

    def test_authenticated_status_exposes_unavailable_capability_and_preserves_pending(self):
        before = self._mark_pending()
        body = self._authenticated_status()
        self.assertFalse(body["tier3_address_available"])
        self.assertTrue(body["tier3_address_unavailable_reason"])
        self.assertFalse(body["address_verified"])
        self.assertTrue(body["address_verification_pending"])
        self.assertEqual(body["address_verification_state"], "pending")
        self.assertNotIn("pending", body)
        self.assertFalse(body["identity_processing"])
        self.assertEqual(body["tier"], 2)
        self.assertEqual(self._snapshot(), before)
        self._assert_no_address_provider_call()

    def test_authenticated_status_preserves_historical_verification(self):
        before = self._mark_verified()
        body = self._authenticated_status()
        self.assertFalse(body["tier3_address_available"])
        self.assertTrue(body["address_verified"])
        self.assertFalse(body["address_verification_pending"])
        self.assertEqual(body["address_verification_state"], "verified")
        self.assertEqual(body["tier"], 3)
        self.assertEqual(self._snapshot(), before)
        self._assert_no_address_provider_call()

    def test_reconciliation_does_not_use_uncorrelated_address_status(self):
        before = self._mark_pending()
        self.wallet.pnd_lifted = True
        self.wallet.save(update_fields=["pnd_lifted"])
        history = {"credited": 0, "windows": 1, "backlog": False,
                   "fetch_failed": False, "error_code": ""}
        command = "utility.management.commands.reconcile_wema"
        with patch(f"{command}.wema_provisioned_wallets", return_value=[self.wallet]), \
                patch(f"{command}.reconcile_account_history", return_value=history):
            call_command("reconcile_wema", account_recovery_limit=0,
                         stdout=StringIO(), stderr=StringIO())
        self.assertEqual(self._snapshot(), before)
        self._assert_no_address_provider_call()
