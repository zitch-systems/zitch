"""Session ownership and evidence gates; all provider traffic is mocked."""
from copy import deepcopy
from datetime import timedelta
from unittest.mock import Mock, patch

import requests
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import AccessToken, IdentityProof, PremblyFaceSession, User, record_identity_proof
from utility.liveness import start_tier2_liveness, verify_tier2_liveness
from wallet.services import get_or_create_wallet

CONFIG = {"API_KEY": "secret-test", "FACE_WIDGET_KEY": "public-test", "FACE_WIDGET_ID": "face-only",
          "FACE_SDK_ENABLED": True, "ORGANISATION_ID": "org-test"}


@override_settings(PREMBLY=CONFIG, RATELIMIT_ENABLE=False)
class PremblySessionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="live-face", phone="08011110000",
            email="face@example.test", email_verified=True, phone_verified=True, bvn_verified=True, tier=1)
        self.user.set_bvn("12345678901")
        self.user.save()
        record_identity_proof(self.user, "bvn", "12345678901", source=IdentityProof.WEMA_FACE)
        self.token = AccessToken.issue(self.user).key
        self.enterContext(patch("requests.sessions.Session.request", side_effect=AssertionError("Unexpected network")))

    def start(self):
        response = Mock(status_code=201)
        response.json.return_value = {"status": True, "data": {"session": {"session_id": "sdk_session_12345"}}}
        with patch("utility.liveness.requests.post", return_value=response) as call:
            result = start_tier2_liveness(self.user)
        self.assertTrue(result["success"], result)
        self.assertEqual(call.call_args.kwargs["json"]["metadata"]["zitch_action"], result["session"])
        self.assertNotIn("nin", call.call_args.kwargs["json"])
        return result, call

    def attestation(self):
        session = PremblyFaceSession.objects.get(user=self.user)
        return {"status": True, "data": {"session_id": "sdk_session_12345",
            "widget_config": {"id": "face-only", "enable_face_scan": True,
                "use_gesture_liveness": True, "metadata": {"image_comparison": True}},
            "status": "completed", "overall_result": "passed",
            "completed_at": timezone.now().isoformat(), "failure_reason": None,
            "metadata": {"zitch_action": str(session.state), "purpose": "tier2_liveness"},
            "verification_data": {"number": "12345678901"},
            "liveness": {"enabled": True, "status": "completed", "is_live": True,
                         "process_id": "live-provider-123", "error": None},
            "face_scan_result": {"comparison_result": [{"result": {
                "status": True, "response_code": "00", "confidence": 99.9}}]}}}

    def verify(self, session, body=None, user=None):
        response = Mock(status_code=200)
        response.json.return_value = self.attestation() if body is None else body
        with patch("utility.liveness.requests.get", return_value=response) as call:
            result = verify_tier2_liveness(user or self.user, {"session": session})
        return result, call

    def test_bvn_only_live_pass_promotes_local_tier_without_nin_or_bank_upgrade(self):
        start, _ = self.start()
        wallet = get_or_create_wallet(self.user)
        wallet.bank_tier = 1
        wallet.save()
        result, call = self.verify(start["session"])
        self.assertTrue(result["success"], result)
        self.user.refresh_from_db(); wallet.refresh_from_db()
        self.assertEqual(self.user.tier, 2)
        self.assertTrue(self.user.bvn_verified and self.user.face_verified)
        self.assertFalse(self.user.nin_verified)
        self.assertEqual(wallet.bank_tier, 1)
        self.assertEqual(call.call_args.kwargs["headers"]["x-api-key"], "secret-test")
        with patch("utility.liveness.requests.get") as again:
            self.assertTrue(verify_tier2_liveness(self.user, {"session": start["session"]})["success"])
        again.assert_not_called()

    def test_start_reuses_pending_session_without_second_charge(self):
        start, _ = self.start()
        with patch("utility.liveness.requests.post") as provider:
            self.assertEqual(start_tier2_liveness(self.user)["session"], start["session"])
        provider.assert_not_called()

    def test_client_claims_and_other_customers_session_never_grant_verification(self):
        start, _ = self.start()
        other = User.objects.create_user(username="other", phone="08011110001")
        result, call = self.verify(start["session"], user=other)
        self.assertFalse(result["success"]); call.assert_not_called()
        with patch("utility.liveness.requests.get") as provider:
            result = verify_tier2_liveness(self.user, {"session_id": "sdk_session_12345", "success": True,
                "live_image": "fake", "status": "verified"})
        self.assertFalse(result["success"]); provider.assert_not_called()

    def test_incomplete_wrong_workflow_and_photo_only_results_fail_closed(self):
        start, _ = self.start()
        alterations = [("status", "in_progress"), ("overall_result", "failed"),
            ("failure_reason", "data_verification_failed"), ("completed_at", None),
            ("widget_config", {}), ("widget_config", "invalid"), ("liveness", {}),
            ("session_id", "someone-else"), ("metadata", {}), ("face_scan_result", {}),
            ("verification_data", {"number": "11111111111"}),
            ("verification_data", {"number": "12345678901", "data": {"bvn": "11111111111"}})]
        for key, value in alterations:
            with self.subTest(key=key, value=value):
                body = deepcopy(self.attestation()); body["data"][key] = value
                result, _ = self.verify(start["session"], body)
                self.assertFalse(result["success"])
                self.user.refresh_from_db(); self.assertFalse(self.user.face_verified)
        body = self.attestation(); body["data"]["face_scan_result"]["comparison_result"][0]["result"]["confidence"] = True
        self.assertFalse(self.verify(start["session"], body)[0]["success"])

    def test_liveness_requires_true_live_result_and_unchanged_action(self):
        start, _ = self.start()
        for field, value in (("enabled", False), ("status", "in_progress"), ("is_live", "true"),
                             ("is_live", False), ("process_id", None), ("error", "failed")):
            with self.subTest(field=field, value=value):
                body = self.attestation(); body["data"]["liveness"][field] = value
                self.assertFalse(self.verify(start["session"], body)[0]["success"])
        body = self.attestation(); body["data"]["metadata"]["zitch_action"] = "wrong-action"
        self.assertFalse(self.verify(start["session"], body)[0]["success"])
        body = self.attestation(); body["data"]["widget_config"]["use_gesture_liveness"] = False
        self.assertFalse(self.verify(start["session"], body)[0]["success"])
        body = self.attestation(); body["data"]["widget_config"]["metadata"]["image_comparison"] = False
        self.assertFalse(self.verify(start["session"], body)[0]["success"])

    def test_expiry_and_changed_identity_or_credentials_revoke_session(self):
        start, _ = self.start()
        original = self.user.bvn_hash
        self.user.bvn_hash = "different"
        self.assertEqual(self.verify(start["session"])[0]["status"], "expired")
        self.user.bvn_hash = original
        self.user.password = "changed"
        self.assertEqual(self.verify(start["session"])[0]["status"], "expired")
        self.user.refresh_from_db()
        PremblyFaceSession.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.verify(start["session"])[0]["status"], "expired")

    def test_identity_change_while_provider_responds_cannot_grant_tier(self):
        start, _ = self.start()
        def changed(*args, **kwargs):
            User.objects.filter(pk=self.user.pk).update(bvn_verified=False)
            return Mock(status_code=200, json=Mock(return_value=self.attestation()))
        with patch("utility.liveness.requests.get", side_effect=changed):
            result = verify_tier2_liveness(self.user, {"session": start["session"]})
        self.assertFalse(result["success"])
        self.user.refresh_from_db(); self.assertFalse(self.user.face_verified)

    def test_revoked_web_grant_and_legacy_bank_images_cannot_use_session(self):
        start, _ = self.start()
        response = Mock(status_code=200, json=Mock(return_value=self.attestation()))
        with patch("utility.liveness.requests.get", return_value=response):
            result = verify_tier2_liveness(self.user, {"session": start["session"]}, authorize=lambda: False)
        self.assertFalse(result["success"])
        self.user.refresh_from_db(); self.assertFalse(self.user.face_verified)
        result = verify_tier2_liveness(self.user, {"session": start["session"], "live_image": "arbitrary-image"})
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "bank_image_contract_unavailable")

    def test_timeout_does_not_duplicate_session_or_promote_tier(self):
        with patch("utility.liveness.requests.post", side_effect=requests.Timeout):
            self.assertTrue(start_tier2_liveness(self.user)["pending"])
        with patch("utility.liveness.requests.post") as provider:
            self.assertTrue(start_tier2_liveness(self.user)["pending"])
        provider.assert_not_called()
        self.assertEqual(PremblyFaceSession.objects.count(), 1)
        self.user.refresh_from_db(); self.assertEqual(self.user.tier, 1)

    def test_missing_durable_identity_proof_cannot_start(self):
        IdentityProof.objects.all().delete()
        with patch("utility.liveness.requests.post") as provider:
            self.assertFalse(start_tier2_liveness(self.user)["success"])
        provider.assert_not_called()
