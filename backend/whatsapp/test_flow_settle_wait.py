"""The Flow waits briefly for the rail before deciding what its last screen says.

"Successful" was unreachable in production. Every money Flow closed on
"Pending", because the endpoint answered the instant the job was queued — so the
customer's last word from the Flow was always about a payment that had not been
attempted yet, and the tick was decoration on a heading nothing could reach.

The wait changes nothing about the payment: it is queued and executing either
way, and the worker sends the chat receipt regardless. All it decides is which
heading the closing screen can honestly show.
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from wallet.models import Transaction
from whatsapp import flows, router
from whatsapp.models import PendingAction
from whatsapp.test_flows import MSISDN, _make_user


def _action(user):
    return PendingAction.objects.create(
        user=user, msisdn=MSISDN, action_type="transfer", state="flow_pin",
        payload={"amount": "1000", "pin_attempts": 0},
        expires_at=timezone.now() + timedelta(minutes=5))


def _ledger(user, action_id, status):
    return Transaction.objects.create(
        user=user, amount=Decimal("1000"), direction=Transaction.OUT,
        service="transfer", reference=f"ref-{action_id}",
        idempotency_key=f"wa-{action_id}", transaction_status=status)


class SettleWaitTests(TestCase):

    def setUp(self):
        self.user = _make_user()

    @override_settings(WHATSAPP_FLOW_SETTLE_WAIT=2)
    def test_a_settled_transfer_closes_on_success(self):
        pa = _action(self.user)
        _ledger(self.user, pa.id, Transaction.SUCCESS)
        outcome = router._await_settlement(pa.id, self.user)
        self.assertEqual(outcome.status, router.OUTCOME_SUCCESS)

    @override_settings(WHATSAPP_FLOW_SETTLE_WAIT=2)
    def test_a_failed_transfer_closes_on_failed(self):
        """The one outcome the customer should see BEFORE the screen closes,
        rather than only in a chat message they may scroll past."""
        pa = _action(self.user)
        _ledger(self.user, pa.id, Transaction.FAILED)
        outcome = router._await_settlement(pa.id, self.user)
        self.assertEqual(outcome.status, router.OUTCOME_FAILED)
        # Phrased for every action, not just a transfer: an electricity payment
        # or a data bundle is not something "sent", and this line closes the Flow
        # for all of them.
        self.assertIn("not completed", outcome)
        self.assertNotIn("not charged", outcome)

    @override_settings(WHATSAPP_FLOW_SETTLE_WAIT=0.5)
    def test_a_row_still_pending_gives_up_and_says_pending(self):
        """Waiting past the budget is how the customer gets "Couldn't load
        content" instead of an answer — so the budget wins, always."""
        pa = _action(self.user)
        _ledger(self.user, pa.id, Transaction.PENDING)
        self.assertIsNone(router._await_settlement(pa.id, self.user))

    @override_settings(WHATSAPP_FLOW_SETTLE_WAIT=0.5)
    def test_no_ledger_row_at_all_gives_up_cleanly(self):
        pa = _action(self.user)
        self.assertIsNone(router._await_settlement(pa.id, self.user))

    @override_settings(WHATSAPP_FLOW_SETTLE_WAIT=0)
    def test_a_zero_budget_never_queries_or_sleeps(self):
        """The off switch has to be genuinely free — it is what gets set when
        the wait starts costing more than the heading is worth."""
        pa = _action(self.user)
        _ledger(self.user, pa.id, Transaction.SUCCESS)
        with patch.object(router.time, "sleep") as slept:
            self.assertIsNone(router._await_settlement(pa.id, self.user))
        slept.assert_not_called()

    @override_settings(WHATSAPP_FLOW_SETTLE_WAIT=5)
    def test_it_returns_as_soon_as_the_row_is_terminal(self):
        """A settled payment must not sit out the rest of the budget — that
        would spend seconds of a request thread on a decision already made."""
        pa = _action(self.user)
        _ledger(self.user, pa.id, Transaction.SUCCESS)
        with patch.object(router.time, "sleep") as slept:
            router._await_settlement(pa.id, self.user)
        slept.assert_not_called()

    @override_settings(WHATSAPP_FLOW_SETTLE_WAIT=2)
    def test_another_users_row_with_the_same_key_is_not_read(self):
        """Keys are per-action, but the lookup is scoped by user anyway: reading
        someone else's ledger row to decide this screen would be the worst kind
        of wrong answer."""
        from django.contrib.auth import get_user_model

        other = get_user_model().objects.create(
            username="08010000002", phone="08010000002", email="b@zitch.test")
        pa = _action(self.user)
        _ledger(other, pa.id, Transaction.SUCCESS)
        self.assertIsNone(router._await_settlement(pa.id, self.user))

    @override_settings(WHATSAPP_FLOW_SETTLE_WAIT=2)
    def test_the_queued_path_reports_the_settled_outcome(self):
        """End to end: authorise_flow_execution hands back what the rail said,
        not what it hoped."""
        pa = _action(self.user)
        _ledger(self.user, pa.id, Transaction.SUCCESS)
        with override_settings(WHATSAPP_PROCESS_INLINE=False), \
             patch("whatsapp.jobs.enqueue_flow_execution"), \
             patch("whatsapp.jobs.drain_in_background"):
            outcome = router.authorise_flow_execution(pa, self.user)
        self.assertEqual(outcome.status, router.OUTCOME_SUCCESS)

    @override_settings(WHATSAPP_FLOW={"RESULT_SCREEN": True})
    def test_done_rechecks_a_payment_that_settled_after_initial_pending(self):
        """A payment that settles after Meta rendered Pending must be shown as
        successful before the next Done tap is allowed to close the Flow."""
        pa = _action(self.user)
        token = flows.sign_flow_token(pa)
        _ledger(self.user, pa.id, Transaction.SUCCESS)
        flows.remember_pending(pa, self.user)

        refreshed = flows.handle_flow_request({
            "action": "data_exchange", "flow_token": token,
            "data": {"close": True},
        })
        self.assertEqual(refreshed["screen"], flows.RESULT_SCREEN)
        self.assertEqual(refreshed["data"]["status"], "✅ Successful")
        self.assertNotIn("extension_message_response", refreshed["data"])

        closed = flows.handle_flow_request({
            "action": "data_exchange", "flow_token": token,
            "data": {"close": True},
        })
        self.assertIn("extension_message_response", closed["data"])

    @override_settings(WHATSAPP_FLOW={"RESULT_SCREEN": True})
    def test_done_does_not_close_while_payment_is_still_pending(self):
        pa = _action(self.user)
        token = flows.sign_flow_token(pa)
        _ledger(self.user, pa.id, Transaction.PENDING)
        flows.remember_pending(pa, self.user)

        response = flows.handle_flow_request({
            "action": "data_exchange", "flow_token": token,
            "data": {"close": True},
        })
        self.assertEqual(response["screen"], flows.RESULT_SCREEN)
        self.assertEqual(response["data"]["status"], "⏳ Pending")
        self.assertNotIn("extension_message_response", response["data"])

    @override_settings(WHATSAPP_FLOW_SETTLE_WAIT=2)
    def test_identity_unlock_is_done_not_a_pending_payment(self):
        pa = PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="unlock",
            state="flow_pin", payload={"resume": "7", "pin_attempts": 0},
            expires_at=timezone.now() + timedelta(minutes=5))
        with override_settings(WHATSAPP_PROCESS_INLINE=False), \
             patch("whatsapp.jobs.enqueue_flow_execution") as queued, \
             patch("whatsapp.jobs.drain_in_background"), \
             patch.object(router, "_await_settlement") as waited:
            outcome = router.authorise_flow_execution(pa, self.user)
        queued.assert_called_once()
        waited.assert_not_called()
        self.assertEqual(outcome.status, "done")
        self.assertIn("Identity confirmed", outcome)

    def test_unlock_screen_does_not_disclose_balance_before_authentication(self):
        pa = PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="unlock", state="flow_pin",
            payload={}, expires_at=timezone.now() + timedelta(minutes=5))
        with patch.object(router, "_flow_balance_line") as balance:
            fields = router._flow_fields(pa)
        balance.assert_not_called()
        self.assertEqual(fields["balance"], "")
        self.assertIn("No payment", fields["details"])

    @override_settings(WHATSAPP_FLOW={"RESULT_SCREEN": True})
    def test_reopening_queued_payment_checks_ledger_instead_of_reporting_expiry(self):
        pa = _action(self.user)
        token = flows.sign_flow_token(pa)
        flows.remember_pending(pa, self.user)
        pa.state = "executing"
        pa.save(update_fields=["state"])
        txn = _ledger(self.user, pa.pk, Transaction.PENDING)
        response = flows.handle_flow_request({"action": "INIT", "flow_token": token})
        self.assertEqual(response["data"]["status"], "⏳ Pending")
        txn.transaction_status = Transaction.SUCCESS
        txn.save(update_fields=["transaction_status"])
        response = flows.handle_flow_request({"action": "INIT", "flow_token": token})
        self.assertEqual(response["data"]["status"], "✅ Successful")

    @override_settings(WHATSAPP_FLOW={"RESULT_SCREEN": False})
    def test_legacy_flow_receives_completion_envelope_not_reserved_screen_payload(self):
        response = flows.handle_flow_request({"action": "INIT", "flow_token": "expired"})
        self.assertEqual(response["screen"], "SUCCESS")
        self.assertIn("extension_message_response", response["data"])

    @override_settings(WHATSAPP_FLOW_SETTLE_WAIT=0.5)
    def test_the_queued_path_still_falls_back_to_pending(self):
        pa = _action(self.user)
        with override_settings(WHATSAPP_PROCESS_INLINE=False), \
             patch("whatsapp.jobs.enqueue_flow_execution"), \
             patch("whatsapp.jobs.drain_in_background"):
            outcome = router.authorise_flow_execution(pa, self.user)
        self.assertEqual(outcome.status, router.OUTCOME_PENDING)
        self.assertNotIn("✅", outcome)
