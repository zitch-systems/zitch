"""The confirm screen shows what you have, and what you said the money is for.

Balance: deciding whether to send ₦50,000 needs the balance in front of you, and
the PIN screen is the last one before the money goes. Without it the only way to
check was to abandon the payment, ask, and start over — so the screen that most
needed the number was the one that never showed it.

Narration: optional everywhere, and never a reason to refuse a payment. It
reaches the beneficiary's statement, the receipt, the settlement alert and the
confirm screen, because those are the four places someone asks "what was this
for" — and the customer only types it once.
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from whatsapp import flows, router
from whatsapp.models import PendingAction
from whatsapp.test_flows import MSISDN, _make_user
from wallet.services import get_or_create_wallet


def _transfer(user, **payload):
    return PendingAction.objects.create(
        user=user, msisdn=MSISDN, action_type="transfer", state=flows.FLOW_PIN_STATE,
        payload={"amount": "1000", "account": "0123456789", "bank_name": "GTBank",
                 "name": "john doe", "pin_attempts": 0, **payload},
        expires_at=timezone.now() + timedelta(minutes=5))


class CleanNarrationTests(TestCase):
    """Bounded at the boundary, because both consumers render it: the bank puts
    it on a statement and the receipt draws it into an image."""

    def test_blank_is_an_ordinary_answer(self):
        for empty in (None, "", "   ", "\n"):
            self.assertEqual(flows.clean_narration(empty), "")

    def test_newlines_collapse_rather_than_refuse(self):
        """Dictated and pasted notes arrive with newlines. Refusing a payment
        over whitespace would be a strange place to be strict."""
        self.assertEqual(flows.clean_narration("rent\nfor  June\t2026"), "rent for June 2026")

    def test_it_is_capped(self):
        self.assertEqual(len(flows.clean_narration("x" * 500)), flows.NARRATION_MAX)

    def test_control_characters_never_survive(self):
        self.assertEqual(flows.clean_narration("re\x00nt\x07"), "re nt")


class ConfirmScreenTests(TestCase):

    def setUp(self):
        self.user = _make_user()

    def test_the_balance_is_on_the_screen_that_spends_it(self):
        wallet = get_or_create_wallet(self.user)
        wallet.balance = Decimal("12500")
        wallet.save(update_fields=["balance"])
        fields = router._flow_fields(_transfer(self.user))
        self.assertIn("12,500.00", fields["balance"])

    def test_a_balance_we_cannot_read_never_takes_down_the_payment(self):
        """The number is decoration on a confirm screen. A confirm screen that
        fails to render because of it is not."""
        with patch("whatsapp.router.get_or_create_wallet", side_effect=RuntimeError("db")):
            fields = router._flow_fields(_transfer(self.user))
        self.assertEqual(fields["balance"], "")
        self.assertIn("JOHN DOE", fields["recipient"])

    def test_narration_reaches_the_screen_and_is_marked_as_a_note(self):
        fields = router._flow_fields(_transfer(self.user, narration="Rent"))
        self.assertEqual(fields["narration"], "Note: Rent")

    def test_no_narration_is_blank_not_absent(self):
        """Declared-but-absent is the contract mismatch that shows "Couldn't
        load content. Try again later." instead of an ending."""
        fields = router._flow_fields(_transfer(self.user))
        self.assertEqual(fields["narration"], "")
        self.assertIn("balance", fields)

    def test_every_action_type_supplies_every_declared_property(self):
        """No branch may ship a dict missing a property the screen declares —
        including the fallback that itemises nothing."""
        needed = {"balance", "amount", "recipient", "details", "narration"}
        for action_type, payload in (
            ("transfer", {"amount": "1000", "name": "a", "bank_name": "b", "account": "1"}),
            ("airtime", {"amount": "500", "net": "1", "phone": "08010000000"}),
            ("data", {"price": "1000", "net": "1", "plan_name": "1GB", "phone": "08010000000"}),
            ("electricity", {"amount": "3000", "disco": "1", "meter": "1234", "meter_type": "prepaid"}),
            ("cable", {"price": "4000", "prov": "1", "plan_name": "Compact", "iuc": "1234"}),
            ("convert", {}),                       # the un-itemised fallback
            ("airtime", {}),                       # KeyError -> same fallback
        ):
            pa = PendingAction.objects.create(
                user=self.user, msisdn=MSISDN, action_type=action_type,
                state=flows.FLOW_PIN_STATE, payload=payload,
                expires_at=timezone.now() + timedelta(minutes=5))
            self.assertEqual(set(router._flow_fields(pa)), needed, action_type)
            pa.delete()

    def test_the_pin_screen_response_declares_the_same_properties(self):
        """The endpoint's answer is the other half of the same contract."""
        screen = flows._pin_screen(router._flow_fields(_transfer(self.user)))
        self.assertEqual(set(screen["data"]),
                         {"balance", "amount", "recipient", "details", "narration", "error"})

    def test_the_signup_pin_has_neither_but_still_supplies_both(self):
        """It shares the published screen and describes no payment at all."""
        screen = flows._pin_screen("Create a 6-digit PIN")
        self.assertEqual(screen["data"]["balance"], "")
        self.assertEqual(screen["data"]["narration"], "")
        self.assertEqual(screen["data"]["amount"], "Create a 6-digit PIN")


class NarrationOnTheReceiptTests(TestCase):

    def setUp(self):
        self.user = _make_user()

    def test_it_sits_with_the_amount_not_after_the_reference(self):
        """Placed where it reads with the payment — the reference and the date
        are for us, not for the person being shown the receipt."""
        rows = router._with_narration(_transfer(self.user, narration="Rent"), [
            ("To", "JOHN DOE"), ("Amount", "₦1,000.00"), ("Reference", "r"), ("Date", "d")])
        self.assertEqual([k for k, _ in rows],
                         ["To", "Amount", "Narration", "Reference", "Date"])

    def test_no_note_leaves_the_receipt_exactly_as_it_was(self):
        """An empty "Narration —" row looks like something failed to render."""
        original = [("To", "JOHN DOE"), ("Amount", "₦1,000.00")]
        self.assertEqual(router._with_narration(_transfer(self.user), original), original)


class NarrationOnTheAlertTests(TestCase):
    """Both money paths already carried a note under different names, long
    before there was a field to fill either."""

    def setUp(self):
        self.user = _make_user()

    def _txn(self, meta):
        from wallet.models import Transaction

        return Transaction.objects.create(
            user=self.user, amount=Decimal("1000"), direction=Transaction.OUT,
            service="transfer", reference="ref-1", meta=meta,
            transaction_status=Transaction.SUCCESS)

    def test_a_transfer_note_is_quoted(self):
        from wallet.alerts import _describe

        _subject, body = _describe(self._txn({"note": "Rent"}))
        self.assertIn("For: Rent", body)

    def test_a_bill_narration_is_quoted(self):
        from wallet.alerts import _describe

        _subject, body = _describe(self._txn({"narration": "August light"}))
        self.assertIn("For: August light", body)

    def test_no_note_closes_the_alert_up_around_it(self):
        from wallet.alerts import _describe

        _subject, body = _describe(self._txn({}))
        self.assertNotIn("For:", body)

    def test_a_reversal_does_not_repeat_what_the_customer_meant_to_buy(self):
        """A reversal is about money coming back. Their own words belong in a
        sentence about a payment that happened."""
        from wallet.alerts import _describe

        _subject, body = _describe(self._txn({"note": "Rent"}), reversal=True)
        self.assertNotIn("For: Rent", body)


class NarrationFromTheAITests(TestCase):

    def setUp(self):
        self.user = _make_user()

    def test_it_reaches_the_action_the_intent_creates(self):
        with patch("whatsapp.router._begin_bank_transfer", return_value=False), \
             patch("whatsapp.router.reply"):
            router.dispatch_intent(self.user, MSISDN, {
                "name": "transfer",
                "input": {"amount": 5000, "narration": "for rent"}})
        pa = PendingAction.objects.filter(msisdn=MSISDN).first()
        self.assertIsNotNone(pa)
        self.assertEqual(pa.payload.get("narration"), "for rent")

    def test_it_does_not_outlive_the_message_that_carried_it(self):
        """One customer's words must never attach to the next customer's
        payment."""
        with patch("whatsapp.router._begin_bank_transfer", return_value=False), \
             patch("whatsapp.router.reply"):
            router.dispatch_intent(self.user, MSISDN, {
                "name": "transfer", "input": {"amount": 1, "narration": "first"}})
        self.assertEqual(router._ai_narration.get(""), "")

    def test_a_non_money_flow_is_never_narrated(self):
        pa = router._new_flow(self.user, MSISDN, "unlock", "pin", {"pin_attempts": 0})
        self.assertNotIn("narration", pa.payload)
