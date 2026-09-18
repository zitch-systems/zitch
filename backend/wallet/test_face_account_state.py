from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from accounts.models import AccessToken, User
from wallet.models import WemaFaceSession
from wallet.services import attach_existing_bank_account, get_or_create_wallet
from wallet.wema_callbacks import _record_face_account_outcome


class FaceAccountStateTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="issuance-test", phone="08011112222")
        self.session = WemaFaceSession.objects.create(user=self.user, state="issuance-test",
            identity_type="bvn", identity_hash="test-hash", status="verified",
            expires_at=timezone.now() + timedelta(minutes=15))

    def test_legacy_verified_is_not_creation_acceptance_and_alert_is_deduplicated(self):
        with mock.patch("utility.alerts.alert") as alert, mock.patch("utility.wema._post") as bank:
            for _ in range(2):
                wallet, detail = attach_existing_bank_account(self.user, using_bvn=True)
                self.assertIsNone(wallet)
                self.assertIn("requires review", detail)
            alert.assert_called_once()
            bank.assert_not_called()

    def test_accepted_creation_waits_for_callback_then_escalates(self):
        _record_face_account_outcome(self.session, {"success": True, "http_status": 200})
        with mock.patch("utility.alerts.alert") as alert:
            self.assertIn("accepted", attach_existing_bank_account(self.user)[1])
            alert.assert_not_called()
            WemaFaceSession.objects.filter(pk=self.session.pk).update(
                updated=timezone.now() - timedelta(hours=2))
            self.assertIn("requires review", attach_existing_bank_account(self.user)[1])
            alert.assert_called_once()

    def test_stored_outcome_has_no_raw_response_and_no_identity_change(self):
        _record_face_account_outcome(self.session, {"success": False,
            "account_state": "review_required", "failure_category": "existing_customer",
            "http_status": 400, "raw": {"bvn": "22222222222"}})
        self.session.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(self.session.account_state, "review_required")
        self.assertEqual(self.session.account_failure_category, "existing_customer")
        self.assertEqual(self.session.account_http_status, 400)
        self.assertFalse(self.user.bvn_verified)

    def test_existing_nuban_wins_over_stale_issuance_state(self):
        wallet = get_or_create_wallet(self.user)
        wallet.account_number = "0123456789"
        wallet.save(update_fields=["account_number"])
        with mock.patch("utility.alerts.alert") as alert:
            recovered, _ = attach_existing_bank_account(self.user)
            self.assertEqual(recovered.account_number, "0123456789")
            alert.assert_not_called()

    def test_status_distinguishes_verification_issuance_and_stalled_account(self):
        token = AccessToken.issue(self.user).key

        def status():
            response = self.client.post("/api/kyc/face/status/", {
                "access_token": token, "session": self.session.state},
                content_type="application/json")
            self.assertEqual(response.status_code, 200)
            return response.json()

        self.assertEqual(status()["account_setup_state"], "review_required")
        WemaFaceSession.objects.filter(pk=self.session.pk).update(status="pending")
        self.assertEqual(status()["account_setup_state"], "awaiting_verification")
        self.assertFalse(status()["account_review_required"])
        _record_face_account_outcome(self.session, {"success": True, "http_status": 200})
        self.assertEqual(status()["account_setup_state"], "awaiting_callback")
        WemaFaceSession.objects.filter(pk=self.session.pk).update(
            updated=timezone.now() - timedelta(hours=2))
        self.assertEqual(status()["account_setup_state"], "review_required")
        wallet = get_or_create_wallet(self.user)
        wallet.account_number = "0123456789"
        wallet.save(update_fields=["account_number"])
        self.assertEqual(status()["account_setup_state"], "ready")
