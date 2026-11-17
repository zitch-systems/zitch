"""Wema's Tier 2 account upgrade callback: /webhooks/wema/upgrade/<path key>.

Wema completes the Tier 2 upgrade at once and calls this URL with the result.
The callback only prompts: Tier 2 is granted from the bank's own
partner-account-kyc-status read-back, so a forged or garbled call raises no one.
A refusal stops the wait. The WhatsApp customer is told at once.
"""
import json
from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.models import User, hash_identifier
from wallet.models import Wallet
from wallet.tests import make_user
from wallet.views import _TIER2_PENDING_KEY
from whatsapp.models import PendingAction
from whatsapp.verification_web import ACTION_TYPE, COMPLETE, FACE_FAILED, FACE_PENDING

from .test_wema_callbacks import TOKEN, WEMA_CB

BVN = "22222222222"
ACCOUNT = "0123456789"
TIER_2 = {"success": True, "tier": "TIER_2", "name": "ADA EZE"}
TIER_1 = {"success": True, "tier": "Tier 1", "name": "ADA EZE"}


@override_settings(WEMA=WEMA_CB, PAYMENT_PROVIDER="wema")
class UpgradeCallbackTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.user, _ = make_user("08030000999", "upgradecb@zitch.app", identity_verified=False)
        User.objects.filter(pk=self.user.pk).update(
            bvn_verified=True, bvn_hash=hash_identifier(BVN), bvn_last4=BVN[-4:], tier=1,
            tier2_review="pending", tier2_review_at=timezone.now())
        self.user.refresh_from_db()
        Wallet.objects.filter(user=self.user).update(
            account_number=ACCOUNT, account_name="ADA EZE", bank_name="Wema Bank")
        cache.set(_TIER2_PENDING_KEY.format(self.user.pk), {"face": True}, 3600)
        self.pa = PendingAction.objects.create(
            user=self.user, msisdn="2348030000999", action_type=ACTION_TYPE, state=FACE_PENDING,
            payload={}, expires_at=timezone.now() + timezone.timedelta(hours=1))

    def _post(self, payload, token=TOKEN, bank=TIER_2):
        with patch("utility.wema.get_kyc_status", return_value=bank) as read, \
             patch("whatsapp.router.reply") as reply:
            r = self.client.post(f"/webhooks/wema/upgrade/{token}", data=json.dumps(payload),
                                 content_type="application/json")
        self.user.refresh_from_db()
        self.pa.refresh_from_db()
        return r, read, reply

    def test_a_successful_upgrade_is_applied_at_once_and_the_customer_told(self):
        r, read, reply = self._post({"accountNumber": ACCOUNT, "status": True,
                                     "message": "Upgrade successful", "accountTier": "Tier 2"})
        self.assertEqual(r.status_code, 200)
        read.assert_called()                     # decided by the bank's read-back
        self.assertTrue(self.user.face_verified)
        self.assertEqual(self.user.tier2_review, "")
        self.assertEqual(self.pa.state, COMPLETE)
        reply.assert_called_once()
        self.assertIsNone(cache.get(_TIER2_PENDING_KEY.format(self.user.pk)))

    def test_nested_under_data_is_read_too(self):
        r, _read, _reply = self._post({"data": {"nuban": ACCOUNT, "tier": "2"}, "successful": True})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(self.user.face_verified)

    def test_a_callback_claiming_success_cannot_raise_anyone_on_its_own(self):
        """The read-back still says Tier 1: nothing is granted, the wait goes on."""
        r, _read, reply = self._post({"accountNumber": ACCOUNT, "status": True,
                                      "accountTier": "Tier 2"}, bank=TIER_1)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(self.user.face_verified)
        self.assertEqual(self.user.tier2_review, "pending")
        self.assertEqual(self.pa.state, FACE_PENDING)
        reply.assert_not_called()

    def test_a_refusal_stops_the_wait_and_tells_the_customer(self):
        r, _read, reply = self._post({"accountNumber": ACCOUNT, "status": False,
                                      "message": "Face does not match BVN record"}, bank=TIER_1)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(self.user.face_verified)
        self.assertEqual(self.user.tier2_review, "rejected")
        self.assertEqual(self.pa.state, FACE_FAILED)
        reply.assert_called_once()
        self.assertIsNone(cache.get(_TIER2_PENDING_KEY.format(self.user.pk)))

    def test_an_unknown_account_is_acknowledged_and_changes_nothing(self):
        r, read, reply = self._post({"accountNumber": "9999999999", "status": True})
        self.assertEqual(r.status_code, 200)
        read.assert_not_called()
        reply.assert_not_called()
        self.assertFalse(self.user.face_verified)

    def test_a_wrong_path_key_is_refused(self):
        r, read, _reply = self._post({"accountNumber": ACCOUNT, "status": True}, token="wrong")
        self.assertEqual(r.status_code, 403)
        read.assert_not_called()
        self.assertFalse(self.user.face_verified)

    def test_the_customer_is_told_once_even_if_wema_calls_twice(self):
        self._post({"accountNumber": ACCOUNT, "status": True})
        _r, _read, reply = self._post({"accountNumber": ACCOUNT, "status": True})
        reply.assert_not_called()
