"""Regression coverage for deferred execution and customer-visible outcomes."""
from banking.test_fixtures import bank_channel_case
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from wallet.models import FundingIntent, ReversalEvidence, Transaction
from whatsapp import flows, router
from whatsapp.models import ConversationState, PendingAction, WaMessageLog, WhatsAppLink
from whatsapp.test_flows import MSISDN, _make_user, _transfer_action


@bank_channel_case
@override_settings(WHATSAPP_PROCESS_INLINE=False, WHATSAPP_FLOW_SETTLE_WAIT=0,
                   WHATSAPP_FLOW={"RESULT_SCREEN": True})
class DeferredExecutionBoundaryTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = _make_user()

    def test_queue_insert_failure_rolls_back_authorisation_and_job_together(self):
        from whatsapp.jobs import enqueue_flow_execution

        pa = _transfer_action(self.user)

        def insert_then_fail(action):
            enqueue_flow_execution(action)
            raise RuntimeError("queue failed before commit")

        with patch("whatsapp.jobs.enqueue_flow_execution", side_effect=insert_then_fail):
            with self.assertRaises(RuntimeError):
                router.authorise_flow_execution(pa, self.user)
        pa.refresh_from_db()
        self.assertEqual(pa.state, flows.FLOW_PIN_STATE)
        self.assertFalse(WaMessageLog.objects.filter(wa_message_id=f"flowexec-{pa.pk}").exists())
        self.assertFalse(flows._pending_meta(flows.sign_flow_token(pa)))

    def test_action_changed_during_pin_check_is_not_resurrected(self):
        for replacement in ("cancelled", "expired", "state_changed", "suspended"):
            with self.subTest(replacement=replacement):
                pa = _transfer_action(self.user)
                if replacement == "cancelled":
                    PendingAction.objects.filter(pk=pa.pk).delete()
                elif replacement == "expired":
                    PendingAction.objects.filter(pk=pa.pk).update(expires_at=timezone.now())
                elif replacement == "state_changed":
                    PendingAction.objects.filter(pk=pa.pk).update(state="amount")
                else:
                    type(self.user).objects.filter(pk=self.user.pk).update(is_active=False)
                outcome = router.authorise_flow_execution(pa, self.user)
                self.assertEqual(outcome.status, router.OUTCOME_FAILED)
                self.assertFalse(WaMessageLog.objects.filter(wa_message_id=f"flowexec-{pa.pk}").exists())

    def _unlock(self):
        pa = PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="unlock", state=flows.FLOW_PIN_STATE,
            payload={"resume": "balance", **router._unlock_proof(self.user, MSISDN)},
            expires_at=timezone.now() + timedelta(minutes=5))
        router.authorise_flow_execution(pa, self.user)
        pa.refresh_from_db()
        return pa

    def test_queued_unlock_does_not_authenticate_a_replaced_link(self):
        pa = self._unlock()
        WhatsAppLink.objects.filter(user=self.user).delete()
        WhatsAppLink.objects.create(user=self.user, wa_msisdn=MSISDN, status=WhatsAppLink.ACTIVE)
        with patch.object(router, "handle_inbound") as resumed:
            outcome = router.run_flow_execution(pa, self.user)
        self.assertEqual(outcome.status, router.OUTCOME_FAILED)
        resumed.assert_not_called()
        self.assertFalse(ConversationState.objects.filter(msisdn=MSISDN, last_verified__isnull=False).exists())

    def test_queued_unlock_is_revoked_when_credentials_change(self):
        pa = self._unlock()
        self.user.set_transaction_pin("638291")
        self.user.save(update_fields=["transaction_pin"])
        with patch.object(router, "handle_inbound") as resumed:
            outcome = router.run_flow_execution(pa, self.user)
        self.assertEqual(outcome.status, router.OUTCOME_FAILED)
        resumed.assert_not_called()

    def test_queued_unlock_does_not_resume_after_channel_is_unlinked(self):
        pa = self._unlock()
        WhatsAppLink.objects.filter(user=self.user).delete()
        with patch.object(router, "handle_inbound") as resumed:
            outcome = router.run_flow_execution(pa, self.user)
        self.assertEqual(outcome.status, router.OUTCOME_FAILED)
        resumed.assert_not_called()

    def test_pin_proof_cannot_adopt_a_credential_change_before_enqueue(self):
        from common.http import evaluate_transaction_pin

        pa = PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="unlock", state=flows.FLOW_PIN_STATE,
            payload={"resume": "balance"}, expires_at=timezone.now() + timedelta(minutes=5))

        def verify_then_change(user, pin):
            result = evaluate_transaction_pin(user, pin)
            type(user).objects.filter(pk=user.pk).update(password="credential-changed-after-proof")
            return result

        with patch("common.http.evaluate_transaction_pin", side_effect=verify_then_change):
            response = flows._submit_pin(flows.sign_flow_token(pa), {"pin": "1234"})
        self.assertEqual(response["data"]["status"], "❌ Not completed")
        self.assertFalse(WaMessageLog.objects.filter(wa_message_id=f"flowexec-{pa.pk}").exists())
        self.assertFalse(ConversationState.objects.filter(msisdn=MSISDN, last_verified__isnull=False).exists())

    def test_unlock_executor_rechecks_after_dispatcher_releases_its_user_lock(self):
        pa = self._unlock()
        execute = router._exec_unlock

        def change_then_execute(action, user, msisdn):
            type(user).objects.filter(pk=user.pk).update(password="changed-after-dispatch-lock")
            return execute(action, user, msisdn)

        with patch.object(router, "_exec_unlock", side_effect=change_then_execute), \
                patch.object(router, "handle_inbound") as resumed:
            outcome = router.run_flow_execution(pa, self.user)
        self.assertEqual(outcome.status, router.OUTCOME_FAILED)
        resumed.assert_not_called()
        self.assertFalse(ConversationState.objects.filter(msisdn=MSISDN, last_verified__isnull=False).exists())

    def test_current_queued_unlock_resumes_requested_details(self):
        pa = self._unlock()
        with patch.object(router, "handle_inbound") as resumed:
            router.run_flow_execution(pa, self.user)
        resumed.assert_called_once_with(MSISDN, "balance")
        self.assertTrue(ConversationState.objects.filter(msisdn=MSISDN, last_verified__isnull=False).exists())


@bank_channel_case
@override_settings(WHATSAPP_FLOW={"RESULT_SCREEN": True}, WHATSAPP_FLOW_SETTLE_WAIT=0.1)
class ReviewOutcomeBoundaryTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = _make_user()
        self.pa = _transfer_action(self.user)
        self.txn = Transaction.objects.create(
            user=self.user, amount=Decimal("5000"), direction=Transaction.OUT,
            service="Transfer", reference="wa-review-boundary",
            idempotency_key=f"wa-{self.pa.pk}", transaction_status=Transaction.SUCCESS)
        # Evidence-only holds are valid even without the older JSON marker.
        self.evidence = ReversalEvidence.objects.create(
            user=self.user, payout=self.txn, amount=Decimal("5000"),
            provider_reference="provider-review-boundary", provider_reference_hash="e" * 64,
            initial_reason="conflicting_outcome", reason="conflicting_outcome",
            state=ReversalEvidence.CONFLICT)

    def test_settlement_wait_does_not_claim_success_during_review(self):
        outcome = router._await_settlement(self.pa.pk, self.user, "transfer")
        self.assertEqual(outcome.status, router.OUTCOME_PENDING)
        self.assertIn("under review", outcome)

    def test_done_keeps_review_pending_until_evidence_is_resolved(self):
        token = flows.sign_flow_token(self.pa)
        flows.remember_pending(self.pa, self.user)
        request = {"action": "data_exchange", "flow_token": token, "data": {"close": True}}
        response = flows.handle_flow_request(request)
        self.assertEqual(response["data"]["status"], "⏳ Pending")
        self.assertIn("under review", response["data"]["message"])
        self.evidence.state = ReversalEvidence.RESOLVED
        self.evidence.resolved_amount = self.evidence.amount
        self.evidence.resolved_at = timezone.now()
        self.evidence.save(update_fields=["state", "resolved_amount", "resolved_at"])
        response = flows.handle_flow_request(request)
        self.assertEqual(response["data"]["status"], "✅ Successful")

    def _cache_success(self):
        self.evidence.state = ReversalEvidence.RESOLVED
        self.evidence.resolved_amount = self.evidence.amount
        self.evidence.resolved_at = timezone.now()
        self.evidence.save(update_fields=["state", "resolved_amount", "resolved_at"])
        token = flows.sign_flow_token(self.pa)
        flows.remember_pending(self.pa, self.user)
        flows.forget_pending(token, status="success")
        flows.remember_settled(self.pa, "Payment successful - the receipt is in your chat.")
        self.pa.delete()
        return token

    def test_cached_success_is_rechecked_for_later_review_on_every_reentry(self):
        token = self._cache_success()
        for hold in ("evidence", "card", "funding"):
            with self.subTest(hold=hold):
                if hold == "evidence":
                    self.evidence.state = ReversalEvidence.CONFLICT
                    self.evidence.save(update_fields=["state"])
                elif hold == "card":
                    self.evidence.state = ReversalEvidence.RESOLVED
                    self.evidence.save(update_fields=["state"])
                    self.txn.meta = {"card_funding": True, "card_balance_review": True}
                    self.txn.save(update_fields=["meta"])
                else:
                    self.txn.meta = {}
                    self.txn.save(update_fields=["meta"])
                    FundingIntent.objects.create(user=self.user, reference=self.txn.reference,
                        amount=self.txn.amount, status=FundingIntent.PAID, credited=True,
                        meta={"funding_review": {"active": True, "reason": "conflict"}})
                for action, data in (("INIT", {}), ("data_exchange", {"pin": "1234"}),
                                     ("data_exchange", {"close": True})):
                    response = flows.handle_flow_request({"action": action, "data": data, "flow_token": token})
                    self.assertEqual(response["data"]["status"], "⏳ Pending")
                    self.assertIn("under review", response["data"]["message"])
                self.assertEqual(flows.settled_outcome(token), "")

    def test_cached_success_rereads_a_later_failure_and_done_then_closes(self):
        token = self._cache_success()
        self.txn.transaction_status = Transaction.FAILED
        self.txn.save(update_fields=["transaction_status"])
        request = {"action": "data_exchange", "flow_token": token, "data": {"close": True}}
        response = flows.handle_flow_request(request)
        self.assertEqual(response["data"]["status"], "❌ Not completed")
        self.assertIn("extension_message_response", flows.handle_flow_request(request)["data"])

    def test_predebit_refusal_without_ledger_does_not_stay_pending(self):
        pa = _transfer_action(self.user)
        token = flows.sign_flow_token(pa)
        flows.remember_pending(pa, self.user)
        pa.delete()  # e.g. funds/limits check refused before a ledger debit
        response = flows.handle_flow_request({"action": "INIT", "flow_token": token})
        self.assertNotEqual(response["data"]["status"], "⏳ Pending")
        closed = flows.handle_flow_request({"action": "data_exchange", "flow_token": token,
                                            "data": {"close": True}})
        self.assertIn("extension_message_response", closed["data"])

    def test_legacy_cached_words_without_ledger_mapping_do_not_prove_success(self):
        token = self._cache_success()
        cache.delete(flows._transaction_key(token))
        response = flows.handle_flow_request({"action": "INIT", "flow_token": token})
        self.assertNotEqual(response["data"]["status"], "✅ Successful")

    def test_completed_fx_pointer_keeps_its_own_idempotency_prefix(self):
        self.pa.action_type = "convert"
        self.pa.save(update_fields=["action_type"])
        self.txn = Transaction.objects.create(
            user=self.user, amount=Decimal("5000"), direction=Transaction.OUT,
            service="Conversion", reference="wa-fx-review-boundary",
            idempotency_key=f"wa-fx-{self.pa.pk}", transaction_status=Transaction.SUCCESS)
        token = self._cache_success()
        response = flows.handle_flow_request({"action": "INIT", "flow_token": token})
        self.assertEqual(response["data"]["status"], "✅ Successful")

    def test_reviewed_transfer_does_not_issue_a_success_receipt(self):
        with patch.object(router, "reply_receipt") as receipt, patch.object(router, "reply"):
            outcome = router._transfer_outcome(self.pa, self.user, MSISDN, self.txn, replay=True)
        self.assertEqual(outcome.status, router.OUTCOME_PENDING)
        receipt.assert_not_called()


@bank_channel_case
class MalformedFlowHandleTests(TestCase):
    def test_invalid_handles_fail_before_database_or_unbounded_integer_conversion(self):
        for prefix, resolve in (("", flows.resolve_flow_token), ("ob", flows.resolve_onboarding_token),
                                ("ap", flows.resolve_approve_token), ("id", flows.resolve_identity_token)):
            for token in (None, {}, 12, prefix + "²." + "a" * 22,
                          prefix + "9" * 5000 + "." + "a" * 22,
                          prefix + "9223372036854775808." + "a" * 22,
                          prefix + "1." + "é" * 22):
                with self.subTest(prefix=prefix, value_type=type(token).__name__), self.assertNumQueries(0):
                    self.assertIsNone(resolve(token))

    @override_settings(WHATSAPP_FLOW={"RESULT_SCREEN": True})
    def test_malformed_payload_returns_a_renderable_failure(self):
        for token in ({}, [], 123, "9" * 5000):
            with self.subTest(value_type=type(token).__name__), self.assertNumQueries(0):
                response = flows.handle_flow_request({"action": "INIT", "flow_token": token})
                self.assertEqual(response["screen"], flows.RESULT_SCREEN)
                self.assertEqual(response["data"]["status"], "❌ Not completed")
