"""Partnership ownership choices and interrupted account setup in WhatsApp."""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from wallet.models import WemaFaceSession, WemaProvisioningAttempt
from wallet.services import get_or_create_wallet
from . import router
from .flows import ACCOUNT_OTP, FLOW_ID_STATE, _submit_identity
from .models import PendingAction
from .test_face_kyc import FACE_ON, MSISDN, VERIFIED_BVN, _user


@override_settings(WEMA=FACE_ON, BANK_ACCOUNT_PROVIDER="partnership", WEMA_PARTNERSHIP_MODE="active")
class PartnershipJourneyTests(TestCase):
    def setUp(self):
        self.user = _user()
        self.user.bvn_verified = self.user.nin_verified = False
        self.user.bvn_hash = self.user.nin_hash = ""
        self.user.save(update_fields=["bvn_verified", "nin_verified", "bvn_hash", "nin_hash"])
        self.enterContext(patch.object(router, "reply"))

    def action(self, *, state="bvn", **payload):
        return PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="add_account", state=state,
            payload=payload, expires_at=router._flow_deadline(state))

    def test_explicit_face_method_opens_bank_face_without_sending_sms(self):
        for kind in ("bvn", "nin"):
            with self.subTest(kind=kind):
                pa = self.action(id_type=kind, id_kind=kind, verification_method="face")
                with patch.object(router, "send_cta_url", return_value={"success": True}) as cta, \
                     patch.object(router.wallet_views, "_start_wema_attempt") as start:
                    result = _submit_identity(pa, {"number": VERIFIED_BVN})
                start.assert_not_called()
                cta.assert_called_once()
                self.assertNotIn(VERIFIED_BVN, cta.call_args.args[1])
                self.assertIn("secure face-verification link", result["data"]["message"])
                self.assertEqual(WemaFaceSession.objects.filter(user=self.user, identity_type=kind).count(), 1)
                self.assertFalse(PendingAction.objects.filter(pk=pa.pk).exists())
        self.user.refresh_from_db()
        self.assertFalse(self.user.bvn_verified)
        self.assertFalse(self.user.nin_verified)

    def test_failed_face_choice_does_not_silently_send_sms(self):
        pa = self.action(id_type="nin", verification_method="face")
        with patch.object(router, "_send_identity_face_option", return_value=False), \
             patch.object(router.wallet_views, "_start_wema_attempt") as start:
            result = router._account_submit_identity(pa, self.user, MSISDN, VERIFIED_BVN)
        self.assertEqual(result, "fail")
        start.assert_not_called()

    def test_face_command_while_secure_bank_code_open_uses_same_nin_route(self):
        pa = self.action(state=FLOW_ID_STATE, id_kind=ACCOUNT_OTP, using_bvn=False,
                         tracking_id="nin-pending")
        with patch.object(router, "_send_identity_flow", return_value=True) as secure, \
             patch.object(router.wema_provider, "resend_wallet_otp") as resend:
            router._advance(pa, self.user, MSISDN, "face")
        secure.assert_called_once_with(pa, "nin", fallback_state=router.FACE_ID_STATE)
        resend.assert_not_called()
        pa.refresh_from_db()
        self.assertEqual(pa.payload["id_purpose"], "account_face")
        self.assertEqual(pa.payload["id_type"], "nin")

    def test_unverified_profile_resumes_existing_nin_code_without_reentering_identity(self):
        WemaProvisioningAttempt.objects.create(
            user=self.user, identity_type="nin", tracking_id="existing-nin-code",
            status=WemaProvisioningAttempt.PENDING,
            expires_at=timezone.now() + timedelta(minutes=20))
        with patch.object(router.wallet_views, "_wema_funding_enabled", return_value=True), \
             patch.object(router.wema_provider, "resend_wallet_otp", return_value={"success": True}) as resend, \
             patch.object(router, "_send_account_otp_flow", return_value=True), \
             patch.object(router.wallet_views, "_start_wema_attempt") as start:
            router._start_add_account(self.user, MSISDN)
        resend.assert_called_once_with(self.user.phone, "existing-nin-code", bvn=False)
        start.assert_not_called()
        pa = PendingAction.objects.get(user=self.user)
        self.assertEqual(pa.payload["id_type"], "nin")
        self.assertEqual(pa.payload["tracking_id"], "existing-nin-code")

    def test_completed_nin_face_without_account_keeps_issuance_review(self):
        self.user.nin_verified = True
        self.user.save(update_fields=["nin_verified"])
        WemaFaceSession.objects.create(
            user=self.user, identity_type="nin", status=WemaFaceSession.VERIFIED,
            state="completed-face", expires_at=timezone.now() + timedelta(minutes=20))
        with patch.object(router.wallet_views, "_wema_funding_enabled", return_value=True), \
             patch.object(router, "attach_existing_bank_account", return_value=(None, "missing")) as recover, \
             patch.object(router, "_send_identity_flow") as identity:
            router._start_add_account(self.user, MSISDN)
        recover.assert_called_once_with(self.user, using_bvn=False)
        identity.assert_not_called()
        self.assertFalse(PendingAction.objects.filter(user=self.user).exists())

    def test_adopted_nin_account_without_proof_continues_real_identity_verification(self):
        pa = self.action(id_type="nin", verification_method="sms")

        def adopt(*args, **kwargs):
            wallet = get_or_create_wallet(self.user)
            wallet.account_number = "0100000456"
            wallet.save(update_fields=["account_number", "updated"])
            return wallet

        with patch.object(router.wallet_views, "_start_wema_attempt", return_value=({"success": False}, "")), \
             patch.object(router.wallet_views, "_adopt_existing_wema_account", side_effect=adopt), \
             patch.object(router, "reply_buttons") as buttons:
            result = router._account_submit_identity(pa, self.user, MSISDN, VERIFIED_BVN)
        self.assertEqual(result, "adopted")
        self.assertIn(("use_nin", "Use NIN instead"), buttons.call_args.args[2])
        self.assertEqual(PendingAction.objects.get(user=self.user).state, router.BVN_METHOD_STATE)
        self.user.refresh_from_db()
        self.assertFalse(self.user.bvn_verified)
        self.assertFalse(self.user.nin_verified)

    def test_nin_only_customer_can_start_secure_pin_reset(self):
        self.user.nin_verified = True
        self.user.save(update_fields=["nin_verified"])
        with patch.object(router, "flows_live", return_value=True), \
             patch.object(router, "send_flow", return_value={"success": True}) as secure:
            router._start_pin_reset(self.user, MSISDN)
        secure.assert_called_once()
        self.assertEqual(PendingAction.objects.get(user=self.user).action_type, "setpin")

    def test_accepted_otp_cannot_be_replaced_by_stale_face_submission(self):
        pa = self.action(id_type="nin", verification_method="face")
        with patch("wallet.identity.accepted_identity_pending", return_value=True), \
             patch.object(router, "_send_identity_face_option") as face, \
             patch.object(router.wallet_views, "_start_wema_attempt") as start:
            result = _submit_identity(pa, {"number": VERIFIED_BVN})
        face.assert_not_called()
        start.assert_not_called()
        self.assertIn("Account setup is processing", result["data"]["message"])
        self.assertFalse(PendingAction.objects.filter(pk=pa.pk).exists())

    def test_bank_history_review_shows_funding_and_stops_spend_without_kyc_loop(self):
        wallet = get_or_create_wallet(self.user)
        # history_review = has_account and ..., so this state implies has_account,
        # and customer_funding_account returns can_fund = has_account. Without it
        # the mock describes a state the real function never produces.
        funding = {"provider": "partnership", "account_setup_state": "bank_history_review",
                   "has_account": True, "can_fund": True,
                   "account_number": "0100000456", "bank_name": "Partner bank", "account_name": "Ada",
                   "spending_available": False, "transfers_available": False, "bill_payments_available": False,
                   "migration_message": "Your account balance needs review before you can spend."}
        with patch.object(router, "customer_funding_account", return_value=funding), \
             patch.object(router, "reply") as reply, \
             patch.object(router, "bank_spend_error", return_value=funding["migration_message"]), \
             patch.object(router, "_start_kyc") as identity:
            router._send_account_details(MSISDN, wallet)
            self.assertIn("0100000456", reply.call_args.args[1])
            self.assertIn("balance needs review", reply.call_args.args[1])
            router._start_transfer(self.user, MSISDN)
            self.assertIn(funding["migration_message"], reply.call_args.args[1])
        identity.assert_not_called()
        self.assertFalse(PendingAction.objects.filter(user=self.user).exists())

    def test_stale_identity_cannot_start_face_after_individual_migration_hold(self):
        pa = self.action(id_type="nin", verification_method="face")
        with patch("utility.providers.partnership_new_business_allowed", return_value=False), \
             patch.object(router, "send_cta_url") as send:
            offered = router._send_identity_face_option(
                pa, self.user, MSISDN, "nin", VERIFIED_BVN, account_setup=True)
        self.assertFalse(offered)
        send.assert_not_called()
        self.assertFalse(WemaFaceSession.objects.filter(user=self.user).exists())

    def test_stale_face_link_cannot_restart_after_accepted_otp(self):
        pa = self.action(id_type="nin", id_purpose="account_face")
        with patch("wallet.identity.accepted_identity_pending", return_value=True), \
             patch.object(router, "send_cta_url") as send:
            offered = router._send_identity_face_option(
                pa, self.user, MSISDN, "nin", VERIFIED_BVN, account_setup=True)
        self.assertFalse(offered)
        send.assert_not_called()
        self.assertFalse(WemaFaceSession.objects.filter(user=self.user).exists())

    def test_stale_face_form_closes_with_processing_status_after_accepted_otp(self):
        pa = self.action(id_type="nin", id_purpose="account_face")
        with patch("wallet.identity.accepted_identity_pending", return_value=True), \
             patch.object(router, "send_cta_url") as send:
            result = _submit_identity(pa, {"number": VERIFIED_BVN})
        send.assert_not_called()
        self.assertIn("complete or processing", result["data"]["message"])
        self.assertFalse(PendingAction.objects.filter(pk=pa.pk).exists())

    def _tier2_user(self, address="12 Allen Avenue, Ikeja, Lagos"):
        self.user.bvn_verified = self.user.nin_verified = self.user.face_verified = True
        self.user.address = address
        self.user.recompute_tier()
        self.user.save()
        wallet = get_or_create_wallet(self.user)
        wallet.account_number = "0123456789"
        wallet.save(update_fields=["account_number"])

    def test_a_pending_tier3_tap_says_pending_and_sends_nothing_again(self):
        """17 Nov 2026: the tap answers success, pending or rejected - never a second send."""
        self._tier2_user()
        type(self.user).objects.filter(pk=self.user.pk).update(
            address_verification_pending=True, tier3_review="pending", tier3_review_at=timezone.now())
        self.user.refresh_from_db()
        with patch("wallet.address_verification.refresh_address_verification"), \
             patch.object(router, "reply_buttons") as buttons:
            router._offer_tier_upgrade(self.user, MSISDN)
        self.assertIn(("tier3", "Upgrade to Tier 3"), buttons.call_args.args[2])
        with patch("utility.wema.upgrade_tier3") as sent, patch.object(router, "reply") as reply:
            router.handle_inbound(MSISDN, "tier3")
        sent.assert_not_called()
        text = reply.call_args.args[1]
        self.assertIn("Tier 3 is pending", text)
        self.assertNotIn("Wema", text)
        self.assertNotIn("bank", text)

    def test_a_refused_tier3_tap_says_not_approved_and_sends_nothing_again(self):
        self._tier2_user()
        type(self.user).objects.filter(pk=self.user.pk).update(tier3_review="rejected")
        self.user.refresh_from_db()
        with patch.object(router, "reply_buttons") as buttons:
            router._offer_tier_upgrade(self.user, MSISDN)
        self.assertIn(("tier3", "Upgrade to Tier 3"), buttons.call_args.args[2])
        with patch("utility.wema.upgrade_tier3") as sent, \
             patch("whatsapp.verification_web.start_verification", return_value="https://x/v/t") as page, \
             patch("whatsapp.providers.send_cta_url", return_value={"success": True}) as cta:
            router.handle_inbound(MSISDN, "tier3")
        sent.assert_not_called()
        # The refusal opens the Tier 3 page, where the address is entered again.
        page.assert_called_once_with(self.user, MSISDN, 3)
        msisdn, body, url = cta.call_args.args
        self.assertEqual((msisdn, body, url), (MSISDN, router.TIER3_REJECTED_TEXT, "https://x/v/t"))
        self.assertEqual(cta.call_args.kwargs["cta"], "Enter address")
        self.assertIn("could not be verified", body)
        self.assertNotIn("Wema", body)

    def test_tier3_from_chat_offers_the_saved_address_by_area_only_and_sends_it(self):
        self._tier2_user()
        with patch.object(router, "reply_buttons") as buttons, \
             patch("whatsapp.verification_web.start_verification") as portal:
            router._offer_tier_upgrade(self.user, MSISDN)
        text, choices = buttons.call_args.args[1], buttons.call_args.args[2]
        self.assertIn(("tier3", "Upgrade to Tier 3"), choices)
        self.assertIn("in Ikeja, Lagos", text)
        # the street and house number stay out of the chat
        self.assertNotIn("Allen Avenue", text)
        self.assertNotIn("12 ", text.split("Upgrade to Tier 3")[-1])
        portal.assert_not_called()
        with patch("utility.wema.upgrade_tier3", return_value={"success": False, "pending": True}) as sent, \
             patch.object(router, "reply") as reply:
            router.handle_inbound(MSISDN, "tier3")
        sent.assert_called_once()
        account, address = sent.call_args.args
        self.assertEqual(account, "0123456789")
        self.assertEqual((address["buildingNumber"], address["street"], address["lga"], address["state"]),
                         ("12", "Allen Avenue", "Ikeja", "Lagos"))
        self.assertIn("Tier 3 is pending", reply.call_args.args[1])
        self.user.refresh_from_db()
        self.assertTrue(self.user.address_verification_pending)
        self.assertFalse(self.user.address_verified, "the bank's answer never raises Tier 3")
        self.assertEqual(self.user.tier, 2)

    def test_tier3_from_chat_without_a_usable_address_points_to_the_app(self):
        for address, review in (("", ""), ("Somewhere", "")):
            with self.subTest(address=address, review=review):
                self._tier2_user(address)
                type(self.user).objects.filter(pk=self.user.pk).update(tier3_review=review)
                self.user.refresh_from_db()
                with patch.object(router, "reply") as reply, \
                     patch.object(router, "reply_buttons") as buttons, \
                     patch("utility.wema.upgrade_tier3") as sent:
                    router._offer_tier_upgrade(self.user, MSISDN)
                buttons.assert_not_called()
                sent.assert_not_called()
                self.assertIn("Account limits, then Verify Tier 3", reply.call_args.args[1])

    def test_a_tier_3_address_the_bank_verified_applies_on_the_chat_tap(self):
        """Zitch's own limiter: verified at the bank, applied only when the customer taps."""
        self._tier2_user()
        type(self.user).objects.filter(pk=self.user.pk).update(address_verified=True, tier_cap=2, tier=2)
        self.user.refresh_from_db()
        with patch.object(router, "reply_buttons") as buttons:
            router._offer_tier_upgrade(self.user, MSISDN)
        self.assertIn(("tier3", "Upgrade to Tier 3"), buttons.call_args.args[2])
        self.user.refresh_from_db()
        self.assertEqual(self.user.tier, 2, "nothing applies before the tap")
        with patch("utility.wema.upgrade_tier3") as sent, patch.object(router, "reply") as reply:
            router.handle_inbound(MSISDN, "tier3")
        sent.assert_not_called()
        self.assertIn("Tier 3 verification complete", reply.call_args.args[1])
        self.user.refresh_from_db()
        self.assertEqual(self.user.tier, 3)

    def test_tier2_from_chat_applies_a_bank_confirmed_sign_up_check_without_a_new_photo(self):
        self._tier2_user()
        type(self.user).objects.filter(pk=self.user.pk).update(tier_cap=1, tier=1)
        self.user.refresh_from_db()
        router.PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="kyc", state=router.KYC_UPGRADE_STATE,
            payload={}, expires_at=router._flow_deadline("idle"))
        with patch.object(router, "reply") as reply, \
             patch("whatsapp.verification_web.face_link") as link:
            router.handle_inbound(MSISDN, "tier2")
        link.assert_not_called()
        self.assertIn("You're now Tier 2", reply.call_args.args[1])
        self.user.refresh_from_db()
        self.assertEqual(self.user.tier, 2)

    def test_tier2_from_chat_says_processing_for_a_sign_up_check_the_bank_still_has(self):
        self._tier2_user()
        type(self.user).objects.filter(pk=self.user.pk).update(
            face_verified=False, tier=1, tier2_review="pending", tier2_review_at=timezone.now())
        self.user.refresh_from_db()
        router.PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="kyc", state=router.KYC_UPGRADE_STATE,
            payload={}, expires_at=router._flow_deadline("idle"))
        with patch.object(router, "reply") as reply, \
             patch("whatsapp.verification_web.face_link") as link:
            router.handle_inbound(MSISDN, "tier2")
        link.assert_not_called()
        self.assertIn("Tier 2 is processing", reply.call_args.args[1])
        self.user.refresh_from_db()
        self.assertEqual(self.user.tier, 1)

    def test_disabled_new_tier3_checks_do_not_downgrade_previously_verified_customer(self):
        self.user.bvn_verified = self.user.nin_verified = self.user.face_verified = True
        self.user.address_verified = True
        self.user.recompute_tier()
        self.user.save()
        with patch("wallet.address_verification.tier3_address_capability", return_value={
                "tier3_address_available": False, "tier3_address_unavailable_reason": "Contact support."}), \
             patch.object(router, "reply") as reply:
            router._offer_tier_upgrade(self.user, MSISDN)
        self.assertIn("highest verification tier", reply.call_args.args[1])
        self.user.refresh_from_db()
        self.assertTrue(self.user.address_verified)
        self.assertEqual(self.user.tier, 3)
