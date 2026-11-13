"""Regressions from the WhatsApp channel audit.

- the Flow transfer form quotes, checks and stores the bank fee exactly as the
  typed-chat path does, so the fee debited is the fee shown on the PIN screen;
- a provider-success/ledger "state conflict" is under review, never "failed";
- "ok" / "done" typed with a card open keeps the payment instead of dropping it;
- a row tapped on an expired list is never read as a main-menu number;
- the bank picker accepts the exact bank name its prompt invites.
"""
import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import Client, TestCase
from django.utils import timezone

from transfers.models import Bank
from transfers.services import PayoutError
from wallet.models import Transaction
from wallet.services import get_or_create_wallet
from wallet.transfer_fees import FEE_FOR_KEY

from . import router
from .flows import FLOW_FORM_STATE, FLOW_PIN_STATE, sign_flow_token
from .models import PendingAction, WaMessageLog
from .test_flows import BANK_FEE_5K, MSISDN, _make_user


def _out(msisdn=MSISDN):
    return list(WaMessageLog.objects.filter(msisdn=msisdn, direction=WaMessageLog.OUT)
                .order_by("created").values_list("text", flat=True))


class FlowTransferFeeTests(TestCase):
    """The form chained straight into the PIN screen without a fee: the screen
    showed none, the funds check ignored it, and execute_payout then quoted and
    debited one anyway - so a whole-balance send passed the PIN and failed."""

    def setUp(self):
        Bank.objects.create(code="gtb", name="GTBank", bank_code="058", color="#e30613", active=True)
        Bank.objects.create(code="wema", name="Wema Bank", bank_code="035", color="#909", active=True)

    def _pa(self, user):
        return PendingAction.objects.create(
            user=user, msisdn=MSISDN, action_type="transfer", state=FLOW_FORM_STATE,
            payload={"pin_attempts": 0}, expires_at=timezone.now() + timedelta(minutes=5))

    def _submit(self, pa, **data):
        from .flows import handle_flow_request

        return handle_flow_request({"action": "data_exchange",
                                    "flow_token": sign_flow_token(pa), "data": data})

    def _form(self, pa, bank):
        with patch("utility.providers.payout_resolve_account",
                   return_value={"success": True, "name": "Ada Eze"}):
            return self._submit(pa, amount="5000", account_number="0123456789", bank=bank)

    def test_the_pin_screen_shows_the_fee_and_the_same_fee_is_debited(self):
        from .flows import PIN_CHAIN

        user = _make_user()
        pa = self._pa(user)
        resp = self._form(pa, "gtb")
        self.assertEqual(resp["screen"], PIN_CHAIN)
        self.assertIn("Bank fee ₦10.75 · Total ₦5,010.75", resp["data"]["details"])
        pa.refresh_from_db()
        self.assertEqual(Decimal(pa.payload["fee"]), BANK_FEE_5K)

        before = get_or_create_wallet(user).balance
        real = router.execute_payout
        with patch.object(router, "execute_payout", wraps=real) as payout:
            self._submit(pa, pin="1234")
        self.assertEqual(payout.call_args.kwargs["fee"], BANK_FEE_5K)   # shown == charged
        fee_row = Transaction.objects.get(user=user, meta__has_key=FEE_FOR_KEY)
        self.assertEqual(fee_row.amount, BANK_FEE_5K)
        self.assertEqual(get_or_create_wallet(user).balance,
                         before - Decimal("5000") - BANK_FEE_5K)

    def test_a_balance_that_covers_the_amount_but_not_the_fee_is_refused_on_the_form(self):
        from .flows import TRANSFER_FORM

        user = _make_user(balance="5005")
        pa = self._pa(user)
        resp = self._form(pa, "gtb")
        self.assertEqual(resp["screen"], TRANSFER_FORM)
        self.assertIn("bank fee", resp["data"]["error"])
        self.assertIn("5,010.75", resp["data"]["error"])
        pa.refresh_from_db()
        self.assertEqual(pa.state, FLOW_FORM_STATE)                     # no PIN screen
        self.assertFalse(Transaction.objects.filter(user=user, direction=Transaction.OUT).exists())

    def test_a_wema_transfer_has_no_fee_line_and_can_send_the_whole_balance(self):
        from .flows import PIN_CHAIN

        user = _make_user(balance="5000")
        pa = self._pa(user)
        resp = self._form(pa, "wema")
        self.assertEqual(resp["screen"], PIN_CHAIN)
        self.assertNotIn("Bank fee", resp["data"]["details"])
        self.assertNotIn("Total", resp["data"]["details"])
        pa.refresh_from_db()
        self.assertEqual(Decimal(pa.payload["fee"]), Decimal("0"))


class StateConflictIsUnderReviewTests(TestCase):
    """The bank said it paid; the ledger disagrees. The money has probably left,
    so "failed" invited the customer to send it again."""

    def setUp(self):
        self.user = _make_user()
        Bank.objects.create(code="gtb", name="GTBank", bank_code="058", active=True)
        self.pa = PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="transfer", state=FLOW_PIN_STATE,
            payload={"amount": "5000", "account": "0123456789", "bank_code": "058",
                     "bank_name": "GTBank", "name": "JOHN DOE", "fee": "10.75",
                     "pin_attempts": 0},
            expires_at=timezone.now() + timedelta(minutes=5))

    def test_state_conflict_is_pending_review_not_failed(self):
        def conflict(user, amount, *args, **kwargs):
            Transaction.objects.create(
                user=user, service="Transfer to JOHN DOE", amount=amount,
                direction=Transaction.OUT, transaction_status=Transaction.PENDING,
                reference="ZWACONFLICT01", idempotency_key=kwargs["idempotency_key"])
            raise PayoutError("state_conflict", "The bank response conflicted with the ledger.")

        with patch.object(router, "payout_resolve_account",
                          return_value={"success": True, "name": "John Doe", "mock": True}), \
             patch.object(router, "execute_payout", side_effect=conflict):
            outcome = router._exec_transfer(self.pa, self.user, MSISDN)

        self.assertEqual(outcome.status, router.OUTCOME_PENDING)
        self.assertIn("under review", outcome)
        self.assertIn("Do not send it again", outcome)
        self.assertIn("ZWACONFLICT01", outcome)
        self.assertNotIn("failed", outcome.lower())
        self.assertTrue(Transaction.objects.get(reference="ZWACONFLICT01")
                        .meta.get("wa_awaiting_settlement"))
        self.assertEqual(_out()[-1], str(outcome))


class AcknowledgementKeepsTheCardTests(TestCase):
    """"ok" typed under an open PIN screen answered "leaving that payment
    unconfirmed" and dropped the action the customer was agreeing to."""

    def setUp(self):
        self.user = _make_user()

    def _armed(self, state=FLOW_PIN_STATE):
        return PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="transfer", state=state,
            payload={"amount": "5000", "account": "0123456789", "bank_code": "058",
                     "bank_name": "GTBank", "name": "JOHN DOE", "pin_attempts": 0},
            expires_at=timezone.now() + timedelta(minutes=5))

    def test_acknowledgements_keep_the_pin_screen(self):
        for said in ("ok", "Yes", "done", "alright", "Okay, thanks", "👍"):
            with self.subTest(said=said):
                pa = self._armed()
                router.handle_inbound(MSISDN, said)
                last = _out()[-1]
                self.assertIn("secure screen", last)
                self.assertNotIn("leaving", last)
                self.assertTrue(PendingAction.objects.filter(pk=pa.pk).exists())
                pa.delete()

    def test_acknowledgement_keeps_the_transfer_form(self):
        pa = self._armed(FLOW_FORM_STATE)
        router.handle_inbound(MSISDN, "ok")
        self.assertIn("Send money", _out()[-1])
        self.assertTrue(PendingAction.objects.filter(pk=pa.pk).exists())

    def test_cancel_still_cancels_and_a_command_still_escapes(self):
        pa = self._armed()
        router.handle_inbound(MSISDN, "cancel")
        self.assertIn("cancelled", _out()[-1].lower())
        self.assertFalse(PendingAction.objects.filter(pk=pa.pk).exists())

        pa = self._armed()
        router.handle_inbound(MSISDN, "balance")
        self.assertTrue(any("leaving that payment" in t for t in _out()))
        self.assertFalse(PendingAction.objects.filter(pk=pa.pk).exists())


class StaleListTapTests(TestCase):
    """A bank picker's rows are "1".."6". Tapped after the picker expired, "5"
    opened Convert and "2" opened Send money - a different product."""

    def setUp(self):
        self.client = Client()
        self.user = _make_user()
        Bank.objects.create(code="gtb", name="GTBank", bank_code="058", active=True)

    def _post(self, message, mid):
        message = {"from": MSISDN, "id": mid, **message}
        event = {"entry": [{"changes": [{"value": {"messages": [message]}}]}]}
        self.client.post("/webhooks/whatsapp", data=json.dumps(event),
                         content_type="application/json")

    def _tap(self, row_id, mid):
        self._post({"type": "interactive", "interactive": {
            "type": "list_reply", "list_reply": {"id": row_id, "title": "GTBank"}}}, mid)

    def _picker(self, *, expired):
        return PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="transfer", state="bank_pick",
            payload={"amount": "5000", "account": "0123456789", "bank_choices": ["gtb"],
                     "pin_attempts": 0},
            expires_at=timezone.now() + timedelta(minutes=-1 if expired else 5))

    def test_a_tap_from_an_expired_picker_starts_nothing(self):
        for index, row in enumerate(("5", "2", "1")):
            with self.subTest(row=row):
                self._picker(expired=True)
                self._tap(row, f"stale-{index}")
                self.assertIn("list has expired", _out()[-1])
                self.assertFalse(PendingAction.objects.filter(msisdn=MSISDN).exists())

    def test_a_tap_with_no_picker_at_all_starts_nothing(self):
        self._tap("2", "orphan-1")
        self.assertIn("list has expired", _out()[-1])
        self.assertFalse(PendingAction.objects.filter(msisdn=MSISDN).exists())

    def test_a_tap_on_a_live_picker_still_picks_the_bank(self):
        self._picker(expired=False)
        with patch.object(router, "payout_resolve_account",
                          return_value={"success": True, "name": "Ada Eze"}):
            self._tap("1", "live-1")
        self.assertTrue(any("Confirm transfer" in t for t in _out()))

    def test_a_typed_menu_number_still_works(self):
        self._post({"type": "text", "text": {"body": "1"}}, "typed-1")
        self.assertNotIn("list has expired", _out()[-1])
        self.assertIn("₦", _out()[-1])


class BankPickerTypedNameTests(TestCase):
    """The picker said "type the bank's exact name" and then refused one."""

    def setUp(self):
        self.user = _make_user()
        for code, name, bank_code in (("gtb", "GTBank", "058"), ("kuda", "Kuda", "50211"),
                                      ("opay", "OPay", "999992")):
            Bank.objects.create(code=code, name=name, bank_code=bank_code, active=True)
        self.pa = PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="transfer", state="bank_pick",
            payload={"amount": "5000", "account": "0123456789",
                     "bank_choices": ["gtb", "kuda"], "pin_attempts": 0},
            expires_at=timezone.now() + timedelta(minutes=5))

    def test_an_exact_bank_name_is_accepted(self):
        with patch.object(router, "payout_resolve_account",
                          return_value={"success": True, "name": "Ada Eze"}) as enquiry:
            router.handle_inbound(MSISDN, "kuda")
        enquiry.assert_called_once_with("0123456789", "50211")
        self.assertTrue(any("Confirm transfer" in t and "Kuda" in t for t in _out()))

    def test_a_name_that_is_not_a_bank_reprompts_with_honest_wording(self):
        router.handle_inbound(MSISDN, "kud")
        self.assertIn("exact name", _out()[-1])
        self.pa.refresh_from_db()
        self.assertEqual(self.pa.state, "bank_pick")
