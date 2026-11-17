"""Wema's address verification (Tier 3) callback.

Wema, 17 Nov 2026: the callback is for address verification (the Tier 2 upgrade
is instant). It sends

    {title, message, data: {email, nuban, nubanName, type, nubanStatus,
     phoneNumber}, requestType: 2}

and expects back {title: "Account Upgraded Successfully", message: "Address has
been verified and account upgraded successfully ..."}. A failure arrives as
title "Address verification failed", message "Address verification failed for
customer with details below". Source IPs: 135.236.18.76 and 74.178.162.156.
"""
import json
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.models import User, hash_identifier
from wallet.models import Wallet
from wallet.tests import make_user
from wallet.wema_callbacks import ADDRESS_CB_RESPONSE

from .test_wema_callbacks import TOKEN, WEMA_CB

ACCOUNT = "0123456789"
PHONE = "08030001111"
TIER_3 = {"success": True, "tier": "Tier 3", "name": "JANE DOE"}
TIER_2 = {"success": True, "tier": "Tier 2", "name": "JANE DOE"}


def wema_body(title="Account Upgraded Successfully",
              message="Address has been verified and account upgraded successfully for the "
                      "account with details below", nuban=ACCOUNT):
    return {"title": title, "message": message,
            "data": {"email": "user@alat.ng", "nuban": nuban, "nubanName": "JANE DOE",
                     "type": 1, "nubanStatus": "Active", "phoneNumber": PHONE},
            "requestType": 2}


@override_settings(WEMA=WEMA_CB, PAYMENT_PROVIDER="wema")
class AddressCallbackTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user, _ = make_user(PHONE, "address@zitch.app", identity_verified=False)
        User.objects.filter(pk=self.user.pk).update(
            bvn_verified=True, bvn_hash=hash_identifier("22222222222"), bvn_last4="2222",
            face_verified=True, email_verified=True, phone_verified=True, tier=2, tier_cap=3,
            address="12 Allen Avenue, Ikeja, Lagos", address_verification_pending=True,
            address_verification_account_number=ACCOUNT, address_verification_requested_at=timezone.now(),
            tier3_review="pending", tier3_review_at=timezone.now())
        Wallet.objects.filter(user=self.user).update(
            account_number=ACCOUNT, account_name="JANE DOE", bank_name="Wema Bank")

    def _post(self, body, path="address", token=TOKEN, bank=TIER_3):
        with patch("utility.wema.get_kyc_status", return_value=bank) as read, \
             patch("whatsapp.router.reply") as reply:
            r = self.client.post(f"/webhooks/wema/{path}/{token}", data=json.dumps(body),
                                 content_type="application/json")
        self.user.refresh_from_db()
        return r, read, reply

    def test_a_verified_address_lifts_the_customer_to_tier_3_and_answers_as_wema_asked(self):
        r, read, _reply = self._post(wema_body())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), ADDRESS_CB_RESPONSE)
        read.assert_called_once_with(ACCOUNT)        # confirmed by the bank's own record
        self.assertTrue(self.user.address_verified)
        self.assertFalse(self.user.address_verification_pending)
        self.assertEqual(self.user.tier3_review, "")
        self.assertEqual(self.user.tier, 3)
        self.assertEqual(Wallet.objects.get(user=self.user).bank_tier, 3)

    def test_it_also_works_when_wema_sends_it_to_the_account_opening_url(self):
        r, _read, _reply = self._post(wema_body(), path="account")
        self.assertEqual(r.json(), ADDRESS_CB_RESPONSE)
        self.assertTrue(self.user.address_verified)

    def test_the_callback_alone_cannot_grant_tier_3(self):
        """The bank's record still says Tier 2: nothing is granted, the wait goes on."""
        r, _read, _reply = self._post(wema_body(), bank=TIER_2)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(self.user.address_verified)
        self.assertTrue(self.user.address_verification_pending)
        self.assertEqual(self.user.tier, 2)

    def test_no_address_waiting_means_nothing_is_granted(self):
        User.objects.filter(pk=self.user.pk).update(address_verification_pending=False, tier3_review="")
        r, read, _reply = self._post(wema_body())
        self.assertEqual(r.status_code, 200)
        read.assert_not_called()
        self.assertFalse(self.user.address_verified)

    def test_a_failed_verification_stops_the_wait_so_the_address_can_be_sent_again(self):
        r, read, _reply = self._post(wema_body(title="Address Verification Failed",
                                               message="Address could not be verified"))
        self.assertEqual(r.status_code, 200)
        read.assert_not_called()
        self.assertFalse(self.user.address_verified)
        self.assertFalse(self.user.address_verification_pending)
        self.assertEqual(self.user.tier3_review, "rejected")

    def test_wemas_own_failure_callback_stops_the_wait(self):
        """Wema, 17 Nov 2026: the failed address verification callback."""
        r, read, _reply = self._post(wema_body(
            title="Address verification failed",
            message="Address verification failed for customer with details below"))
        self.assertEqual(r.status_code, 200)
        read.assert_not_called()
        self.assertFalse(self.user.address_verified)
        self.assertFalse(self.user.address_verification_pending)
        self.assertEqual(self.user.tier3_review, "rejected")
        self.assertEqual(self.user.tier, 2)

    def test_wemas_failure_callback_on_the_account_url_is_handled_too(self):
        r, _read, _reply = self._post(wema_body(
            title="Address verification failed",
            message="Address verification failed for customer with details below"), path="account")
        self.assertEqual(r.json(), ADDRESS_CB_RESPONSE)
        self.assertEqual(self.user.tier3_review, "rejected")

    def test_an_unknown_account_changes_nothing(self):
        r, read, _reply = self._post(wema_body(nuban="9999999999"))
        self.assertEqual(r.status_code, 200)
        read.assert_not_called()
        self.assertFalse(self.user.address_verified)

    def test_a_wrong_path_key_is_refused(self):
        r, read, _reply = self._post(wema_body(), token="wrong")
        self.assertEqual(r.status_code, 403)
        read.assert_not_called()
        self.assertFalse(self.user.address_verified)

    def test_the_customer_is_told_in_whatsapp(self):
        from whatsapp.models import WhatsAppLink

        WhatsAppLink.objects.create(user=self.user, wa_msisdn="2348030001111",
                                    status=WhatsAppLink.ACTIVE)
        _r, _read, reply = self._post(wema_body())
        reply.assert_called_once()
        self.assertIn("Address verified", reply.call_args.args[1])
