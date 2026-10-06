"""Customer chat must never present validation or archived funding details."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from wallet.services import get_or_create_wallet
from whatsapp import router
from whatsapp.models import PendingAction
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
