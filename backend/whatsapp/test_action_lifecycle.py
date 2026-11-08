"""Chat navigation cannot cancel a payment already authorised for execution."""
from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from datetime import timedelta
from . import router
from .models import PendingAction
from .test_flows import _make_user, MSISDN


class AuthorisedActionLifecycleTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.action = PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="transfer",
            state=router.EXECUTING_STATE, payload={"amount": "56"},
            expires_at=timezone.now() + timedelta(minutes=10))

    def test_cancel_keeps_authorised_action_and_does_not_claim_cancellation(self):
        with patch.object(router, "reply") as reply:
            router.handle_inbound(MSISDN, "cancel")
        self.assertTrue(PendingAction.objects.filter(pk=self.action.pk).exists())
        message = reply.call_args.args[1]
        self.assertIn("cannot be cancelled", message)
        self.assertNotIn("Okay, cancelled", message)

    def test_menu_keeps_authorised_action(self):
        with patch.object(router, "send_menu"):
            router.handle_inbound(MSISDN, "menu")
        self.assertTrue(PendingAction.objects.filter(pk=self.action.pk).exists())

    def test_executor_retires_only_its_action_after_outcome(self):
        unrelated = PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="verification_web",
            state="web_pin", payload={}, expires_at=timezone.now() + timedelta(minutes=10))
        with patch.object(router, "_exec_transfer", return_value=router.Outcome("Sent", router.OUTCOME_SUCCESS)):
            router.run_flow_execution(self.action, self.user)
        self.assertFalse(PendingAction.objects.filter(pk=self.action.pk).exists())
        self.assertTrue(PendingAction.objects.filter(pk=unrelated.pk).exists())

    def test_exception_preserves_authorised_action_for_durable_retry(self):
        with patch.object(router, "_exec_transfer", side_effect=RuntimeError("unavailable")):
            with self.assertRaises(RuntimeError):
                router.run_flow_execution(self.action, self.user)
        self.assertTrue(PendingAction.objects.filter(pk=self.action.pk).exists())

    def test_cancel_unlock_does_not_describe_a_payment(self):
        self.action.action_type = "unlock"
        self.action.save(update_fields=["action_type"])
        with patch.object(router, "reply") as reply:
            router.handle_inbound(MSISDN, "cancel")
        self.assertFalse(PendingAction.objects.filter(pk=self.action.pk).exists())
        self.assertNotIn("payment", reply.call_args.args[1])
