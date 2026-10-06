"""VAS contact/identity verification is independent of account allocation."""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from whatsapp import flows, router, vas_identity
from whatsapp.models import PendingAction, WaOnboarding, WhatsAppLink
from whatsapp.test_flows import MSISDN, _make_user


@override_settings(BANK_ACCOUNT_PROVIDER="wema_vas", WEMA_PARTNERSHIP_MODE="archive")
class VasOnboardingFlowTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.user.bvn_verified = False
        self.user.bvn_hash = ""
        self.user.save(update_fields=["bvn_verified", "bvn_hash"])
        self.funding = {"provider": "wema_vas", "has_account": False, "available": False,
                        "enrollment_available": False, "test_mode": True,
                        "migration_message": "Your test account invitation is pending."}

    def contact_action(self):
        from django.contrib.auth.hashers import make_password
        self.user.email_verified = False
        self.user.save(update_fields=["email_verified"])
        pa = PendingAction.objects.create(user=self.user, msisdn=MSISDN, action_type="kyc",
            state="idle", payload={"id_kind": "email", "id_step": "code",
                "code_hash": make_password("572938"), "code_attempts": 0,
                "code_target": router._email_challenge_target(self.user),
                "code_exp": (timezone.now() + timedelta(minutes=5)).isoformat()},
            expires_at=timezone.now() + timedelta(minutes=5))
        self.assertTrue(vas_identity.arm_contacts(pa, self.user, MSISDN))
        pa.state = flows.FLOW_ID_STATE
        pa.save(update_fields=["state"])
        return pa

    def test_revoked_link_or_credentials_block_contact_token_edit_code_and_resend(self):
        for mutation in ("link", "password", "pin"):
            with self.subTest(mutation=mutation):
                pa = self.contact_action()
                token = flows.sign_identity_token(pa)
                if mutation == "link":
                    WhatsAppLink.objects.filter(user=self.user).update(status="revoked")
                elif mutation == "password":
                    User.objects.filter(pk=self.user.pk).update(password="changed-password-hash")
                else:
                    User.objects.filter(pk=self.user.pk).update(transaction_pin="changed-pin-hash")
                with patch.object(router, "email_live", return_value=True), \
                        patch.object(router, "send_email") as email, patch.object(router, "reply"):
                    self.assertIsNone(flows.resolve_identity_token(token))
                    self.assertEqual(router.kyc_flow_email_address(pa, "changed@example.test")[0], "stop")
                    self.assertEqual(router.kyc_flow_email_code(pa, "572938")[0], "stop")
                    router._kyc_send_email_code(pa, self.user, MSISDN)
                email.assert_not_called()
                self.user.refresh_from_db()
                self.assertEqual(self.user.email, "ada@zitch.test")
                self.assertFalse(self.user.email_verified)
                pa.delete()
                WhatsAppLink.objects.filter(user=self.user).update(status=WhatsAppLink.ACTIVE)

    def test_intentional_contact_email_change_refreshes_only_that_bound_session(self):
        pa = self.contact_action()
        token = flows.sign_identity_token(pa)
        with patch.object(router, "email_live", return_value=True), \
                patch.object(router, "_kyc_test_code", return_value=""), \
                patch.object(router, "send_email", return_value={"success": True}) as email, \
                patch.object(router, "reply"), patch.object(router, "_kyc_next"):
            self.assertEqual(router.kyc_flow_email_address(pa, "new-contact@example.test")[0], "ok")
            code = email.call_args.args[2][-6:]
            self.assertIsNotNone(flows.resolve_identity_token(token))
            self.assertEqual(router.kyc_flow_email_code(pa, code)[0], "ok")
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "new-contact@example.test")
        self.assertTrue(self.user.email_verified)
        self.assertIsNotNone(flows.resolve_identity_token(token))

    def test_contact_revoked_during_email_delivery_does_not_arm_new_code(self):
        pa = self.contact_action()
        old_hash = pa.payload["code_hash"]
        def revoke(*args, **kwargs):
            WhatsAppLink.objects.filter(user=self.user).update(status="revoked")
            return {"success": True}
        with patch.object(router, "send_email", side_effect=revoke):
            self.assertFalse(router._kyc_mail_code(pa, self.user))
        pa.refresh_from_db()
        self.assertEqual(pa.payload["code_hash"], old_hash)

    def test_identity_can_start_before_test_account_invitation_and_legacy_gates(self):
        with patch.object(router, "customer_funding_account", return_value=self.funding), \
                patch.object(router, "flows_live", return_value=True), \
                patch.object(router, "send_flow", return_value={"success": True}) as send, \
                patch.object(router, "reply"), \
                patch.object(router, "_bank_upgrade_blocks", side_effect=AssertionError("legacy gate")), \
                patch("wallet.identity.accepted_identity_pending", side_effect=AssertionError("legacy gate")), \
                patch("whatsapp.vas_flow.start") as allocate:
            router._start_kyc(self.user, MSISDN)
        allocate.assert_not_called()
        self.assertEqual(send.call_args.kwargs["screen"], flows.IDENTITY_SCREEN)
        pa = PendingAction.objects.get()
        self.assertTrue(pa.payload["vas_identity"])
        self.assertEqual(pa.payload["id_kind"], "bvn")

    def test_signup_email_is_verified_separately_before_bvn_lookup(self):
        self.user.email_verified = False
        self.user.save(update_fields=["email_verified"])
        with patch.object(router, "customer_funding_account", return_value=self.funding), \
                patch.object(router, "flows_live", return_value=True), \
                patch.object(router, "email_live", return_value=True), \
                patch.object(router, "_kyc_test_code", return_value=""), \
                patch.object(router, "send_flow", return_value={"success": True}), \
                patch.object(router, "send_email", return_value={"success": True}) as email, \
                patch.object(router, "reply"), \
                patch("utility.providers.prembly_verify_bvn") as lookup:
            router._start_kyc(self.user, MSISDN)
            pa = PendingAction.objects.get()
            self.assertEqual(pa.payload["id_kind"], "email")
            self.assertTrue(pa.payload["vas_contacts"])
            self.assertEqual(email.call_args.args[0], self.user.email)
            code = email.call_args.args[2][-6:]
            self.assertEqual(router.kyc_flow_email_code(pa, code)[0], "ok")
        lookup.assert_not_called()
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)
        self.assertFalse(self.user.bvn_verified)
        self.assertTrue(PendingAction.objects.get().payload["vas_identity"])

    def test_existing_user_phone_proof_keeps_contact_session_bound_for_email(self):
        self.user.phone_verified = False
        self.user.email_verified = False
        self.user.save(update_fields=["phone_verified", "email_verified"])
        with patch.object(router, "customer_funding_account", return_value=self.funding), \
                patch.object(router, "flows_live", return_value=True), \
                patch.object(router, "sms_live", return_value=True), \
                patch.object(router, "email_live", return_value=True), \
                patch.object(router, "_kyc_test_code", return_value=""), \
                patch.object(router, "send_sms", return_value={"success": True}) as sms, \
                patch.object(router, "send_email", return_value={"success": True}) as email, \
                patch.object(router, "send_flow", return_value={"success": True}), \
                patch.object(router, "reply"):
            router._start_kyc(self.user, MSISDN)
            pa = PendingAction.objects.get()
            self.assertEqual(pa.state, "phone")
            original_stamp = pa.payload["vas_identity_credentials"]
            phone_code = sms.call_args.args[1].split()[1]
            router._advance_kyc(pa, self.user, MSISDN, phone_code)
        self.user.refresh_from_db()
        pa.refresh_from_db()
        self.assertTrue(self.user.phone_verified)
        self.assertFalse(self.user.email_verified)
        self.assertNotEqual(pa.payload["vas_identity_credentials"], original_stamp)
        self.assertTrue(vas_identity.contact_bound(pa, self.user))
        self.assertEqual(pa.state, flows.FLOW_ID_STATE)
        self.assertEqual(pa.payload["id_kind"], "email")
        self.assertEqual(email.call_args.args[0], self.user.email)
        self.assertIsNotNone(flows.resolve_identity_token(flows.sign_identity_token(pa)))

    def test_completed_vas_identity_never_offers_address_or_face_tier_upgrade(self):
        with patch.object(router, "customer_funding_account", return_value=self.funding), \
                patch.object(router, "_kyc_outstanding", return_value=[]), \
                patch.object(router, "reply") as reply, patch.object(router, "reply_buttons") as buttons, \
                patch.object(router, "send_cta_url") as link:
            router._offer_tier_upgrade(self.user, MSISDN)
        buttons.assert_not_called()
        link.assert_not_called()
        messages = " ".join(call.args[1] for call in reply.call_args_list)
        self.assertNotIn("address verification", messages.lower())
        self.assertNotIn("face check", messages.lower())
        self.assertIn("invitation is pending", messages)

    def test_stale_tier_three_action_returns_to_vas_verification(self):
        pa = PendingAction.objects.create(user=self.user, msisdn=MSISDN,
            action_type="kyc", state=router.KYC_UPGRADE_STATE, payload={},
            expires_at=timezone.now() + timedelta(minutes=5))
        with patch.object(router, "customer_funding_account", return_value=self.funding), \
                patch.object(router, "_start_kyc") as start, \
                patch.object(router, "_send_web_verification") as address:
            router._advance_kyc(pa, self.user, MSISDN, "tier3")
        start.assert_called_once_with(self.user, MSISDN)
        address.assert_not_called()

    def test_signup_starts_contact_checks_without_legacy_account_credentials(self):
        msisdn = "2348099990000"
        ob = WaOnboarding.objects.create(msisdn=msisdn, step="pin", payload={
            "first_name": "New", "last_name": "Tester", "email": "new@example.test",
            "phone_verified_flow": True}, expires_at=timezone.now() + timedelta(minutes=5))
        with patch.object(router, "customer_funding_account", return_value=self.funding), \
                patch.object(router, "_start_kyc") as contacts, \
                patch.object(router.wallet_views, "_wema_funding_enabled", return_value=False), \
                patch.object(router, "_start_add_account") as legacy, \
                patch.object(router, "reply") as reply:
            self.assertTrue(router._finish_onboarding(ob, msisdn, "572938"))
        user = User.objects.get(email="new@example.test")
        self.assertFalse(user.email_verified)
        contacts.assert_called_once_with(user, msisdn)
        legacy.assert_not_called()
        self.assertIn("Do not send money", reply.call_args.args[1])

    def test_contact_code_typed_in_chat_is_not_consumed(self):
        pa = PendingAction.objects.create(user=self.user, msisdn=MSISDN,
            action_type="kyc", state=flows.FLOW_ID_STATE,
            payload={"id_kind": "email", "id_step": "code", "vas_contacts": True},
            expires_at=timezone.now() + timedelta(minutes=5))
        with patch.object(router, "reply") as reply, patch.object(router, "_accept_identity_in_chat") as accept:
            router._advance(pa, self.user, MSISDN, "572938")
        accept.assert_not_called()
        self.assertIn("secure verification form", reply.call_args.args[1])
        self.assertNotIn("572938", reply.call_args.args[1])

    def test_private_identity_media_and_codes_are_scrubbed_before_queue(self):
        from whatsapp.jobs import _decrypt
        from whatsapp.models import WaMessageLog
        from whatsapp.views import _process
        PendingAction.objects.create(user=self.user, msisdn=MSISDN,
            action_type="kyc", state=flows.FLOW_ID_STATE,
            payload={"id_kind": "bvn", "vas_identity": True},
            expires_at=timezone.now() + timedelta(minutes=5))
        with patch("whatsapp.jobs.process_inbound_message"):
            _process({"id": "vas-generic-private", "from": MSISDN, "type": "image",
                      "image": {"id": "private-media", "caption": "12345678901"}})
        row = WaMessageLog.objects.get(wa_message_id="vas-generic-private")
        payload = _decrypt(row.processing_payload)
        self.assertTrue(payload["vas_private_input"])
        self.assertEqual(payload["media_id"], "")
        self.assertNotIn("12345678901", str(payload))

    def test_identity_resend_explains_secure_restart_without_using_signup_contacts(self):
        pa = PendingAction.objects.create(user=self.user, msisdn=MSISDN,
            action_type="kyc", state=flows.FLOW_ID_STATE,
            payload={"id_kind": "bvn", "vas_identity": True, "vas_contacts": True,
                     "id_otp_hash": "an-existing-challenge"},
            expires_at=timezone.now() + timedelta(minutes=5))
        with patch.object(router, "reply") as reply, patch.object(router, "send_email") as email, \
                patch.object(router, "send_sms") as sms:
            router._advance(pa, self.user, MSISDN, "resend")
        sms.assert_not_called()
        email.assert_not_called()
        self.assertIn("reply cancel", reply.call_args.args[1])
        self.assertNotIn("no code to re-send", reply.call_args.args[1])
