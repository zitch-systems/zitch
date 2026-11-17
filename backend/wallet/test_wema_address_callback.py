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
        self.assertIn("upgraded your account", reply.call_args.args[1])


@override_settings(WEMA=WEMA_CB, PAYMENT_PROVIDER="wema")
class AddressCallbackSentOnceTests(TestCase):
    """Wema, 17 Nov 2026: the webhook is sent once, never retried; a missed one is
    sent again only when Zitch asks. So nothing may depend on a second one."""

    _post = AddressCallbackTests._post

    def setUp(self):
        AddressCallbackTests.setUp(self)
        from django.core.cache import cache
        cache.clear()   # the read-back throttle is kept in the cache

    def _reconcile_refresh(self, bank):
        from wallet.address_verification import refresh_address_verification

        with patch("utility.wema.get_kyc_status", return_value=bank) as read, \
             patch("whatsapp.router.reply") as reply:
            refresh_address_verification(self.user, wallet=Wallet.objects.get(user=self.user))
        self.user.refresh_from_db()
        return read, reply

    def test_a_webhook_that_beats_the_bank_record_is_remembered_and_finished_later(self):
        self._post(wema_body(), bank=TIER_2)
        self.assertEqual(self.user.tier3_review, "bank_ok")
        self.assertFalse(self.user.address_verified)
        # The reconcile job's read-back while the record still lags: still waiting.
        self._reconcile_refresh(TIER_2)
        self.assertFalse(self.user.address_verified)
        # The record catches up: Tier 3, with no second webhook.
        from django.core.cache import cache
        cache.clear()
        read, _reply = self._reconcile_refresh(TIER_3)
        read.assert_called_once_with(ACCOUNT)
        self.assertTrue(self.user.address_verified)
        self.assertEqual((self.user.tier, self.user.tier3_review), (3, ""))

    def test_the_status_read_back_is_throttled_per_customer(self):
        self._post(wema_body(), bank=TIER_2)
        read, _ = self._reconcile_refresh(TIER_2)
        self.assertEqual(read.call_count, 1)
        read, _ = self._reconcile_refresh(TIER_2)
        read.assert_not_called()

    def test_without_the_webhook_the_bank_record_alone_never_grants_tier_3(self):
        read, _ = self._reconcile_refresh(TIER_3)   # tier3_review is only "pending"
        read.assert_not_called()
        self.assertFalse(self.user.address_verified)

    def test_a_webhook_passed_check_does_not_expire_or_invite_a_resend(self):
        from datetime import timedelta

        from accounts.views import tier3_claim_state

        self._post(wema_body(), bank=TIER_2)
        User.objects.filter(pk=self.user.pk).update(
            tier3_review_at=timezone.now() - timedelta(days=9),
            address_verification_requested_at=timezone.now() - timedelta(days=9))
        self.user.refresh_from_db()
        self.assertEqual(tier3_claim_state(self.user), "processing")

    def test_tier_3_from_the_bank_lifts_a_sign_up_hold(self):
        User.objects.filter(pk=self.user.pk).update(tier_cap=2)
        self._post(wema_body())
        self.assertEqual((self.user.tier, self.user.tier_cap), (3, 3))

    def test_an_address_that_passes_before_tier_2_does_not_claim_an_upgrade(self):
        from whatsapp.models import WhatsAppLink

        User.objects.filter(pk=self.user.pk).update(face_verified=False, tier=1)
        WhatsAppLink.objects.create(user=self.user, wa_msisdn="2348030001111",
                                    status=WhatsAppLink.ACTIVE)
        _r, _read, reply = self._post(wema_body())
        self.assertTrue(self.user.address_verified)
        self.assertEqual(self.user.tier, 1)
        self.assertNotIn("upgraded", reply.call_args.args[1])
        self.assertIn("earlier steps", reply.call_args.args[1])


class AddressWebhookWatchTests(TestCase):
    """The daily list of address checks to raise with Wema."""

    def setUp(self):
        self.user, _ = make_user(PHONE, "watch@zitch.app", identity_verified=False)

    def _run(self):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        with patch("utility.alerts.alert") as alert:
            call_command("address_webhook_watch", stdout=out)
        return alert, out.getvalue()

    def _waiting(self, days, review="pending"):
        from datetime import timedelta

        at = timezone.now() - timedelta(days=days)
        User.objects.filter(pk=self.user.pk).update(
            address="12 Allen Avenue, Ikeja, Lagos",
            address_verification_pending=True, address_verification_account_number=ACCOUNT,
            address_verification_requested_at=at, tier3_review=review, tier3_review_at=at)

    def test_nothing_waiting_sends_nothing(self):
        alert, out = self._run()
        alert.assert_not_called()
        self.assertIn("no_webhook=0", out)

    def test_a_check_with_no_webhook_after_two_days_is_listed_without_the_full_account(self):
        self._waiting(3)
        alert, _out = self._run()
        alert.assert_called_once()
        context = alert.call_args.kwargs
        self.assertEqual(context["level"], "error")
        self.assertEqual(context["no_webhook"], 1)
        self.assertIn(f"user {self.user.pk}", context["no_webhook_list"])
        self.assertIn("******6789", context["no_webhook_list"])
        self.assertNotIn(ACCOUNT, context["no_webhook_list"])
        self.assertNotIn("Allen", str(context))

    def test_a_recent_check_is_not_listed_yet(self):
        self._waiting(1)
        alert, _out = self._run()
        alert.assert_not_called()

    def test_a_webhook_passed_check_whose_record_lags_is_listed_separately(self):
        self._waiting(2, review="bank_ok")
        alert, _out = self._run()
        context = alert.call_args.kwargs
        self.assertEqual((context["no_webhook"], context["bank_record_behind"]), (0, 1))

    def test_it_runs_daily_in_the_scheduler(self):
        from utility import schedule

        self.assertEqual(schedule.job_named("address_webhook_watch").command,
                         ("address_webhook_watch",))


class LiftSignupHoldMigrationTests(TestCase):
    def test_held_customers_get_the_tier_their_checks_earn_and_none_is_lowered(self):
        import importlib

        from django.apps import apps

        lift = importlib.import_module("accounts.migrations.0039_lift_signup_tier_hold").lift_hold
        held, _ = make_user("08030002222", "held@zitch.app", identity_verified=False)
        User.objects.filter(pk=held.pk).update(
            bvn_verified=True, face_verified=True, email_verified=True, phone_verified=True,
            tier=1, tier_cap=1)
        unfinished, _ = make_user("08030003333", "unfinished@zitch.app", identity_verified=False)
        User.objects.filter(pk=unfinished.pk).update(
            bvn_verified=True, face_verified=True, email_verified=False, phone_verified=True,
            tier=0, tier_cap=1)
        lift(apps, None)
        held.refresh_from_db()
        unfinished.refresh_from_db()
        self.assertEqual((held.tier, held.tier_cap), (2, 3))
        self.assertEqual((unfinished.tier, unfinished.tier_cap), (0, 3))
