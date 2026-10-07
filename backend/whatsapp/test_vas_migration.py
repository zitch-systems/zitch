"""Customer chat keeps pending accounts private and never offers them for funding."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from wallet.services import get_or_create_wallet
from whatsapp import router
from whatsapp.models import ConversationState, PendingAction
from whatsapp.test_flows import MSISDN, _make_user


@override_settings(ZITCH_LINKS={"APP": "https://zitch.ng/app"})
class VasCustomerChatTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.wallet = get_or_create_wallet(self.user)
        self.wallet.account_number = "0123456789"
        self.wallet.account_name = "Legacy Ada"
        self.wallet.bank_name = "Wema Bank"
        self.wallet.save(update_fields=["account_number", "account_name", "bank_name"])
        self.funding = {
            "provider": "wema_vas", "has_account": False, "available": False,
            "enrollment_available": True, "spending_available": False,
            "account_setup_state": "vas_enrollment_required",
            "migration_message": "Set up your new account here.",
        }

    def action(self):
        return PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="add_account", state="bvn",
            payload={"id_type": "bvn"}, expires_at=timezone.now() + timedelta(minutes=5))

    def test_add_money_routes_to_private_setup_without_app_handoff(self):
        with patch.object(router, "customer_funding_account", return_value=self.funding), \
                patch.object(router, "send_cta_url") as cta, \
                patch("whatsapp.vas_flow.start") as secure, \
                patch.object(router, "_start_add_account") as start:
            router._do_add_money(self.user, MSISDN)
        start.assert_not_called()
        cta.assert_not_called()
        secure.assert_called_once_with(self.user, MSISDN)

    def test_old_callback_cannot_reveal_validation_number(self):
        funding = {**self.funding, "account_setup_state": "vas_validation",
                   "enrollment_available": False, "account_number": "7111234567"}
        with patch.object(router, "customer_funding_account", return_value=funding), \
                patch.object(router, "send_cta_url") as cta, patch.object(router, "reply") as reply:
            router._send_account_details(MSISDN, self.wallet)
        cta.assert_not_called()
        self.assertNotIn("7111234567", reply.call_args.args[1])
        self.assertNotIn(self.wallet.account_number, reply.call_args.args[1])

    def test_account_details_does_not_open_or_replace_a_setup_session(self):
        pa = self.action()
        with patch.object(router, "customer_funding_account", return_value=self.funding), \
                patch.object(router, "reply") as reply, \
                patch("whatsapp.vas_flow.start") as start, \
                patch.object(router, "_start_kyc") as kyc:
            router._do_account_details(self.user, MSISDN)
        start.assert_not_called()
        kyc.assert_not_called()
        self.assertTrue(PendingAction.objects.filter(pk=pa.pk).exists())
        messages = " ".join(call.args[1] for call in reply.call_args_list)
        self.assertIn("Reply *6*", messages)
        self.assertNotIn(self.wallet.account_number, messages)

    def test_validation_details_hide_sample_and_transaction_limit(self):
        funding = {**self.funding, "test_mode": True, "account_setup_state": "vas_validation",
                   "enrollment_available": False, "validation_account_number": "7111234567"}
        with patch.object(router, "customer_funding_account", return_value=funding), \
                patch.object(router, "reply") as reply, patch("whatsapp.vas_flow.start") as start:
            router._do_account_details(self.user, MSISDN)
        start.assert_not_called()
        messages = " ".join(call.args[1] for call in reply.call_args_list)
        self.assertIn("Account activation pending", messages)
        self.assertNotIn("7111234567", messages)
        self.assertNotIn("TEST ONLY", messages)
        self.assertNotIn("Test mode", messages)
        self.assertNotIn("Tier", messages)
        self.assertNotIn("/transaction", messages)
        self.assertNotIn(self.wallet.account_number, messages)

    def test_verification_status_shows_setup_review_without_claiming_account_activation(self):
        funding = {**self.funding, "test_mode": True, "enrollment_available": False,
                   "enrollment_status": "review_required",
                   "enrollment_blockers": ["identity_verification", "balance_review"],
                   "enrollment_message": "Your existing balance needs review before account setup. Contact support.",
                   # A generic migration notice must not hide the actual blocker.
                   "migration_message": "Account activation pending."}
        with patch.object(router, "customer_funding_account", return_value=funding), \
                patch.object(router, "reply") as reply, \
                patch("whatsapp.vas_flow.start") as start:
            router._vas_verification_status(self.user, MSISDN)
        start.assert_not_called()
        messages = " ".join(call.args[1] for call in reply.call_args_list)
        self.assertIn(funding["enrollment_message"], messages)
        self.assertIn("Your completed checks stay saved", messages)
        self.assertNotIn("Account activation pending", messages)
        self.assertNotIn("Reply *6*", messages)
        self.assertNotIn(self.wallet.account_number, messages)

    def test_verification_status_keeps_existing_validation_account_pending_and_private(self):
        funding = {**self.funding, "test_mode": True, "account_setup_state": "vas_validation",
                   "enrollment_available": False, "enrollment_status": "enrolled",
                   "validation_account_number": "7111234567",
                   "migration_message": "Account activation pending."}
        with patch.object(router, "customer_funding_account", return_value=funding), \
                patch.object(router, "reply") as reply, \
                patch("whatsapp.vas_flow.start") as start:
            router._vas_verification_status(self.user, MSISDN)
        start.assert_not_called()
        messages = " ".join(call.args[1] for call in reply.call_args_list)
        self.assertIn("Account activation pending", messages)
        self.assertNotIn("7111234567", messages)
        self.assertNotIn("test account", messages.lower())
        self.assertNotIn("Reply *6*", messages)
        self.assertNotIn(self.wallet.account_number, messages)

    def test_live_account_details_do_not_advertise_transaction_limit(self):
        funding = {**self.funding, "account_setup_state": "ready", "has_account": True,
                   "available": True, "test_mode": False}
        with patch.object(router, "customer_funding_account", return_value=funding), \
                patch.object(router, "reply") as reply, \
                patch.object(router, "_send_account_details") as details:
            router._do_account_details(self.user, MSISDN)
        details.assert_called_once_with(MSISDN, self.wallet, intro="🏦 *Your funding account*")
        messages = " ".join(call.args[1] for call in reply.call_args_list)
        self.assertNotIn("Tier", messages)
        self.assertNotIn("/transaction", messages)

    def test_restricted_test_account_details_do_not_disclose_its_number(self):
        funding = {**self.funding, "test_mode": True, "account_setup_state": "restricted",
                   "enrollment_available": False, "validation_account_number": "7111234567",
                   "enrollment_message": "Your account is restricted. Please contact support."}
        with patch.object(router, "customer_funding_account", return_value=funding), \
                patch.object(router, "reply") as reply, patch("whatsapp.vas_flow.start") as start:
            router._do_account_details(self.user, MSISDN)
        start.assert_not_called()
        messages = " ".join(call.args[1] for call in reply.call_args_list)
        self.assertIn("restricted", messages)
        self.assertNotIn("7111234567", messages)
        self.assertNotIn("/transaction", messages)

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
    def test_expired_setup_then_account_details_keeps_a_warm_session_read_only(self):
        ConversationState.objects.create(msisdn=MSISDN, last_verified=timezone.now())
        PendingAction.objects.create(user=self.user, msisdn=MSISDN, action_type="vas_enroll",
            state="flow_vas", expires_at=timezone.now() - timedelta(seconds=1))
        funding = {**self.funding, "test_mode": True}
        with patch.object(router, "customer_funding_account", return_value=funding), \
                patch.object(router, "reply") as reply, patch("whatsapp.vas_flow.start") as start:
            router.handle_inbound(MSISDN, "7")
        start.assert_not_called()
        self.assertFalse(PendingAction.objects.filter(msisdn=MSISDN).exists())
        messages = " ".join(call.args[1] for call in reply.call_args_list)
        self.assertIn("session expired", messages)
        self.assertIn("My account details", messages)
        self.assertIn("Reply *6*", messages)
        self.assertNotIn("/transaction", messages)

    @override_settings(WA_REAUTH_IDLE_MINUTES=15)
    def test_code_for_expired_setup_does_not_open_an_unlock_form(self):
        PendingAction.objects.create(user=self.user, msisdn=MSISDN, action_type="vas_enroll",
            state="flow_vas", expires_at=timezone.now() - timedelta(seconds=1))
        with patch.object(router, "reply") as reply, patch.object(router, "_send_unlock") as unlock:
            router.handle_inbound(MSISDN, "123456")
        reply.assert_called_once()
        self.assertIn("session expired", reply.call_args.args[1])
        unlock.assert_not_called()

    def test_live_account_uses_vas_number_and_explains_spending_hold(self):
        funding = {**self.funding, "has_account": True, "available": True,
                   "account_setup_state": "ready", "account_number": "7121234567",
                   "account_name": "Zitch/Ada Eze", "bank_name": "Wema Bank"}
        with patch.object(router, "customer_funding_account", return_value=funding), \
                patch.object(router, "reply") as reply:
            router._send_account_details(MSISDN, self.wallet)
        message = reply.call_args.args[1]
        self.assertIn("7121234567", message)
        self.assertNotIn(self.wallet.account_number, message)
        self.assertIn("Transfers and bill payments are currently unavailable", message)

    def test_stale_identity_action_does_not_start_partnership_provisioning(self):
        pa = self.action()
        with patch.object(router, "customer_funding_account", return_value=self.funding), \
                patch("whatsapp.vas_flow.start") as secure, \
                patch.object(router.wallet_views, "_start_wema_attempt") as provision:
            result = router._account_submit_identity(pa, self.user, MSISDN, "22222222222")
        self.assertEqual(result, "fail")
        provision.assert_not_called()
        self.assertFalse(PendingAction.objects.filter(pk=pa.pk).exists())
        secure.assert_called_once_with(self.user, MSISDN)

    def test_balance_explains_funds_are_not_yet_spendable(self):
        with patch.object(router, "customer_funding_account", return_value=self.funding), \
                patch.object(router, "reply") as reply:
            router._do_balance(self.user, MSISDN)
        self.assertIn("Transfers and bill payments are currently unavailable", reply.call_args.args[1])

    def test_spending_flow_stops_before_collecting_transfer_details(self):
        with patch.object(router, "bank_spend_error", return_value="VAS payouts are not available."), \
                patch.object(router, "reply") as reply:
            self.assertTrue(router._blocked_from_spending(self.user, MSISDN))
        self.assertIn("VAS payouts are not available", reply.call_args.args[1])

    def test_existing_otp_can_complete_without_promising_a_funding_account(self):
        pa = self.action()
        with patch.object(router, "customer_funding_account", return_value=self.funding), \
                patch.object(router, "send_cta_url"), \
                patch.object(router.wallet_views, "complete_wema_provisioning", return_value=({"success": True}, 200)) as complete:
            state, message = router.account_flow_otp(pa, "123456")
        complete.assert_called_once()
        self.assertEqual(state, "done")
        self.assertIn("Reply 6 here", message)
        self.assertNotIn("Account created", message)

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
                    "historical_balance": Decimal("500"), "vas_balance": Decimal("100")}
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
