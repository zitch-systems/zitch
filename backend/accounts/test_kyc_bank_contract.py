"""Historical identity evidence and ownership boundaries."""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from accounts.models import (IdentityProof, User, hash_identifier,
                             record_identity_proof, rehydrate_verified_identity_flags)
from wallet.models import WemaFaceSession, WemaProvisioningAttempt
from wallet.services import get_or_create_wallet


BVN, NIN = "22222222222", "33333333333"


class TrustedIdentityRehydrationTests(TestCase):
    """Historical ownership is retained; it cannot grant a new-bank tier."""
    def setUp(self):
        self.user = User.objects.create_user(username="rehydrate", phone="08010000401",
            email_verified=True, phone_verified=True)
        self.wallet = get_or_create_wallet(self.user)
        self.wallet.account_number = "0123456789"
        self.wallet.save(update_fields=["account_number"])

    def test_attempt_status_with_existing_nuban_cannot_replace_ownership_proof(self):
        from accounts.views import _kyc_state

        for kind, raw in (("bvn", BVN), ("nin", NIN)):
            for status in (WemaProvisioningAttempt.PENDING, WemaProvisioningAttempt.FAILED,
                           WemaProvisioningAttempt.VERIFIED):
                WemaProvisioningAttempt.objects.create(user=self.user, identity_type=kind,
                    identity_hash=hash_identifier(raw), identity_last4=raw[-4:],
                    tracking_id=f"{kind}-{status}", status=status,
                    expires_at=timezone.now() + timedelta(minutes=10))
        state = _kyc_state(self.user)
        self.user.refresh_from_db()
        self.assertFalse(state["bvn_verified"] or state["nin_verified"])
        self.assertFalse(self.user.bvn_verified or self.user.nin_verified)
        self.assertEqual(self.user.tier, 0)
        self.assertEqual(self.user.bvn_hash, "")
        self.assertEqual(self.user.nin_hash, "")

    def test_completed_proofs_rehydrate_without_an_account_number(self):
        self.wallet.account_number = ""
        self.wallet.save(update_fields=["account_number"])
        record_identity_proof(self.user, IdentityProof.NIN, NIN, source=IdentityProof.WEMA_WALLET_OTP)
        rehydrate_verified_identity_flags(self.user)
        self.assertTrue(self.user.nin_verified)
        self.assertFalse(self.user.bvn_verified)
        self.assertEqual(self.user.nin_hash, hash_identifier(NIN))
        self.assertEqual(self.user.tier, 0)

    def test_verified_face_session_remains_valid_identity_evidence(self):
        WemaFaceSession.objects.create(user=self.user, state="verified-face", identity_type="nin",
            identity_hash=hash_identifier(NIN), status=WemaFaceSession.VERIFIED,
            correlation_id="verified-correlation", expires_at=timezone.now() - timedelta(minutes=1))
        rehydrate_verified_identity_flags(self.user)
        self.assertTrue(self.user.nin_verified)
        self.assertFalse(self.user.face_verified)
        self.assertEqual(self.user.tier, 0)

    def test_stale_tier_is_recomputed_without_repeating_verified_bvn(self):
        self.user.bvn_verified = True
        self.user.tier = 0
        self.user.save(update_fields=["bvn_verified", "tier"])
        rehydrate_verified_identity_flags(self.user)
        self.user.refresh_from_db()
        self.assertTrue(self.user.bvn_verified)
        self.assertEqual(self.user.tier, 0)

    def test_stale_nin_only_tier_is_recomputed_without_clearing_nin(self):
        self.user.nin_verified = True
        self.user.tier = 0
        self.user.save(update_fields=["nin_verified", "tier"])
        rehydrate_verified_identity_flags(self.user)
        self.user.refresh_from_db()
        self.assertTrue(self.user.nin_verified)
        self.assertFalse(self.user.bvn_verified)
        self.assertEqual(self.user.tier, 0)

    def test_stale_request_observes_verification_committed_by_callback(self):
        User.objects.filter(pk=self.user.pk).update(bvn_verified=True, tier=1)
        rehydrate_verified_identity_flags(self.user)
        self.assertTrue(self.user.bvn_verified)
        self.assertEqual(self.user.tier, 0)

    def test_unfinished_face_session_is_not_identity_evidence(self):
        WemaFaceSession.objects.create(user=self.user, state="pending-face", identity_type="nin",
            identity_hash=hash_identifier(NIN), expires_at=timezone.now() + timedelta(minutes=10))
        self.assertEqual(rehydrate_verified_identity_flags(self.user), [])
        self.assertFalse(self.user.nin_verified)

    def test_proof_for_another_value_cannot_verify_a_stored_identity(self):
        self.user.set_nin(NIN)
        self.user.save(update_fields=["nin_hash", "nin_last4"])
        record_identity_proof(self.user, IdentityProof.NIN, "44444444444", source=IdentityProof.WEMA_WALLET_OTP)
        self.assertEqual(rehydrate_verified_identity_flags(self.user), [])
        self.assertFalse(self.user.nin_verified)
        self.assertEqual(self.user.nin_hash, hash_identifier(NIN))

    def test_conflicting_ownership_does_not_leak_verified_flags_into_response(self):
        other = User.objects.create_user(username="rehydrate-owner", phone="08010000402")
        other.set_nin(NIN)
        other.save(update_fields=["nin_hash", "nin_last4"])
        record_identity_proof(self.user, IdentityProof.NIN, NIN, source=IdentityProof.WEMA_WALLET_OTP)
        self.assertEqual(rehydrate_verified_identity_flags(self.user), [])
        self.assertFalse(self.user.nin_verified)
        self.assertEqual(self.user.nin_hash, "")
        self.assertEqual(self.user.tier, 0)
        self.user.refresh_from_db()
        self.assertFalse(self.user.nin_verified)
