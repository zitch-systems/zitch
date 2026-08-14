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
from whatsapp import router
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
        self.assertIn("Nothing was sent", outcome)

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

    @override_settings(WHATSAPP_FLOW_SETTLE_WAIT=0.5)
    def test_the_queued_path_still_falls_back_to_pending(self):
        pa = _action(self.user)
        with override_settings(WHATSAPP_PROCESS_INLINE=False), \
             patch("whatsapp.jobs.enqueue_flow_execution"), \
             patch("whatsapp.jobs.drain_in_background"):
            outcome = router.authorise_flow_execution(pa, self.user)
        self.assertEqual(outcome.status, router.OUTCOME_PENDING)
        self.assertNotIn("✅", outcome)
