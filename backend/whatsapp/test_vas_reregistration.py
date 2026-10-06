"""An existing WhatsApp customer restarts VAS setup, never their identity/history."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User, hash_identifier, record_identity_proof, IdentityProof
from wallet.models import Wallet, Transaction
from wema_vas.models import VirtualAccount
from wema_vas.test_same_profile import VALIDATION
from whatsapp import flows, router, vas_flow
from whatsapp.models import PendingAction, WhatsAppLink, ConversationState, WaOnboarding
from whatsapp.test_vas_flow import FLOW, WA


@override_settings(BANK_ACCOUNT_PROVIDER="wema_vas", WEMA_PARTNERSHIP_MODE="archive",
                   WHATSAPP_PROCESS_INLINE=True, DEBUG=True, TESTING=True,
                   PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class VasReregistrationTests(TestCase):
    def setUp(self):
        self.msisdn = "2348011112222"
        self.pin = "294681"
        self.user = User.objects.create_user(username="existing-vas", password="existing-password",
            phone="+" + self.msisdn, email="existing@example.test", email_verified=True,
            phone_verified=True, bvn_verified=True, bvn_hash=hash_identifier("12345678901"),
            first_name="Ada", last_name="Eze")
        self.user.set_transaction_pin(self.pin)
        self.user.save(update_fields=User.PIN_UPDATE_FIELDS)
        self.wallet = Wallet.objects.create(user=self.user, balance=Decimal("0.00"),
            account_number="0454243073", account_reference="legacy-owner-ref")
        self.proof = record_identity_proof(self.user, "bvn", "12345678901",
            source=IdentityProof.IDENTITY_PROVIDER_OTP, verified_name="Ada Eze")
        self.txn = Transaction.objects.create(user=self.user, service="legacy airtime",
            amount=Decimal("100.00"), reference="preserved-history", transaction_status=Transaction.SUCCESS)
        self.link = WhatsAppLink.objects.create(user=self.user, wa_msisdn=self.msisdn,
            status=WhatsAppLink.ACTIVE, linked_at=timezone.now())
        self.payload = {"provider": "wema_vas", "test_mode": True, "available": False,
            "has_account": False, "enrollment_available": True,
            "account_setup_state": "vas_enrollment_required"}
        patches = {
            "funding": patch.object(router, "customer_funding_account", return_value=self.payload),
            "flows_live": patch.object(router, "flows_live", return_value=True),
            "send_flow": patch.object(router, "send_flow", return_value={"success": True}),
            "reply": patch.object(router, "reply"),
            "start": patch("whatsapp.vas_flow.start"),
            "network": patch("requests.sessions.Session.request", side_effect=AssertionError("no live calls")),
        }
        for name, mocked in patches.items():
            setattr(self, name, mocked.start())
            self.addCleanup(mocked.stop)

    def begin(self, command="register"):
        router.handle_inbound(self.msisdn, command)
        return PendingAction.objects.get(user=self.user, action_type="unlock")

    def confirm(self, pa, pin=None):
        return flows.handle_flow_request({"action": "data_exchange", "flow_token": flows.sign_flow_token(pa),
            "screen": flows.PIN_SCREEN, "data": {"pin": pin or self.pin}})

    def test_aliases_require_fresh_private_confirmation_even_with_warm_session(self):
        ConversationState.objects.create(msisdn=self.msisdn, last_verified=timezone.now())
        for command in ("register", "reregister", "re-register", "re register", "vas"):
            with self.subTest(command=command):
                pa = self.begin(command)
                self.assertEqual(pa.state, flows.FLOW_PIN_STATE)
                self.assertTrue(pa.payload["vas_reregister"])
                self.assertEqual(pa.payload["vas_reregister_link_id"], self.link.pk)
                self.start.assert_not_called()
                pa.delete()

    def test_correct_pin_continues_current_profile_and_preserves_history(self):
        pa = self.begin()
        self.confirm(pa)
        self.start.assert_called_once()
        self.assertEqual(self.start.call_args.args[0].pk, self.user.pk)
        self.assertEqual(User.objects.count(), 1)
        self.assertFalse(WaOnboarding.objects.exists())
        self.wallet.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(self.wallet.account_number, "0454243073")
        self.assertEqual(self.wallet.account_reference, "legacy-owner-ref")
        self.assertEqual(self.wallet.balance, Decimal("0.00"))
        self.assertTrue(self.user.bvn_verified)
        self.assertTrue(IdentityProof.objects.filter(pk=self.proof.pk).exists())
        self.assertTrue(Transaction.objects.filter(pk=self.txn.pk, reference="preserved-history").exists())

    def test_wrong_pin_cannot_restart_or_allocate(self):
        self.confirm(self.begin(), "999999")
        self.start.assert_not_called()
        self.assertTrue(PendingAction.objects.filter(action_type="unlock").exists())

    def test_replaying_consumed_pin_confirmation_cannot_restart_again(self):
        pa = self.begin()
        self.confirm(pa)
        self.confirm(pa)
        self.start.assert_called_once()

    def test_repeated_command_reuses_armed_confirmation(self):
        pa = self.begin()
        router.handle_inbound(self.msisdn, "vas")
        self.assertEqual(PendingAction.objects.get(action_type="unlock").pk, pa.pk)
        self.assertEqual(self.send_flow.call_count, 1)
        self.assertIn("secure form", self.reply.call_args.args[1])

    def test_link_replacement_invalidates_pending_confirmation(self):
        pa = self.begin()
        self.link.delete()
        WhatsAppLink.objects.create(user=self.user, wa_msisdn=self.msisdn, status=WhatsAppLink.ACTIVE)
        self.confirm(pa)
        self.start.assert_not_called()
        self.assertFalse(PendingAction.objects.filter(pk=pa.pk).exists())
        self.assertIsNone(ConversationState.objects.get(msisdn=self.msisdn).last_verified)

    def test_contact_or_password_changes_invalidate_pending_confirmation(self):
        for field, value in (("email", "changed@example.test"), ("password", "changed-password-hash")):
            with self.subTest(field=field):
                pa = self.begin()
                User.objects.filter(pk=self.user.pk).update(**{field: value})
                self.confirm(pa)
                self.start.assert_not_called()
                self.assertFalse(PendingAction.objects.filter(pk=pa.pk).exists())
                self.assertIsNone(ConversationState.objects.get(msisdn=self.msisdn).last_verified)

    def test_executing_payment_and_submitted_verification_are_not_cancelled(self):
        for action_type, state in (("airtime", router.EXECUTING_STATE), ("verification_web", "web_review")):
            with self.subTest(state=state):
                pa = PendingAction.objects.create(user=self.user, msisdn=self.msisdn,
                    action_type=action_type, state=state, payload={}, expires_at=timezone.now() + timedelta(minutes=10))
                router.handle_inbound(self.msisdn, "register")
                self.assertTrue(PendingAction.objects.filter(pk=pa.pk).exists())
                self.assertFalse(PendingAction.objects.filter(action_type="unlock").exists())
                self.start.assert_not_called()
                pa.delete()

    def test_unsubmitted_identity_form_can_be_replaced_without_erasing_verification(self):
        old = PendingAction.objects.create(user=self.user, msisdn=self.msisdn, action_type="kyc",
            state=flows.FLOW_ID_STATE, payload={"id_kind": "bvn"}, expires_at=timezone.now() + timedelta(minutes=10))
        self.begin()
        self.assertFalse(PendingAction.objects.filter(pk=old.pk).exists())
        self.user.refresh_from_db()
        self.assertTrue(self.user.bvn_verified)

    def test_existing_validation_account_is_shown_without_second_enrollment(self):
        self.funding.return_value = {**self.payload, "account_setup_state": "vas_validation",
            "enrollment_available": False, "validation_account_number": "7111234567"}
        self.confirm(self.begin())
        self.start.assert_not_called()
        messages = " ".join(call.args[1] for call in self.reply.call_args_list)
        self.assertIn("7111234567", messages)
        self.assertIn("TEST ONLY — DO NOT FUND", messages)

    def test_test_mode_can_never_advertise_real_funding_even_with_contradictory_flags(self):
        self.funding.return_value = {**self.payload, "account_setup_state": "ready",
            "has_account": True, "available": True, "enrollment_available": False,
            "account_number": "7111234567"}
        self.confirm(self.begin())
        self.start.assert_not_called()
        messages = " ".join(call.args[1] for call in self.reply.call_args_list)
        self.assertNotIn("Transfer to your dedicated Zitch account", messages)
        self.assertNotIn("Send money to it whenever", messages)

    def test_server_review_message_remains_authoritative_after_pin(self):
        self.funding.return_value = {**self.payload, "enrollment_available": False,
            "enrollment_message": "Your previous balance needs review. Contact support."}
        self.confirm(self.begin())
        self.start.assert_not_called()
        messages = " ".join(call.args[1] for call in self.reply.call_args_list)
        self.assertIn("Your previous balance needs review", messages)

    def test_unlinked_reregister_opens_signin_and_never_allocates(self):
        self.link.delete()
        with patch("whatsapp.login_flow.start_login") as signin:
            router.handle_inbound(self.msisdn, "re-register")
        signin.assert_called_once_with(self.msisdn)
        self.start.assert_not_called()
        self.assertFalse(WaOnboarding.objects.exists())


@override_settings(BANK_ACCOUNT_PROVIDER="wema_vas", WEMA_PARTNERSHIP_MODE="archive",
                   WEMA_VAS=VALIDATION, WHATSAPP=WA, WHATSAPP_FLOW=FLOW,
                   WHATSAPP_PROCESS_INLINE=True, DEBUG=True, TESTING=True,
                   PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class VasLegacyNameRefreshTests(TestCase):
    """Exercise the real eligibility payload, private PIN and setup entry."""

    def setUp(self):
        cache.clear()
        self.msisdn, self.pin, self.raw = "2348011113333", "294681", "12345678901"
        self.user = User.objects.create_user(username="legacy-no-name", password="saved-password",
            phone="+" + self.msisdn, email="legacy@example.test", email_verified=True,
            phone_verified=True, bvn_verified=True, bvn_hash=hash_identifier(self.raw),
            first_name="Ada", last_name="Eze")
        self.user.set_transaction_pin(self.pin)
        self.user.save(update_fields=User.PIN_UPDATE_FIELDS)
        self.wallet = Wallet.objects.create(user=self.user, balance=Decimal("0.00"),
            account_number="0454243073", account_reference="retained-legacy-reference")
        self.txn = Transaction.objects.create(user=self.user, service="legacy airtime",
            amount=Decimal("100.00"), reference="retained-failed-history",
            transaction_status=Transaction.FAILED)
        WhatsAppLink.objects.create(user=self.user, wa_msisdn=self.msisdn,
            status=WhatsAppLink.ACTIVE, linked_at=timezone.now())
        patches = {
            "flows_live": patch.object(router, "flows_live", return_value=True),
            "pin_send": patch.object(router, "send_flow", return_value={"success": True}),
            "setup_send": patch("whatsapp.providers.send_flow", return_value={"success": True}),
            "ready": patch.object(vas_flow, "ready", return_value=True),
            "reply": patch.object(router, "reply"),
            "network": patch("requests.sessions.Session.request", side_effect=AssertionError("no live calls")),
        }
        for name, mocked in patches.items():
            setattr(self, name, mocked.start())
            self.addCleanup(mocked.stop)

    def register(self):
        router.handle_inbound(self.msisdn, "register")
        unlock = PendingAction.objects.get(user=self.user, action_type="unlock")
        self.setup_send.assert_not_called()
        return flows.handle_flow_request({"action": "data_exchange", "flow_token": flows.sign_flow_token(unlock),
            "screen": flows.PIN_SCREEN, "data": {"pin": self.pin}})

    def assert_legacy_unchanged(self):
        self.user.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertTrue(self.user.bvn_verified)
        self.assertEqual(self.user.bvn_hash, hash_identifier(self.raw))
        self.assertEqual(self.wallet.account_number, "0454243073")
        self.assertEqual(self.wallet.account_reference, "retained-legacy-reference")
        self.assertEqual(self.wallet.balance, Decimal("0.00"))
        self.assertTrue(Transaction.objects.filter(pk=self.txn.pk).exists())
        self.assertEqual(User.objects.count(), 1)

    def assert_setup_opened(self):
        self.setup_send.assert_called_once()
        self.assertEqual(self.setup_send.call_args.kwargs["screen"], vas_flow.SETUP)
        self.assertIn("TEST ONLY — DO NOT FUND", self.setup_send.call_args.kwargs["screen_data"]["purpose"])
        self.assertTrue(PendingAction.objects.filter(user=self.user, action_type="vas_enroll").exists())
        self.assertFalse(VirtualAccount.objects.exists())
        self.assertFalse(IdentityProof.objects.exists())
        self.assert_legacy_unchanged()

    def test_verified_legacy_profile_without_legal_name_opens_real_secure_setup_after_pin(self):
        funding = router.customer_funding_account(self.user)
        self.assertFalse(funding["enrollment_available"])
        self.assertEqual(funding["enrollment_blockers"], ["identity_verification"])
        self.assertEqual(router._kyc_outstanding(self.user), [])
        self.register()
        self.assert_setup_opened()

    def test_verification_menu_also_offers_missing_legal_name_refresh(self):
        ConversationState.objects.create(msisdn=self.msisdn, last_verified=timezone.now())
        router.handle_inbound(self.msisdn, "8")
        self.assert_setup_opened()

    def test_financial_review_still_blocks_identity_only_setup_entry(self):
        Wallet.objects.filter(pk=self.wallet.pk).update(balance=Decimal("10.00"))
        funding = router.customer_funding_account(self.user)
        self.assertIn("identity_verification", funding["enrollment_blockers"])
        self.assertIn("balance_review", funding["enrollment_blockers"])
        self.register()
        self.setup_send.assert_not_called()
        self.assertFalse(VirtualAccount.objects.exists())
        self.assertIn(funding["enrollment_message"], " ".join(call.args[1] for call in self.reply.call_args_list))

    @override_settings(WEMA_VAS={**VALIDATION, "VALIDATION_SELF_SERVICE": False})
    def test_disabled_customer_enrollment_cannot_open_repair_flow(self):
        self.assertIn("policy_unavailable", router.customer_funding_account(self.user)["enrollment_blockers"])
        self.register()
        self.setup_send.assert_not_called()
        self.assert_legacy_unchanged()

    def test_existing_restricted_account_cannot_open_repair_flow(self):
        VirtualAccount.objects.create(user=self.user, number="7111234567", display_name="Zitch/Ada Eze",
            encrypted_identity="fixture", verification_reference="prior-verification",
            consent_reference="prior-consent", verified_at=timezone.now(), mode="validation", prefix="711",
            active=False, block_reason="under review")
        funding = router.customer_funding_account(self.user)
        self.assertEqual(funding["enrollment_blockers"], ["account_restricted"])
        self.register()
        self.setup_send.assert_not_called()
        self.assertEqual(VirtualAccount.objects.count(), 1)
        self.assert_legacy_unchanged()
