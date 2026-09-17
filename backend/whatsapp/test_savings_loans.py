"""Private confirmation and ledger invariants for WhatsApp savings/repayment."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from loans.models import Loan
from loans.services import repay
from savings.models import FixedSave
from savings.services import lock
from wallet.models import Transaction
from wallet.services import get_or_create_wallet

from . import flows, router, savings_loans
from .models import ConversationState, PendingAction, WhatsAppLink
from .test_flow_e2e import FlowContractMixin
from .test_flows import MSISDN, _make_user


@override_settings(
    WHATSAPP={"MODE": "sandbox", "TOKEN": "", "PHONE_NUMBER_ID": "1"},
    WHATSAPP_FLOW={"ID": "1", "CTA": "Confirm with PIN", "RESULT_SCREEN": True},
    WHATSAPP_PROCESS_INLINE=True, WA_REAUTH_IDLE_MINUTES=15,
)
class SavingsLoanFlowTests(FlowContractMixin, TestCase):
    def setUp(self):
        cache.clear()
        self.user = _make_user()
        self.user.set_transaction_pin("123456")
        self.user.save()
        router._mark_verified(MSISDN)
        for name, value in (("flows_live", True), ("send_flow", {"success": True}),
                            ("send_text", {"success": True})):
            mock = patch.object(router, name, return_value=value)
            setattr(self, name, mock.start())
            self.addCleanup(mock.stop)

    def say(self, text):
        return router.handle_inbound(MSISDN, text)

    def balance(self):
        return get_or_create_wallet(self.user).balance

    def action(self):
        return PendingAction.objects.filter(msisdn=MSISDN).latest("pk")

    def message(self):
        return self.send_text.call_args.args[1]

    def savings(self, amount="5000", days="30"):
        self.say("new savings")
        self.say(amount)
        self.say(days)
        return self.action()

    def loan(self, user=None, principal="10000", interest="450"):
        return Loan.objects.create(user=user or self.user, principal=Decimal(principal),
            interest=Decimal(interest), tenure_days=30, reference=f"ZLN-{Loan.objects.count() + 1}",
            due_date=timezone.now() + timedelta(days=30))

    def repayment(self, amount="1000"):
        self.say("repay loan")
        self.say(amount)
        return self.action()

    def pin(self, pa, value="123456", **extra):
        return flows.handle_flow_request({"action": "data_exchange", "flow_token": flows.sign_flow_token(pa),
                                          "data": {"pin": value, **extra}})

    def test_savings_quote_uses_existing_screen_and_moves_nothing_before_pin(self):
        pa = self.savings()
        self.assertEqual(pa.state, flows.FLOW_PIN_STATE)
        self.assertEqual(self.balance(), Decimal("50000"))
        self.assertFalse(FixedSave.objects.exists())
        opened = flows.handle_flow_request({"action": "INIT", "flow_token": flows.sign_flow_token(pa)})
        self.assertScreen(opened)
        self.assertEqual(opened["screen"], flows.PIN_SCREEN)
        self.assertIn("30 days", opened["data"]["recipient"])
        self.assertIn("Annual rate 12", opened["data"]["details"])
        self.assertIn("No early withdrawal", opened["data"]["details"])
        result = self.pin(pa)
        self.assertEqual(FixedSave.objects.get().principal, Decimal("5000"))
        self.assertEqual(self.balance(), Decimal("45000"))
        self.assertNotIn("Pending", str(result))
        self.assertEqual(Transaction.objects.get(idempotency_key=f"wa-{pa.pk}").transaction_status, Transaction.SUCCESS)

    def test_wrong_pin_then_retry_does_not_move_money_until_valid(self):
        pa = self.savings()
        response = self.pin(pa, "999999")
        self.assertScreen(response, standing_on=flows.PIN_SCREEN)
        self.assertFalse(FixedSave.objects.exists())
        self.assertEqual(self.balance(), Decimal("50000"))
        self.pin(pa)
        self.assertEqual(FixedSave.objects.count(), 1)

    def test_flow_payload_cannot_change_amount_or_terms(self):
        pa = self.savings()
        self.pin(pa, amount="40000", days=365, rate="1", interest="999999")
        plan = FixedSave.objects.get()
        self.assertEqual((plan.principal, plan.duration_days, plan.rate), (Decimal("5000"), 30, Decimal("0.12")))

    def test_product_confirmation_does_not_require_an_app_even_for_app_users(self):
        with patch.object(router, "_has_app_session", return_value=True):
            self.savings()
        card = self.send_flow.call_args.kwargs
        self.assertEqual(card["screen"], flows.PIN_SCREEN)
        self.assertEqual(card["cta"], "Confirm with PIN")
        self.assertNotIn("zitch://", card["body"])
        self.assertNotIn("fingerprint", card["body"])

    def test_duplicate_pin_submission_creates_one_plan_and_debit(self):
        pa = self.savings()
        self.pin(pa)
        self.pin(pa)
        self.assertEqual(FixedSave.objects.count(), 1)
        self.assertEqual(Transaction.objects.filter(idempotency_key=f"wa-{pa.pk}").count(), 1)
        self.assertEqual(self.balance(), Decimal("45000"))

    def test_chat_pin_never_authorises_a_product(self):
        self.savings()
        self.say("123456")
        self.assertIn("secure screen", self.message())
        self.assertFalse(FixedSave.objects.exists())
        self.assertEqual(self.balance(), Decimal("50000"))

    def test_missing_flow_has_no_sms_or_plaintext_pin_fallback(self):
        self.flows_live.return_value = False
        with patch.object(router, "send_sms") as sms:
            self.say("new savings")
            self.say("5000")
            self.say("30")
        sms.assert_not_called()
        self.assertFalse(PendingAction.objects.filter(msisdn=MSISDN).exists())
        self.assertIn("never type your PIN", self.message())
        self.assertFalse(FixedSave.objects.exists())

    def test_failed_secure_card_fails_closed(self):
        self.send_flow.return_value = {"success": False}
        self.say("new savings")
        self.say("5000")
        self.say("30")
        self.assertFalse(PendingAction.objects.filter(msisdn=MSISDN).exists())
        self.assertIn("No request was submitted", self.message())

    def test_bad_amounts_and_terms_do_not_open_confirmation(self):
        self.say("new savings")
        for amount in ("-5000", "0", "0.001", "5000.999", "NaN", "Infinity", "1e8", "9" * 40, "999", "9999999999999"):
            self.say(amount)
            self.assertEqual(self.action().state, "amount", amount)
        self.say("5k")
        for days in ("0", "-30", "30.5", "60", "999999999999"):
            self.say(days)
            self.assertEqual(self.action().state, "days", days)
        self.send_flow.assert_not_called()
        self.assertFalse(FixedSave.objects.exists())

    def test_changed_rate_requires_fresh_quote(self):
        pa = self.savings()
        with patch.dict(FixedSave.RATES, {30: Decimal("0.13")}):
            self.pin(pa)
        self.assertFalse(FixedSave.objects.exists())
        self.assertEqual(self.balance(), Decimal("50000"))
        self.assertIn("fresh quote", self.message())

    def test_balance_drop_between_confirmation_and_execution_fails_without_partial_plan(self):
        pa = self.savings("40000")
        lock(self.user, Decimal("20000"), 30, idempotency_key="app-other-save")
        response = self.pin(pa)
        self.assertEqual(FixedSave.objects.count(), 1)
        self.assertEqual(self.balance(), Decimal("30000"))
        self.assertFalse(Transaction.objects.filter(idempotency_key=f"wa-{pa.pk}").exists())
        self.assertIn("Insufficient", str(response))

    def test_cancelled_or_expired_quote_cannot_execute(self):
        pa = self.savings()
        self.say("cancel")
        self.pin(pa)
        expired = self.savings()
        PendingAction.objects.filter(pk=expired.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.pin(expired)
        self.assertFalse(FixedSave.objects.exists())
        self.assertEqual(self.balance(), Decimal("50000"))

    def test_post_commit_failure_retries_same_key_before_balance_check(self):
        pa = self.savings("40000")
        PendingAction.objects.filter(pk=pa.pk).update(state=router.EXECUTING_STATE)
        with patch.object(router, "reply", side_effect=RuntimeError("reply bookkeeping failed")):
            with self.assertRaises(RuntimeError):
                router.run_flow_execution(pa, self.user)
        self.assertTrue(PendingAction.objects.filter(pk=pa.pk).exists())
        self.assertEqual(self.balance(), Decimal("10000"))
        result = router.run_flow_execution(pa, self.user)
        self.assertEqual(result.status, router.OUTCOME_SUCCESS)
        self.assertEqual(FixedSave.objects.count(), 1)
        self.assertEqual(self.balance(), Decimal("10000"))

    def test_partial_repayment_and_replayed_pin(self):
        loan = self.loan()
        pa = self.repayment("1k")
        self.assertEqual(self.balance(), Decimal("50000"))
        fields = router._flow_fields(pa)
        self.assertIn(loan.reference, fields["details"])
        self.assertIn("Outstanding at quote: ₦10,450.00", fields["details"])
        self.assertEqual(fields["amount"], "₦1,000.00")
        self.pin(pa)
        self.pin(pa)
        loan.refresh_from_db()
        self.assertEqual(loan.amount_repaid, Decimal("1000"))
        self.assertEqual(self.balance(), Decimal("49000"))
        self.assertEqual(Transaction.objects.filter(idempotency_key=f"wa-{pa.pk}").count(), 1)

    def test_full_repayment_closes_loan(self):
        loan = self.loan()
        pa = self.repayment("full")
        self.pin(pa)
        loan.refresh_from_db()
        self.assertEqual(loan.status, Loan.REPAID)
        self.assertEqual(loan.outstanding, Decimal("0"))
        self.assertEqual(self.balance(), Decimal("39550"))

    def test_repayment_rechecks_outstanding_after_app_payment(self):
        loan = self.loan()
        pa = self.repayment("full")
        repay(self.user, loan, Decimal("1000"), idempotency_key="app-partial")
        self.pin(pa)
        self.assertEqual(Transaction.objects.get(idempotency_key=f"wa-{pa.pk}").amount, Decimal("9450"))
        self.assertEqual(self.balance(), Decimal("39550"))

    def test_stale_confirm_cannot_repay_replacement_loan(self):
        loan = self.loan()
        pa = self.repayment("full")
        repay(self.user, loan, loan.outstanding, idempotency_key="app-full")
        replacement = self.loan()
        self.pin(pa)
        replacement.refresh_from_db()
        self.assertEqual(replacement.amount_repaid, Decimal("0"))
        self.assertFalse(Transaction.objects.filter(idempotency_key=f"wa-{pa.pk}").exists())
        self.assertIn("already repaid", self.message())

    def test_repayment_of_another_users_loan_is_refused(self):
        other = get_user_model().objects.create(username="other", phone="08020000002")
        foreign = self.loan(user=other)
        own = self.loan()
        pa = self.repayment()
        pa.payload["loan_ref"] = foreign.reference
        pa.save(update_fields=["payload"])
        self.pin(pa)
        own.refresh_from_db()
        foreign.refresh_from_db()
        self.assertEqual(own.amount_repaid, Decimal("0"))
        self.assertEqual(foreign.amount_repaid, Decimal("0"))
        self.assertEqual(self.balance(), Decimal("50000"))

    def test_overpayment_requires_an_explicit_valid_amount(self):
        self.loan()
        self.say("repay loan")
        self.say("20000")
        self.assertEqual(self.action().state, "amount")
        self.assertIn("or less", self.message())
        self.send_flow.assert_not_called()

    def test_no_active_loan_does_not_offer_unapproved_borrowing(self):
        self.say("repay loan")
        self.assertFalse(PendingAction.objects.filter(msisdn=MSISDN).exists())
        self.assertIn("don't have an active", self.message())
        self.assertNotIn("borrow up to", self.message())
        self.assertNotIn("Zitch app", self.message())

    def test_overdue_loan_is_labelled(self):
        loan = self.loan()
        Loan.objects.filter(pk=loan.pk).update(due_date=timezone.now() - timedelta(days=1))
        self.say("my loan")
        self.assertIn("overdue", self.message())

    def test_security_changes_after_authorisation_block_new_debit(self):
        for change in ("pin", "password", "locked", "reset_required", "relink", "amount"):
            with self.subTest(change=change):
                self.user.refresh_from_db()
                self.user.set_transaction_pin("123456")
                self.user.save()
                pa = self.savings()
                PendingAction.objects.filter(pk=pa.pk).update(state=router.EXECUTING_STATE)
                if change == "pin":
                    self.user.set_transaction_pin("654321")
                    self.user.save()
                elif change == "password":
                    self.user.set_password("changed-password")
                    self.user.save()
                elif change == "locked":
                    get_user_model().objects.filter(pk=self.user.pk).update(pin_locked_until=timezone.now() + timedelta(hours=1))
                elif change == "reset_required":
                    get_user_model().objects.filter(pk=self.user.pk).update(pin_reset_required=True)
                elif change == "relink":
                    WhatsAppLink.objects.filter(user=self.user).delete()
                    WhatsAppLink.objects.create(user=self.user, wa_msisdn=MSISDN, status=WhatsAppLink.ACTIVE)
                else:
                    pa.payload["amount"] = "10000"
                    pa.save(update_fields=["payload"])
                result = router.run_flow_execution(pa, self.user)
                self.assertEqual(result.status, router.OUTCOME_FAILED)
                self.assertEqual(self.balance(), Decimal("50000"))
        self.assertFalse(FixedSave.objects.exists())

    def test_expired_queued_product_with_no_ledger_entry_does_not_debit(self):
        pa = self.savings()
        PendingAction.objects.filter(pk=pa.pk).update(
            state=router.EXECUTING_STATE, expires_at=timezone.now() - timedelta(seconds=1))
        result = router.run_flow_execution(pa, self.user)
        self.assertEqual(result.status, router.OUTCOME_FAILED)
        self.assertEqual(self.balance(), Decimal("50000"))

    def test_completed_retry_survives_expiry_and_pin_change_without_second_debit(self):
        pa = self.savings()
        PendingAction.objects.filter(pk=pa.pk).update(state=router.EXECUTING_STATE)
        savings_loans.execute_product(pa, self.user, MSISDN)
        PendingAction.objects.filter(pk=pa.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.user.set_transaction_pin("654321")
        self.user.save()
        result = router.run_flow_execution(pa, self.user)
        self.assertEqual(result.status, router.OUTCOME_SUCCESS)
        self.assertEqual(self.balance(), Decimal("45000"))
        self.assertEqual(FixedSave.objects.count(), 1)

    def test_products_call_no_external_money_provider_or_loan_disbursement(self):
        self.loan()
        with patch("requests.sessions.Session.request", side_effect=AssertionError("external call")) as network, \
             patch("loans.services.disburse", side_effect=AssertionError("new loan")) as disburse:
            self.pin(self.savings())
            self.pin(self.repayment())
        network.assert_not_called()
        disburse.assert_not_called()
        self.assertEqual(Loan.objects.count(), 1)
        self.assertEqual(self.balance(), Decimal("44000"))

    def test_freeze_or_unlink_before_execution_blocks_debit(self):
        for disabled in ("freeze", "unlink"):
            with self.subTest(disabled=disabled):
                pa = self.savings()
                if disabled == "freeze":
                    get_user_model().objects.filter(pk=self.user.pk).update(is_active=False)
                else:
                    WhatsAppLink.objects.filter(user=self.user).delete()
                result = router.run_flow_execution(pa, self.user)
                self.assertEqual(result.status, router.OUTCOME_FAILED)
                self.assertEqual(self.balance(), Decimal("50000"))
                get_user_model().objects.filter(pk=self.user.pk).update(is_active=True)
        self.assertFalse(FixedSave.objects.exists())

    def test_savings_list_paginates_and_plan_selection_is_owner_scoped(self):
        plans = [lock(self.user, Decimal("1000"), 30, idempotency_key=f"plan-{n}") for n in range(6)]
        self.say("savings")
        self.assertIn("savings page 2", self.message())
        self.assertNotIn(f"Details: savings plan {plans[0].pk}", self.message())
        self.say("savings page 2")
        self.assertIn(f"Details: savings plan {plans[0].pk}", self.message())
        self.say(f"savings plan {plans[0].pk}")
        self.assertIn(plans[0].reference, self.message())
        self.assertIn("cannot be topped up", self.message())
        other = get_user_model().objects.create(username="other", phone="08020000002")
        FixedSave.objects.filter(pk=plans[0].pk).update(user=other)
        self.say(f"savings plan {plans[0].pk}")
        self.assertNotIn(plans[0].reference, self.message())
        self.assertIn("isn't available", self.message())

    def test_maturity_pays_once_and_history_shows_completed_plan(self):
        plan = lock(self.user, Decimal("1000"), 30, idempotency_key="mature-save")
        FixedSave.objects.filter(pk=plan.pk).update(matures_at=timezone.now() - timedelta(seconds=1))
        self.say("savings")
        self.say("savings history")
        self.say(f"savings plan {plan.pk}")
        self.assertIn("Paid into your wallet", self.message())
        self.assertEqual(self.balance(), Decimal("50000") + plan.interest)
        self.assertEqual(Transaction.objects.filter(reference=f"{plan.reference}-M").count(), 1)

    def test_expired_session_protects_all_product_reads_including_ai_dispatch(self):
        for command in ("13", "14", "my loan", "manage savings", "savings history", "savings plan 1", "repay loan"):
            with self.subTest(command=command):
                router._clear_actions(MSISDN)
                ConversationState.objects.filter(msisdn=MSISDN).update(last_verified=None)
                self.say(command)
                self.assertEqual(self.action().action_type, "unlock")
                self.assertEqual(router._flow_fields(self.action())["balance"], "")
        router._clear_actions(MSISDN)
        router.dispatch_intent(self.user, MSISDN, {"name": "check_savings_balance", "input": {}})
        self.assertEqual(self.action().action_type, "unlock")

    def test_failed_maturity_check_does_not_claim_payout(self):
        with patch.object(savings_loans, "settle_user_maturities", side_effect=RuntimeError("db down")):
            self.say("savings")
        self.assertIn("couldn't confirm", self.message())
        self.assertNotIn("paid out", self.message())

    def test_existing_unsupported_plan_changes_are_explained_without_app_handoff(self):
        for command in ("withdraw savings", "top up savings", "change savings"):
            self.say(command)
            self.assertIn("cannot", self.message())
            self.assertNotIn("Zitch app", self.message())
            self.assertFalse(PendingAction.objects.filter(msisdn=MSISDN).exists())
