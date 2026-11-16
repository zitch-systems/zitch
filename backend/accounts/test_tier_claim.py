"""Sign-up sends the Tier 2 details to the bank; Verify Tier 2 applies them later.

Review, Nov 2026: the customer gives BVN, NIN, a face check and the address at
sign-up and everything goes to the bank at once, but they stay on Tier 1 until
they tap Verify Tier 2 on Account limits. Then: a check the bank already
confirmed upgrades them at once, one it is still running shows Processing, and
one it refused (or that was never sent) asks for the NIN and face check again.
"""
import json
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.models import User, hash_identifier
from wallet import selfie_fixtures as fx
from wallet.models import Wallet
from wallet.tests import make_user

BVN, NIN = "22222222222", "12345678901"
BANK_TIER_1 = {"success": True, "tier": "Tier 1", "name": "ADA EZE"}
BANK_TIER_2 = {"success": True, "tier": "Tier 2", "name": "ADA EZE"}


@override_settings(PAYMENT_PROVIDER="wema")
class TierClaimTests(TestCase):
    def setUp(self):
        cache.clear()   # pending checks are remembered in the cache by user id
        self.client = Client()
        # Where sign-up leaves a customer: BVN verified (Tier 1), account open.
        self.user, self.token = make_user("08030000777", "claim@zitch.app", identity_verified=False)
        User.objects.filter(pk=self.user.pk).update(
            bvn_verified=True, bvn_hash=hash_identifier(BVN), bvn_last4=BVN[-4:], tier=1)
        self.user.refresh_from_db()
        wallet = Wallet.objects.get(user=self.user)
        wallet.account_number, wallet.account_name, wallet.bank_name = "0123456789", "ADA EZE", "Wema Bank"
        wallet.save(update_fields=["account_number", "account_name", "bank_name"])

    def _post(self, path, payload=None):
        return self.client.post(path, data=json.dumps({**(payload or {}), "access_token": self.token}),
                                content_type="application/json")

    def _signup_send(self, upgrade, bank_state):
        with patch("utility.wema.upgrade_tier2", return_value=upgrade), \
             patch("utility.wema.get_kyc_status", return_value=bank_state), fx.detector():
            return self._post("/api/wallet/wema/upgrade-tier2/", {
                "bvn": BVN, "nin": NIN, "live_image": fx.selfie_b64(), "at_signup": True})

    def _claim(self, tier=2, bank_state=BANK_TIER_1):
        with patch("utility.wema.get_kyc_status", return_value=bank_state):
            return self._post("/api/kyc/claim/", {"tier": tier})

    def test_a_check_the_bank_confirmed_at_sign_up_waits_for_verify_then_applies_at_once(self):
        r = self._signup_send({"success": True, "raw": {"message": "ok"}}, BANK_TIER_2)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["tier2_state"], "confirmed")
        self.user.refresh_from_db()
        self.assertTrue(self.user.face_verified)
        self.assertTrue(self.user.nin_verified)
        self.assertEqual(self.user.tier, 1, "still Tier 1 until the customer asks")
        with patch("utility.wema.get_kyc_status", return_value=BANK_TIER_2):
            status = self._post("/api/kyc/status/").json()
        self.assertEqual(status["tier"], 1)
        self.assertEqual(status["tier2_state"], "ready")

        r = self._claim(bank_state=BANK_TIER_2)
        self.assertEqual(r.json()["state"], "upgraded")
        self.assertEqual(r.json()["tier"], 2)
        self.user.refresh_from_db()
        self.assertEqual(self.user.tier, 2)

    def test_a_check_still_with_the_bank_is_processing_until_the_bank_confirms_it(self):
        r = self._signup_send({"success": False, "pending": True}, BANK_TIER_1)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["tier2_state"], "pending")
        self.user.refresh_from_db()
        self.assertEqual(self.user.tier2_review, "pending")

        with patch("utility.wema.upgrade_tier2") as resent:
            r = self._claim(bank_state=BANK_TIER_1)
        self.assertEqual(r.json()["state"], "processing")
        resent.assert_not_called()   # a claim only reads the bank back

        r = self._claim(bank_state=BANK_TIER_2)
        self.assertEqual(r.json()["state"], "upgraded")
        self.user.refresh_from_db()
        self.assertEqual(self.user.tier, 2)
        self.assertEqual(self.user.tier2_review, "")

    def test_a_check_the_bank_refused_asks_for_the_nin_and_face_again(self):
        r = self._signup_send({"success": False, "message": "Face does not match"}, BANK_TIER_1)
        self.assertEqual(r.status_code, 200, "sign-up carries on; the customer is still on Tier 1")
        self.assertEqual(r.json()["tier2_state"], "rejected")
        r = self._claim()
        self.assertEqual(r.json()["state"], "not_confirmed")
        self.user.refresh_from_db()
        self.assertEqual(self.user.tier, 1)

    def test_a_check_with_no_answer_for_two_days_is_offered_again(self):
        User.objects.filter(pk=self.user.pk).update(
            tier2_review="pending", tier2_review_at=timezone.now() - timedelta(hours=49))
        self.assertEqual(self._claim().json()["state"], "not_confirmed")

    def test_nothing_sent_at_sign_up_asks_for_the_details(self):
        self.assertEqual(self._claim().json()["state"], "not_confirmed")

    def test_an_upgrade_asked_for_outside_sign_up_applies_at_once(self):
        User.objects.filter(pk=self.user.pk).update(tier_cap=1)   # left over from sign-up
        with patch("utility.wema.upgrade_tier2", return_value={"success": True, "raw": {"message": "ok"}}), \
             patch("utility.wema.get_kyc_status", return_value=BANK_TIER_2), fx.detector():
            r = self._post("/api/wallet/wema/upgrade-tier2/", {"bvn": BVN, "nin": NIN, "live_image": fx.selfie_b64()})
        self.assertEqual(r.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.tier, 2)
        self.assertEqual(self.user.tier_cap, 3)

    ADDRESS = {"house_number": "12", "street": "Allen Avenue", "state": "Lagos", "lga": "Ikeja"}

    def _send_tier3(self, result):
        with patch("utility.wema.upgrade_tier3", return_value=result) as sent:
            r = self._post("/api/kyc/tier3/send/", self.ADDRESS)
        return r, sent

    def test_the_address_goes_to_the_bank_as_its_own_tier_3_request(self):
        r, sent = self._send_tier3({"success": False, "pending": True})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["tier3_state"], "processing")
        sent.assert_called_once()
        account, address = sent.call_args.args
        self.assertEqual(account, "0123456789")
        self.assertEqual(address["street"], "Allen Avenue")
        self.user.refresh_from_db()
        self.assertEqual(self.user.address, "12 Allen Avenue, Ikeja, Lagos")
        self.assertTrue(self.user.address_verification_pending)
        self.assertFalse(self.user.address_verified, "Zitch never raises Tier 3 from the bank's answer")
        self.assertEqual(self._claim(tier=3).json()["state"], "processing")

    def test_even_a_bank_success_leaves_tier_3_processing(self):
        User.objects.filter(pk=self.user.pk).update(face_verified=True, tier=2)
        r, _ = self._send_tier3({"success": True})
        self.assertEqual(r.json()["tier3_state"], "processing")
        self.user.refresh_from_db()
        self.assertEqual(self.user.tier, 2)
        self.assertFalse(self.user.address_verified)

    def test_an_address_the_bank_refused_can_be_entered_and_sent_again(self):
        r, _ = self._send_tier3({"success": False, "message": "Address not verified"})
        self.assertEqual(r.json()["tier3_state"], "not_confirmed")
        self.assertEqual(self._claim(tier=3).json()["state"], "not_confirmed")
        r, sent = self._send_tier3({"success": False, "pending": True})
        sent.assert_called_once()
        self.assertEqual(r.json()["tier3_state"], "processing")

    def test_a_request_with_the_bank_is_not_sent_twice(self):
        self._send_tier3({"success": False, "pending": True})
        r, sent = self._send_tier3({"success": True})
        sent.assert_not_called()
        self.assertFalse(r.json()["sent"])

    def test_without_an_account_number_the_address_is_only_saved(self):
        Wallet.objects.filter(user=self.user).update(account_number="")
        r, sent = self._send_tier3({"success": True})
        sent.assert_not_called()
        self.assertEqual(r.json()["tier3_state"], "not_sent")
        self.user.refresh_from_db()
        self.assertEqual(self.user.address, "12 Allen Avenue, Ikeja, Lagos")

    def test_an_address_verified_another_way_lifts_the_sign_up_cap(self):
        User.objects.filter(pk=self.user.pk).update(face_verified=True, nin_verified=True, tier_cap=2, tier=2,
                                                   address_verified=True)
        r = self._claim(tier=3)
        self.assertEqual(r.json()["state"], "upgraded")
        self.user.refresh_from_db()
        self.assertEqual(self.user.tier, 3)
        self.assertEqual(self.user.tier_cap, 3)

    def test_a_verified_address_still_waits_for_tier_2(self):
        User.objects.filter(pk=self.user.pk).update(tier_cap=1, address_verified=True)
        r = self._claim(tier=3)
        self.assertEqual(r.json()["state"], "needs_tier2")
        self.user.refresh_from_db()
        self.assertEqual(self.user.tier, 1)

    def test_tier_3_with_nothing_sent_waits_for_tier_2(self):
        self.assertEqual(self._claim(tier=3).json()["state"], "needs_tier2")

    def test_a_bad_tier_is_refused(self):
        self.assertEqual(self._claim(tier="x").status_code, 400)
        self.assertEqual(self._claim(tier=4).status_code, 400)


class TierCapTests(TestCase):
    def test_existing_accounts_keep_every_tier_they_earned(self):
        user, _ = make_user("08030000778", "cap@zitch.app")
        user.face_verified = True
        user.recompute_tier()
        self.assertEqual(user.tier, 2)

    def test_a_cap_holds_a_confirmed_tier_but_never_takes_tier_1(self):
        user, _ = make_user("08030000779", "cap1@zitch.app")
        user.face_verified, user.tier_cap = True, 1
        user.recompute_tier()
        self.assertEqual(user.tier, 1)
        user.tier_cap = 0
        user.recompute_tier()
        self.assertEqual(user.tier, 1)
        user.bvn_verified = user.nin_verified = False
        user.recompute_tier()
        self.assertEqual(user.tier, 0)
