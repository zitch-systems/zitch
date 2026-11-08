"""Partnership chat preserves biller safety and refuses retired setup sessions."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from wallet.services import get_or_create_wallet
from whatsapp import router
from whatsapp.models import ConversationState, PendingAction
from whatsapp.retired_setup import RETIRED_SETUP_MESSAGE
from whatsapp.test_flows import MSISDN, _make_user


class PartnershipCustomerChatTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.wallet = get_or_create_wallet(self.user)
        self.wallet.account_number = "0123456789"
        self.wallet.account_name = "Ada Eze"
        self.wallet.bank_name = "Wema Bank"
        self.wallet.save(update_fields=["account_number", "account_name", "bank_name"])
        self.funding = {
            "provider": "partnership", "has_account": True, "available": True,
            "spending_available": False, "account_setup_state": "ready",
            "account_number": self.wallet.account_number,
            "account_name": self.wallet.account_name, "bank_name": self.wallet.bank_name,
        }

    def retired_action(self, marker="vas_enroll", state="flow_identity"):
        return PendingAction.objects.create(
            user=self.user, msisdn=MSISDN,
            action_type="vas_enroll" if marker == "vas_enroll" else "kyc",
            state=state,
            payload={} if marker == "vas_enroll" else {marker: True},
            expires_at=timezone.now() + timedelta(minutes=5),
        )

    def test_partnership_menu_follows_lifecycle_policy_on_each_call(self):
        for lifecycle, enabled in (("active", True), ("archive", False)):
            with self.subTest(lifecycle=lifecycle), override_settings(
                    BANK_ACCOUNT_PROVIDER="partnership", WEMA_PARTNERSHIP_MODE=lifecycle):
                menu = router.menu_text()
                self.assertEqual("2️⃣  💸 Send money" in menu, enabled)
                self.assertEqual("1️⃣1️⃣  📷 Scan a QR code" in menu, enabled)
                self.assertEqual("send 5k to Ada" in menu, enabled)
                self.assertIn("3️⃣  📱 Airtime / Data", menu)
                self.assertIn("4️⃣  💡 Pay a bill", menu)

    @override_settings(BANK_ACCOUNT_PROVIDER="partnership", WEMA_PARTNERSHIP_MODE="archive")
    def test_archived_transfer_option_is_refused_before_recipient_or_pin(self):
        with patch.object(router, "reply") as reply, \
                patch.object(router, "_bank_items") as banks, \
                patch.object(router, "send_flow") as secure:
            router.handle_inbound(MSISDN, "2")
        banks.assert_not_called()
        secure.assert_not_called()
        self.assertFalse(PendingAction.objects.filter(msisdn=MSISDN).exists())
        self.assertIn("unavailable", reply.call_args.args[1])

    def test_retired_chat_sessions_cannot_reinterpret_identity_or_otp_input(self):
        for marker in ("vas_enroll", "vas_identity", "vas_contacts", "vas_reregister"):
            with self.subTest(marker=marker):
                pa = self.retired_action(marker)
                with patch.object(router, "reply") as reply, \
                        patch.object(router, "_advance_kyc") as kyc, \
                        patch.object(router, "_start_kyc") as start:
                    router.handle_inbound(MSISDN, "22222222222")
                reply.assert_called_once_with(MSISDN, RETIRED_SETUP_MESSAGE)
                kyc.assert_not_called()
                start.assert_not_called()
                self.assertFalse(PendingAction.objects.filter(pk=pa.pk).exists())

    def test_retired_direct_identity_and_contact_helpers_never_contact_providers(self):
        calls = (
            (router._account_submit_identity, (self.user, MSISDN, "22222222222"), "fail"),
            (router.account_flow_otp, ("123456",), ("done", RETIRED_SETUP_MESSAGE)),
            (router.kyc_flow_identity_otp, ("123456",), ("stop", RETIRED_SETUP_MESSAGE)),
            (router.kyc_flow_email_address, ("new@example.com",), ("stop", RETIRED_SETUP_MESSAGE)),
            (router.kyc_flow_email_code, ("123456",), ("stop", RETIRED_SETUP_MESSAGE)),
            (router._kyc_send_identity_otp, (self.user, "bvn", self.user.phone), RETIRED_SETUP_MESSAGE),
            (router._kyc_mail_code, (self.user,), False),
        )
        for fn, args, expected in calls:
            with self.subTest(fn=fn.__name__):
                pa = self.retired_action("vas_identity")
                with patch.object(router, "reply"), \
                        patch.object(router, "send_sms") as sms, \
                        patch.object(router, "send_email") as email, \
                        patch.object(router.wallet_views, "_start_wema_attempt") as create, \
                        patch.object(router.wallet_views, "complete_wema_provisioning") as complete:
                    self.assertEqual(fn(pa, *args), expected)
                sms.assert_not_called()
                email.assert_not_called()
                create.assert_not_called()
                complete.assert_not_called()
                self.assertFalse(PendingAction.objects.filter(pk=pa.pk).exists())

    @override_settings(WHATSAPP_PROCESS_INLINE=False)
    def test_retired_actions_fail_at_both_fresh_locked_execution_boundaries(self):
        for fn in (router.authorise_flow_execution, router.run_flow_execution):
            with self.subTest(fn=fn.__name__):
                pa = self.retired_action("vas_reregister", state=router.FLOW_PIN_STATE)
                pa.action_type = "unlock"
                pa.save(update_fields=["action_type"])
                # The caller can hold an older object; the database wins.
                pa.payload = {}
                with patch.object(router, "_exec_unlock") as unlock, \
                        patch.object(router, "_mark_verified") as mark, \
                        patch("whatsapp.jobs.enqueue_flow_execution") as enqueue:
                    outcome = fn(pa, self.user)
                self.assertEqual(str(outcome), RETIRED_SETUP_MESSAGE)
                self.assertEqual(outcome.status, router.OUTCOME_FAILED)
                unlock.assert_not_called()
                mark.assert_not_called()
                enqueue.assert_not_called()
                self.assertFalse(PendingAction.objects.filter(pk=pa.pk).exists())

    def test_retired_command_only_reports_retirement(self):
        with patch.object(router, "reply") as reply, \
                patch.object(router, "_start_kyc") as kyc, \
                patch.object(router, "_start_add_account") as account:
            router.handle_inbound(MSISDN, "reregister")
        reply.assert_called_once_with(MSISDN, RETIRED_SETUP_MESSAGE)
        kyc.assert_not_called()
        account.assert_not_called()

    def test_unlinked_retired_command_never_starts_signup_or_login(self):
        with patch.object(router, "reply") as reply, \
                patch.object(router, "_current_onboarding") as onboarding:
            router._handle_unlinked(MSISDN, "reregister")
        reply.assert_called_once_with(MSISDN, RETIRED_SETUP_MESSAGE)
        onboarding.assert_not_called()

    def test_historical_funding_review_does_not_open_a_new_account_or_identity_flow(self):
        held = {**self.funding, "account_setup_state": "legacy_funding_review",
                "available": False, "has_account": False,
                "migration_message": "Your historical funds need review. Contact support."}
        for fn in (router._do_add_money, router._do_account_details,
                   router._start_add_account, router._start_kyc):
            with self.subTest(fn=fn.__name__), \
                    patch.object(router, "customer_funding_account", return_value=held), \
                    patch.object(router, "reply") as reply, \
                    patch.object(router, "send_flow") as flow, \
                    patch.object(router.wallet_views, "_start_wema_attempt") as create:
                fn(self.user, MSISDN)
                reply.assert_called_once_with(MSISDN, held["migration_message"])
                flow.assert_not_called()
                create.assert_not_called()
                self.assertFalse(PendingAction.objects.filter(msisdn=MSISDN).exists())

    def test_partnership_kyc_dispatch_and_generic_biller_text_remain_available(self):
        pa = PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="kyc", state="bvn",
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        with patch.object(router, "_advance_kyc") as kyc:
            router._advance(pa, self.user, MSISDN, "22222222222")
        kyc.assert_called_once_with(pa, self.user, MSISDN, "22222222222")
        self.assertIsNotNone(router.vas_text_intent("2k airtime"))

    def test_expired_nonpayment_actions_have_contextual_restart_without_charge_claim(self):
        restarts = {"vas_enroll": "Reply *6*", "kyc": "Reply *8*", "add_account": "Reply *6*",
                    "unlock": "Repeat your request", "setpin": "Reply *reset pin*"}
        for action_type, restart in restarts.items():
            with self.subTest(action_type=action_type):
                pa = PendingAction.objects.create(user=self.user, msisdn=MSISDN,
                    action_type=action_type, state="flow_pin" if action_type == "unlock" else "flow_vas",
                    expires_at=timezone.now() - timedelta(seconds=1))
                with patch.object(router, "reply") as reply:
                    self.assertTrue(router._announce_timeout(MSISDN))
                message = reply.call_args.args[1]
                self.assertIn("session expired", message)
                self.assertIn(restart, message)
                self.assertNotIn("charged", message)
                self.assertNotIn("payment", message)
                self.assertNotIn("minutes", message)
                self.assertFalse(PendingAction.objects.filter(pk=pa.pk).exists())


    @override_settings(WA_REAUTH_IDLE_MINUTES=15)
    def test_expired_setup_never_bypasses_pin_before_account_details(self):
        ConversationState.objects.create(msisdn=MSISDN,
            last_verified=timezone.now() - timedelta(minutes=70))
        for action_type in ("vas_enroll", "unlock", "airtime"):
            with self.subTest(action_type=action_type):
                PendingAction.objects.create(user=self.user, msisdn=MSISDN,
                    action_type=action_type, state="flow_pin" if action_type == "unlock" else "flow_vas",
                    expires_at=timezone.now() - timedelta(seconds=1))
                with patch.object(router, "reply"), patch.object(router, "_send_unlock") as unlock, \
                        patch.object(router, "_do_account_details") as details:
                    router.handle_inbound(MSISDN, "7")
                unlock.assert_called_once_with(self.user, MSISDN, "7")
                details.assert_not_called()


    @override_settings(WA_REAUTH_IDLE_MINUTES=15)
    def test_code_for_expired_setup_does_not_open_an_unlock_form(self):
        PendingAction.objects.create(user=self.user, msisdn=MSISDN, action_type="vas_enroll",
            state="flow_vas", expires_at=timezone.now() - timedelta(seconds=1))
        with patch.object(router, "reply") as reply, patch.object(router, "_send_unlock") as unlock:
            router.handle_inbound(MSISDN, "123456")
        reply.assert_called_once()
        self.assertIn("session expired", reply.call_args.args[1])
        unlock.assert_not_called()


    def test_balance_explains_funds_are_not_yet_spendable(self):
        with patch.object(router, "customer_funding_account", return_value=self.funding), \
                patch.object(router, "reply") as reply:
            router._do_balance(self.user, MSISDN)
        self.assertIn("Transfers and bill payments are currently unavailable", reply.call_args.args[1])


    def test_spending_flow_stops_before_collecting_transfer_details(self):
        with patch.object(router, "bank_spend_error", return_value="Transfers are not available."), \
                patch.object(router, "reply") as reply:
            self.assertTrue(router._blocked_from_spending(self.user, MSISDN))
        self.assertIn("Transfers are not available", reply.call_args.args[1])


    def test_bill_eligible_funding_and_balance_do_not_say_bills_are_disabled(self):
        funding = {**self.funding, "has_account": True, "available": True,
                   "account_setup_state": "ready", "account_number": "7121234567",
                   "bill_payments_available": True, "transfers_available": False}
        with patch.object(router, "customer_funding_account", return_value=funding), \
                patch.object(router, "reply") as reply:
            router._send_account_details(MSISDN, self.wallet)
            account_message = reply.call_args.args[1]
            router._do_balance(self.user, MSISDN)
            balance_message = reply.call_args.args[1]
        for message in (account_message, balance_message):
            self.assertIn("Bill payments are available. Transfers are currently unavailable.", message)
            self.assertNotIn("Transfers and bill payments are currently unavailable", message)


    def test_bill_starters_select_biller_gate_and_transfers_keep_transfer_gate(self):
        starters = [
            (router._start_vtu, ()), (router._start_airtime, ()), (router._start_data, ()),
            (router._start_electricity, ()), (router._begin_electricity, (None, None, None, None)),
            (router._start_cable, ()), (router._begin_cable, (None, None)),
            (router._start_exam, ()), (router._begin_airtime, (None, None, None)),
        ]
        for start, args in starters:
            with self.subTest(start=start.__name__), \
                    patch.object(router, "_blocked_from_spending", return_value=True) as guard:
                start(self.user, MSISDN, *args)
                guard.assert_called_once_with(self.user, MSISDN, biller=True)
        with patch.object(router, "_blocked_from_spending", return_value=True) as guard:
            router._start_transfer(self.user, MSISDN)
        guard.assert_called_once_with(self.user, MSISDN)


    def test_bill_gate_uses_retained_biller_capability(self):
        def reason(user, amount, *, biller=False):
            return None if biller else "Transfers unavailable"
        with patch.object(router, "bank_spend_error", side_effect=reason) as guard, \
                patch.object(router, "reply"):
            self.assertFalse(router._blocked_from_spending(self.user, MSISDN, biller=True))
            self.assertTrue(router._blocked_from_spending(self.user, MSISDN))
        self.assertEqual(guard.call_args_list[0].kwargs, {"biller": True})
        self.assertEqual(guard.call_args_list[1].kwargs, {"biller": False})


    def test_bill_executor_checks_biller_limits_before_provider(self):
        pa = PendingAction.objects.create(user=self.user, msisdn=MSISDN,
            action_type="airtime", state="executing", payload={"amount": "100", "net": "1", "phone": "08012345678"},
            expires_at=timezone.now() + timedelta(minutes=5))
        with patch.object(router, "send_limit_error", return_value="Bill unavailable") as guard, \
                patch.object(router, "_limit_reply"), patch.object(router, "run_provider_purchase") as purchase:
            router._run_vtu(pa, self.user, MSISDN, Decimal("100"), "Airtime - MTN", None, None)
        guard.assert_called_once_with(self.user, Decimal("100"), biller=True)
        purchase.assert_not_called()


    def test_private_confirmation_and_affordability_use_canonical_available_balance(self):
        self.wallet.balance = Decimal("600")
        self.wallet.save(update_fields=["balance"])
        pa = PendingAction.objects.create(user=self.user, msisdn=MSISDN,
            action_type="airtime", state="amount", payload={"amount": "200", "net": "1", "phone": "08012345678"},
            expires_at=timezone.now() + timedelta(minutes=5))
        with patch.object(router, "customer_spendable_balance", return_value=Decimal("100")), \
                patch.object(router, "reply") as reply, patch.object(router, "_arm_confirm") as confirm:
            self.assertEqual(router._fresh_wallet_balance(self.user), Decimal("100"))
            self.assertEqual(router._flow_balance_line(pa), "Available balance ₦100.00")
            self.assertTrue(router._insufficient(self.user, Decimal("200")))
            router._advance_airtime(pa, self.user, MSISDN, "200")
        self.assertIn("₦100.00", reply.call_args.args[1])
        self.assertNotIn("₦600.00", reply.call_args.args[1])
        confirm.assert_not_called()


    def test_balance_separates_historical_total_from_available_bill_funds(self):
        balances = {"balance": Decimal("600"), "available_balance": Decimal("100"),
                    "historical_balance": Decimal("500")}
        funding = {**self.funding, "bill_payments_available": True, "transfers_available": False}
        with patch.object(router, "wallet_balance_payload", return_value=balances), \
                patch.object(router, "customer_funding_account", return_value=funding), \
                patch.object(router, "reply") as reply:
            router._do_balance(self.user, MSISDN)
        message = reply.call_args.args[1]
        self.assertIn("Total NGN wallet balance: ₦600.00", message)
        self.assertIn("Available for bills: ₦100.00", message)
        self.assertIn("Historical funds unavailable for bills: ₦500.00", message)


    def test_encrypted_airtime_details_do_not_offer_historical_funds_for_spending(self):
        from whatsapp.test_vtu_flow import _submit, _vtu_action
        from whatsapp.flows import VTU_AIRTIME
        self.wallet.balance = Decimal("600")
        self.wallet.save(update_fields=["balance"])
        pa = _vtu_action(self.user, vtu_kind="airtime", vtu_step="details", net="1")
        with patch.object(router, "customer_spendable_balance", return_value=Decimal("100")), \
                patch("common.http.send_limit_error", return_value=None):
            response = _submit(pa, {"amount": "200", "phone": "08012345678"})
        self.assertEqual(response["screen"], VTU_AIRTIME)
        self.assertIn("₦100.00", response["data"]["error"])
        self.assertNotIn("₦600.00", response["data"]["error"])

