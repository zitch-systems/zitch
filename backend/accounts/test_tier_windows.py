"""How long a bank Tier 2 / Tier 3 check is waited for, and what counts as Tier 2.

Wema's Account Upgrade documentation names no callback, no rejected state and
no list of ``accountTier`` values (reviewed 17 Nov 2026). So Zitch reads the
tier back, accepts any label that names the tier, and gives the bank 24 hours
for Tier 2 and five days for Tier 3 before reporting "not confirmed".
"""
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User, hash_identifier
from accounts.views import TIER2_REVIEW_PATIENCE, TIER3_REVIEW_PATIENCE, send_tier3_address, \
    tier2_claim_state, tier3_claim_state
from wallet.models import Wallet
from wallet.tests import make_user
from wallet.views import _TIER2_PENDING_SECONDS, confirm_wema_tier2

BVN = "22222222222"


@override_settings(PAYMENT_PROVIDER="wema")
class TierWindowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user, _ = make_user("08030000888", "windows@zitch.app", identity_verified=False)
        User.objects.filter(pk=self.user.pk).update(
            bvn_verified=True, bvn_hash=hash_identifier(BVN), bvn_last4=BVN[-4:], tier=1)
        self.user.refresh_from_db()
        Wallet.objects.filter(user=self.user).update(
            account_number="0123456789", account_name="ADA EZE", bank_name="Wema Bank")

    def test_the_windows(self):
        self.assertEqual(_TIER2_PENDING_SECONDS, 24 * 3600)
        self.assertEqual(TIER2_REVIEW_PATIENCE, timedelta(hours=24))
        self.assertEqual(TIER3_REVIEW_PATIENCE, timedelta(days=5))

    def test_tier2_is_confirmed_whatever_form_the_bank_writes_it_in(self):
        for label in ("TIER_2", "Level 2", "Tier Two", "tier-3", 2):
            with self.subTest(label=label):
                User.objects.filter(pk=self.user.pk).update(face_verified=False, tier=1)
                self.user.refresh_from_db()
                with patch("utility.wema.get_kyc_status",
                           return_value={"success": True, "tier": label, "name": "ADA EZE"}):
                    r = confirm_wema_tier2(self.user)
                self.assertEqual(r.status_code, 200, label)
                self.user.refresh_from_db()
                self.assertTrue(self.user.face_verified)

    def test_tier1_or_an_unknown_label_stays_pending(self):
        for label in ("Tier 1", "unclassified", ""):
            with self.subTest(label=label):
                with patch("utility.wema.get_kyc_status",
                           return_value={"success": True, "tier": label, "name": "ADA EZE"}):
                    self.assertEqual(confirm_wema_tier2(self.user).status_code, 202)
                self.user.refresh_from_db()
                self.assertFalse(self.user.face_verified)

    def test_tier2_processing_for_24_hours_then_not_confirmed(self):
        now = timezone.now()
        User.objects.filter(pk=self.user.pk).update(
            tier2_review="pending", tier2_review_at=now - timedelta(hours=23))
        self.user.refresh_from_db()
        self.assertEqual(tier2_claim_state(self.user), "processing")
        User.objects.filter(pk=self.user.pk).update(tier2_review_at=now - timedelta(hours=25))
        self.user.refresh_from_db()
        self.assertEqual(tier2_claim_state(self.user), "not_confirmed")

    def test_tier3_processing_for_five_days_then_not_confirmed_and_can_be_sent_again(self):
        now = timezone.now()
        User.objects.filter(pk=self.user.pk).update(
            address_verification_pending=True, tier3_review="pending",
            tier3_review_at=now - timedelta(days=4, hours=23))
        self.user.refresh_from_db()
        self.assertEqual(tier3_claim_state(self.user), "processing")
        with patch("utility.wema.upgrade_tier3") as send:
            sent, _state, _ = send_tier3_address(self.user, "12", "Allen Avenue", "Lagos", "Ikeja")
        self.assertFalse(sent)
        send.assert_not_called()

        User.objects.filter(pk=self.user.pk).update(tier3_review_at=now - timedelta(days=5, hours=1))
        self.user.refresh_from_db()
        self.assertEqual(tier3_claim_state(self.user), "not_confirmed")
        with patch("utility.wema.upgrade_tier3", return_value={"success": False, "pending": True}) as send:
            sent, state, _ = send_tier3_address(self.user, "12", "Allen Avenue", "Lagos", "Ikeja")
        self.assertTrue(sent)
        self.assertEqual(state, "processing")
        send.assert_called_once()
        self.user.refresh_from_db()
        self.assertEqual(tier3_claim_state(self.user), "processing")
