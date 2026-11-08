"""Partnership chat preserves biller safety and refuses retired setup sessions."""
from banking.test_fixtures import bank_channel_case
from banking.test_client import CONFIG
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


@bank_channel_case
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
            action_type=marker if marker in ("vas_enroll", "add_account") else "kyc",
            state=state,
            payload={} if marker in ("vas_enroll", "add_account") else {marker: True},
            expires_at=timezone.now() + timedelta(minutes=5),
        )

    def test_the_menu_offers_nothing_the_rails_cannot_do(self):
        """It used to flip every option on the NINEPSB ENABLED setting. That
        setting reaches nothing now -- banking.client.configured() is False
        whatever it says -- so the two halves of that test were the same half.

        What the menu has to get right is narrower and more useful: it must not
        name a product whose rail is gone, and the options it does relabel must
        be relabelled rather than silently still working. The companion test
        below checks the other end, that the relabelled ones actually refuse.
        """
        menu = router.menu_text()
        # Relabelled, numbers kept so a remembered digit still lands somewhere
        # truthful rather than on whatever moved up into its place.
        self.assertIn("2️⃣  💸 Transfers unavailable", menu)
        self.assertIn("1️⃣1️⃣  📷 QR payments unavailable", menu)
        self.assertNotIn("Send money", menu)
        # Removed outright with the VAS rail, numbers left as gaps.
        for gone in ("📱 Airtime / Data", "💡 Pay a bill", "Exam PIN"):
            self.assertNotIn(gone, menu)
        # And the examples line reads as an instruction, not as the same
        # suggestion twice -- which is what replacing "send 5k to Ada" with
        # "check balance" produced when the line already offered it.
        hint = [l for l in menu.splitlines() if "type what you want" in l]
        self.assertEqual(len(hint), 1)
        self.assertEqual(hint[0].count("check balance"), 1)
        self.assertNotIn("send 5k to Ada", hint[0])

    def test_every_outbound_flow_refuses_at_the_door_not_at_the_pin(self):
        """The menu saying "Transfers unavailable" and the flow then asking "how
        much would you like to send?" is the failure this removal exists to end.

        It happened: the relabel is driven by partnership_new_business_allowed(),
        and the comment beside it says the shared spending guard refuses the
        payment before it begins. That stopped being true when bank_spend_error
        started returning None with no provider -- deliberately, because that is
        what lets a bankless wallet spend internally. So the guard grew a
        separate outbound leg, and this is what holds it in place.
        """
        for label, text in (("transfer", "2"), ("qr", "11")):
            with self.subTest(flow=label):
                with patch.object(router, "reply") as reply, \
                        patch.object(router, "send_flow") as secure, \
                        patch.object(router, "_bank_items") as banks:
                    router.handle_inbound(MSISDN, text)
                secure.assert_not_called()
                banks.assert_not_called()
                self.assertFalse(PendingAction.objects.filter(msisdn=MSISDN).exists())
                said = reply.call_args.args[1]
                self.assertIn("can't send money to a bank account", said)
                # Not a limit and not about them: no padlock, no "upgrade".
                self.assertNotIn("🔒", said)
                self.assertIn("balance is safe", said)

    def test_a_ledger_only_flow_is_not_caught_by_the_outbound_gate(self):
        """The other side of that line. A conversion never leaves the ledger, so
        it must still start -- otherwise the outbound leg has quietly become the
        blanket refusal the spending gate used to be."""
        with patch.object(router, "reply") as reply:
            router.handle_inbound(MSISDN, "5")
        said = " ".join(str(c.args[1]) for c in reply.call_args_list)
        self.assertNotIn("can't send money to a bank account", said)
        self.assertIn("currency", said.lower())

    def test_retired_chat_sessions_cannot_reinterpret_identity_or_otp_input(self):
        for marker in ("vas_enroll", "add_account", "vas_identity", "vas_contacts", "vas_reregister"):
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
            # kyc_flow_identity_otp and _kyc_send_identity_otp were the BVN/NIN
            # challenge pair. Both went with the identity rail; the email and
            # mail-code helpers below are what a retired session can still reach,
            # and are what must still refuse without touching a provider.
            (router.kyc_flow_email_address, ("new@example.com",), ("stop", RETIRED_SETUP_MESSAGE)),
            (router.kyc_flow_email_code, ("123456",), ("stop", RETIRED_SETUP_MESSAGE)),
            (router._kyc_mail_code, (self.user,), False),
        )
        for fn, args, expected in calls:
            with self.subTest(fn=fn.__name__):
                pa = self.retired_action("vas_identity")
                with patch.object(router, "reply"), \
                        patch.object(router, "send_sms") as sms, \
                        patch.object(router, "send_email") as email:
                    self.assertEqual(fn(pa, *args), expected)
                sms.assert_not_called()
                email.assert_not_called()
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


    def test_the_confirmation_and_the_affordability_check_use_available_not_raw(self):
        """What the chat offers to spend must be the canonical available balance.

        A wallet holding 600 of which only 100 is available must be told 100,
        and 200 must be refused as unaffordable. Offering the raw balance is how
        a customer is invited to spend money that is not theirs to spend yet --
        historical funds, a held amount, an archived balance.

        This was asserted on an airtime ladder and removed with it, which left
        _fresh_wallet_balance and _insufficient with no test at all. The three
        assertions never depended on the action type; only the _advance_airtime
        call at the end did.
        """
        self.wallet.balance = Decimal("600")
        self.wallet.save(update_fields=["balance"])
        pa = PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="transfer", state="amount",
            payload={"amount": "200", "account": "0123456789", "bank_code": "058",
                     "bank_name": "GTBank", "name": "JOHN DOE", "pin_attempts": 0},
            expires_at=timezone.now() + timedelta(minutes=5))
        with patch.object(router, "customer_spendable_balance", return_value=Decimal("100")):
            self.assertEqual(router._fresh_wallet_balance(self.user), Decimal("100"))
            self.assertEqual(router._flow_balance_line(pa), "Available balance ₦100.00")
            self.assertTrue(router._insufficient(self.user, Decimal("200")))
            # And the raw balance is not what 200 is compared against.
            self.assertFalse(router._insufficient(self.user, Decimal("50")))

    def test_a_kyc_session_still_dispatches_to_the_kyc_handler(self):
        """The dispatch, not the step. It was asserted on state "bvn", which
        _kyc_next no longer issues, and paired with a check that
        vas_text_intent still read "2k airtime" -- a reader for a rail that is
        gone, along with the function. The email state is what the ladder
        actually parks on now.
        """
        pa = PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="kyc", state="email",
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        with patch.object(router, "_advance_kyc") as kyc:
            router._advance(pa, self.user, MSISDN, "123456")
        kyc.assert_called_once_with(pa, self.user, MSISDN, "123456")

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

