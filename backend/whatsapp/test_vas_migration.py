"""Customer chat must never present validation or archived funding details."""
from datetime import timedelta
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
