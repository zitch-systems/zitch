"""The masked PIN box may never be answered with the screen id it is already on.

WhatsApp keeps a form's client-side value whenever the endpoint answers with the
SAME screen id. The PIN field is masked and declared min-chars/max-chars 6/6, so
a retained six-character value is both invisible AND un-typeable: the customer
cannot read what is in the box and cannot add a digit until they have deleted six
characters they cannot see. The box reads as broken, and every Confirm tap spends
another attempt from a cross-channel budget that ends in an hour-long lockout.

The module already routes the FIRST error of each masked step to a fresh twin
(PIN_RETRY, PIN_CONFIRM_RETRY) so it arrives empty. There is no second twin — and
routing is forward-only, so there cannot be a cheap one. These tests pin the other
half of the answer: the attempt budget is capped at the number of distinct screen
ids the step actually has, so a repeat render is unreachable rather than merely
uncomfortable.

Driven end-to-end through handle_flow_request rather than by seeding counters: a
seeded-counter test passes happily against a counter production never writes.
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from transfers.models import Bank
from wallet.services import credit, get_or_create_wallet

from .flows import (FLOW_PIN_STATE, PIN_CHAIN, PIN_CONFIRM, PIN_CONFIRM_RETRY,
                    PIN_RETRY, PIN_SCREEN, RESULT_SCREEN, SUCCESS_SCREEN, _PIN_CONFIRM_ATTEMPTS,
                    _PIN_CREATE_ATTEMPTS, handle_flow_request, sign_flow_token,
                    sign_onboarding_token)
from .models import PendingAction, WaOnboarding, WhatsAppLink
from .router import PIN_FLOW_ATTEMPTS

User = get_user_model()
MSISDN = "2348011112222"


def _user(pin="1234", balance="50000"):
    u = User.objects.create(username="08010000001", phone="08010000001",
                            email="ada@zitch.test", first_name="Ada", last_name="Eze",
                            tier=1, bvn_verified=True, nin_verified=True,
                            email_verified=True, phone_verified=True)
    if pin:
        u.set_transaction_pin(pin)
    u.save()
    get_or_create_wallet(u)
    credit(u, Decimal(balance), "Seed")
    WhatsAppLink.objects.create(user=u, wa_msisdn=MSISDN, status=WhatsAppLink.ACTIVE)
    return u


#: Screens that END a session and carry no input field. Repeats of these are
#: harmless — a resolved-away token answering one twice retains nothing and
#: strands no one — so they are exempt from the no-repeat rule below.
_ENDINGS = (SUCCESS_SCREEN, RESULT_SCREEN)


def _no_id_twice_in_a_row(screens):
    """Consecutive repeats of an INPUT-bearing screen id — the defect."""
    return [(a, b) for a, b in zip(screens, screens[1:])
            if a == b and a not in _ENDINGS]


class MoneyPathPinAttemptsTests(TestCase):

    def setUp(self):
        Bank.objects.create(code="gtb", name="GTBank", bank_code="058",
                            color="#e30613", active=True)
        self.user = _user()

    def _action(self):
        return PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="transfer", state=FLOW_PIN_STATE,
            payload={"amount": "5000", "account": "0123456789", "bank_code": "058",
                     "bank_name": "GTBank", "name": "JOHN DOE",
                     "flow_screen": PIN_SCREEN,
                     "flow_fields": {"amount": "₦5,000.00", "recipient": "To JOHN DOE",
                                     "details": "GTBank · 0123456789"}},
            expires_at=timezone.now() + timedelta(minutes=5),
        )

    def _submit(self, token, pin):
        return handle_flow_request({"action": "data_exchange", "flow_token": token,
                                    "data": {"pin": pin}})["screen"]

    def test_a_second_wrong_pin_cancels_instead_of_re_rendering_the_twin(self):
        pa = self._action()
        token = sign_flow_token(pa)
        screens = [self._submit(token, "9999"), self._submit(token, "8888")]

        # RESULT, not SUCCESS: a cancelled payment has something the customer needs
        # to read, so the panel holds open with a Done button rather than closing.
        self.assertEqual(screens, [PIN_RETRY, RESULT_SCREEN])
        self.assertEqual(_no_id_twice_in_a_row(screens), [])
        self.assertFalse(PendingAction.objects.filter(pk=pa.pk).exists())  # fails closed

        # And the customer is CANCELLED, not locked out — the cap spends session
        # attempts, never the shared budget that gates the app and the chat too.
        self.user.refresh_from_db()
        self.assertEqual(self.user.pin_failed_attempts, 2)
        self.assertIsNone(self.user.pin_locked_until)

    def test_no_screen_id_is_ever_answered_twice_in_a_row(self):
        """The general invariant, driven past the cap: whatever the handler does
        it must never answer the id the device is already standing on."""
        pa = self._action()
        token = sign_flow_token(pa)
        screens = []
        for _ in range(6):
            resp = handle_flow_request({"action": "data_exchange", "flow_token": token,
                                        "data": {"pin": "9999"}})
            screens.append(resp["screen"])
        self.assertEqual(_no_id_twice_in_a_row(screens), [], screens)
        self.assertEqual(screens[-1], SUCCESS_SCREEN)

    def test_an_account_with_no_pin_terminates_instead_of_looping(self):
        """`no_pin` returns before evaluate_transaction_pin's atomic block, so it
        is never counted and can never reach the lockout that ends every other
        failing path. Re-rendering the pad looped one screen id until the token
        expired — for a PIN that does not exist and cannot be produced."""
        self.user.transaction_pin = ""
        self.user.save(update_fields=["transaction_pin"])
        pa = self._action()
        token = sign_flow_token(pa)

        resp = handle_flow_request({"action": "data_exchange", "flow_token": token,
                                    "data": {"pin": "9999"}})
        # The no-PIN ending is a FAILURE, so it holds the panel open on RESULT.
        self.assertEqual(resp["screen"], RESULT_SCREEN)
        self.assertIn("set pin", resp["data"]["message"])
        self.assertFalse(PendingAction.objects.filter(pk=pa.pk).exists())
        # The token no longer resolves, so there is nothing left to loop on.
        self.assertEqual(self._submit(token, "9999"), SUCCESS_SCREEN)

    def test_a_correct_pin_still_goes_straight_through(self):
        pa = self._action()
        self.assertEqual(self._submit(sign_flow_token(pa), "1234"), SUCCESS_SCREEN)

    def test_one_wrong_pin_then_the_right_one_still_pays(self):
        """The cap must not cost a customer their legitimate retry."""
        pa = self._action()
        token = sign_flow_token(pa)
        self.assertEqual(self._submit(token, "9999"), PIN_RETRY)
        self.assertEqual(self._submit(token, "1234"), SUCCESS_SCREEN)
        self.user.refresh_from_db()
        self.assertEqual(self.user.pin_failed_attempts, 0)   # cleared by the good PIN


class ReArmResetsTheScreenAndTheBudgetTests(TestCase):
    """_send_pin_flow was the one sender that never reset flow_screen, while every
    sibling does ("opens on the root, not the twin"). A re-armed action carrying
    flow_screen=PIN_CHAIN from the transfer form would have INIT/BACK answer
    PIN_CHAIN against a message opened on PIN_SCREEN — and PIN_SCREEN -> PIN_CHAIN
    is not a declared route, so Meta refuses it on the device, mid-payment."""

    def setUp(self):
        Bank.objects.create(code="gtb", name="GTBank", bank_code="058",
                            color="#e30613", active=True)
        self.user = _user()

    def _stale_action(self, screen):
        return PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="transfer", state=FLOW_PIN_STATE,
            payload={"amount": "5000", "account": "0123456789", "bank_code": "058",
                     "bank_name": "GTBank", "name": "JOHN DOE",
                     "flow_screen": screen, "flow_pin_tries": 1},
            expires_at=timezone.now() + timedelta(minutes=5),
        )

    def test_re_arming_returns_to_the_root_with_a_fresh_budget(self):
        from .router import _send_pin_flow

        pa = self._stale_action(PIN_CHAIN)
        _send_pin_flow(pa, self.user)
        pa.refresh_from_db()
        self.assertEqual(pa.payload["flow_screen"], PIN_SCREEN)
        self.assertEqual(pa.payload["flow_pin_tries"], 0)

    def test_a_stale_retry_twin_never_survives_a_re_arm(self):
        from .router import _send_pin_flow

        pa = self._stale_action(PIN_RETRY)
        _send_pin_flow(pa, self.user)
        pa.refresh_from_db()
        self.assertEqual(pa.payload["flow_screen"], PIN_SCREEN)


class CreatePinAttemptsTests(TestCase):
    """transaction_pin_rejection is a POLICY check with no counter of its own, and
    the onboarding container lives for ONBOARD_TTL (15 minutes) — so before the
    cap this was the most reachable instance of the retained-box trap, not the
    least."""

    def _onboarding(self):
        return WaOnboarding.objects.create(
            msisdn=MSISDN, step=FLOW_PIN_STATE,
            payload={"first_name": "Ngozi", "last_name": "Ade",
                     "email": "ngozi@example.com", "phone": "08099990001",
                     "flow_screen": PIN_SCREEN},
            expires_at=timezone.now() + timedelta(minutes=15),
        )

    def _submit(self, ob, pin):
        return handle_flow_request({"action": "data_exchange",
                                    "flow_token": sign_onboarding_token(ob),
                                    "data": {"pin": pin}})["screen"]

    def test_a_second_weak_pin_terminates_instead_of_re_rendering(self):
        ob = self._onboarding()
        screens = [self._submit(ob, "123456"), self._submit(ob, "111111")]
        self.assertEqual(screens, [PIN_RETRY, SUCCESS_SCREEN])
        self.assertEqual(_no_id_twice_in_a_row(screens), [])
        # The signup row SURVIVES — it is not a PendingAction and _clear_actions
        # is not its teardown; it resumes from the card or expires on its TTL.
        self.assertTrue(WaOnboarding.objects.filter(pk=ob.pk).exists())

    def test_re_opening_the_card_gets_a_fresh_budget_not_a_dead_end(self):
        """The cap is a per-SESSION render budget: client-side field state dies
        with the session, so a re-tap is a genuinely empty start. Persisting the
        exhaustion would leave the surviving signup row pointing at a card that
        terminated on the customer's very next keystroke, forever."""
        ob = self._onboarding()
        self._submit(ob, "123456")
        self._submit(ob, "111111")                       # budget spent, terminal
        ob.refresh_from_db()
        self.assertEqual(ob.payload.get("pin_policy_tries"), 0)
        # A second session gets its retry back, and a good PIN still completes.
        self.assertEqual(self._submit(ob, "123456"), PIN_RETRY)
        self.assertEqual(self._submit(ob, "246810"), PIN_CONFIRM)

    def test_an_unreproducible_first_entry_is_dropped_on_terminate(self):
        """The customer could not retype it, so keeping it authoritative would
        make the next session unwinnable."""
        ob = self._onboarding()
        self._submit(ob, "246810")
        self._submit(ob, "111112")
        self._submit(ob, "111113")                       # confirm budget spent
        ob.refresh_from_db()
        self.assertNotIn("flow_pin_hash", ob.payload)
        # Re-tapping starts the create-then-confirm pair over.
        self.assertEqual(self._submit(ob, "135790"), PIN_CONFIRM)
        self.assertEqual(self._submit(ob, "135790"), SUCCESS_SCREEN)

    def test_a_weak_pin_then_a_good_one_still_reaches_the_confirm_step(self):
        ob = self._onboarding()
        self.assertEqual(self._submit(ob, "123456"), PIN_RETRY)
        self.assertEqual(self._submit(ob, "246810"), PIN_CONFIRM)

    def test_a_second_confirm_mismatch_terminates(self):
        ob = self._onboarding()
        screens = [self._submit(ob, "246810"),      # held
                   self._submit(ob, "111112"),      # mismatch 1
                   self._submit(ob, "111113")]      # mismatch 2
        self.assertEqual(screens, [PIN_CONFIRM, PIN_CONFIRM_RETRY, SUCCESS_SCREEN])
        self.assertEqual(_no_id_twice_in_a_row(screens), [])


class SetPinAttemptsTests(TestCase):

    def setUp(self):
        self.user = _user()

    def _armed(self):
        from .router import _new_flow

        return _new_flow(self.user, MSISDN, "setpin", FLOW_PIN_STATE, {})

    def _submit(self, pa, pin):
        return handle_flow_request({"action": "data_exchange",
                                    "flow_token": sign_flow_token(pa),
                                    "data": {"pin": pin}})["screen"]

    def test_a_second_weak_pin_terminates_instead_of_re_rendering(self):
        pa = self._armed()
        screens = [self._submit(pa, "123456"), self._submit(pa, "111111")]
        self.assertEqual(screens, [PIN_RETRY, SUCCESS_SCREEN])
        self.assertEqual(_no_id_twice_in_a_row(screens), [])
        self.assertFalse(PendingAction.objects.filter(pk=pa.pk).exists())
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_transaction_pin("1234"))   # unchanged

    def test_a_second_confirm_mismatch_terminates(self):
        pa = self._armed()
        screens = [self._submit(pa, "246810"),
                   self._submit(pa, "111112"),
                   self._submit(pa, "111113")]
        self.assertEqual(screens, [PIN_CONFIRM, PIN_CONFIRM_RETRY, SUCCESS_SCREEN])
        self.assertEqual(_no_id_twice_in_a_row(screens), [])
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_transaction_pin("1234"))   # never set


class EscalatedLockOffersTheResetTests(TestCase):
    """Five wrong PINs lock the account for an hour; five more without a correct
    PIN in between lock it for a day. A day is not something you tell a customer
    to wait out, so on that tier every surface names the way out — and the way
    out has to be reachable WHILE locked, or naming it is a cruelty.

    The counting happens in evaluate_transaction_pin (shared with the app), so
    these tests drive the WhatsApp wrapping around it, not the ladder itself.
    """

    def setUp(self):
        Bank.objects.create(code="gtb", name="GTBank", bank_code="058",
                            color="#e30613", active=True)
        self.user = _user()

    def _lock(self, rounds=1):
        """Burn `rounds` full sets of wrong PINs, ageing each lock out in between
        so the next set can actually run."""
        from common.http import evaluate_transaction_pin

        from accounts.models import User as UserModel

        for i in range(rounds):
            for _ in range(UserModel.PIN_MAX_ATTEMPTS):
                evaluate_transaction_pin(self.user, "0000")
            if i < rounds - 1:
                UserModel.objects.filter(pk=self.user.pk).update(
                    pin_locked_until=timezone.now() - timedelta(seconds=1))
        self.user.refresh_from_db()

    def _action(self):
        return PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="transfer", state=FLOW_PIN_STATE,
            payload={"amount": "5000", "account": "0123456789", "bank_code": "058",
                     "bank_name": "GTBank", "name": "JOHN DOE", "flow_screen": PIN_SCREEN},
            expires_at=timezone.now() + timedelta(minutes=5),
        )

    def _open_reset(self):
        """Run `reset pin` with the Flow rail up. A PIN is never collected in the
        thread, so with no rail the reset legitimately declines — which is a
        different behaviour from the one under test here."""
        from .router import _start_pin_reset

        with patch("whatsapp.router.flows_live", return_value=True), \
             patch("whatsapp.router.send_flow", return_value={"success": True}):
            _start_pin_reset(self.user, MSISDN)

    def _flow_message(self):
        resp = handle_flow_request({"action": "data_exchange",
                                    "flow_token": sign_flow_token(self._action()),
                                    "data": {"pin": "1234"}})
        self.assertEqual(resp["screen"], RESULT_SCREEN)
        return resp["data"]["message"]

    def test_the_flow_names_the_reset_only_once_the_lock_is_a_day(self):
        self._lock(1)
        first = self._flow_message()
        self.assertIn("about an hour", first)
        self.assertNotIn("reset", first.lower())

        self._lock(2)
        second = self._flow_message()
        self.assertIn("about 24 hours", second)
        self.assertIn("reset pin", second)
        # The Flow is closing, so the instruction points at the chat that
        # outlives it — and asterisks are chat markdown, not Flow markup.
        self.assertIn("in the chat", second)
        self.assertNotIn("*", second)

    def test_the_hint_is_there_on_the_render_that_creates_the_escalated_lock(self):
        """The lock is created INSIDE evaluate_transaction_pin, which works on
        its own row-locked copy of the user. Reading the escalation off the
        caller's instance without syncing it back drops the reset offer from the
        very message announcing the 24-hour lock — the one that most needs it,
        and the one a hand-seeded test would never reach.
        """
        from common.http import evaluate_transaction_pin

        from accounts.models import User as UserModel

        self._lock(1)
        UserModel.objects.filter(pk=self.user.pk).update(
            pin_locked_until=timezone.now() - timedelta(seconds=1))
        self.user.refresh_from_db()
        for _ in range(UserModel.PIN_MAX_ATTEMPTS - 1):        # four of the five
            evaluate_transaction_pin(self.user, "0000")

        pa = self._action()                                    # loads user pre-lock
        resp = handle_flow_request({"action": "data_exchange",
                                    "flow_token": sign_flow_token(pa),
                                    "data": {"pin": "0000"}})  # the fifth lands here
        self.assertEqual(resp["screen"], RESULT_SCREEN)
        self.assertIn("about 24 hours", resp["data"]["message"])
        self.assertIn("reset pin", resp["data"]["message"])

    def test_the_chat_names_the_reset_command_on_the_escalated_lock(self):
        from .router import _flow_pin_ok

        self._lock(2)
        pa = self._action()
        with patch("whatsapp.router.reply") as sent:
            self.assertFalse(_flow_pin_ok(pa, self.user, MSISDN, "1234"))
        body = sent.call_args[0][1]
        self.assertIn("about 24 hours", body)
        self.assertIn("*reset pin*", body)
        # And the payment is torn down rather than left armed against a lock.
        self.assertFalse(PendingAction.objects.filter(pk=pa.pk).exists())

    def test_the_reset_the_message_advertises_is_reachable_while_locked(self):
        """The lock gates spending, not recovery. If `reset pin` were refused
        while locked, the escalated message would be sending a customer at a
        door we hold shut for a day."""
        self._lock(2)
        with patch("whatsapp.router.reply") as sent:
            self._open_reset()
        body = sent.call_args[0][1]
        self.assertNotIn("locked", body.lower())
        self.assertTrue(PendingAction.objects.filter(
            msisdn=MSISDN, action_type="setpin").exists())

    def test_the_new_pin_lifts_the_lock_and_pays_immediately(self):
        """End to end, the promise the message makes: choose a new PIN on the
        secure screen and payments work again — not in 24 hours."""
        self._lock(2)
        with patch("whatsapp.router.reply"):
            self._open_reset()
        pa = PendingAction.objects.get(msisdn=MSISDN, action_type="setpin")

        token = sign_flow_token(pa)
        self.assertEqual(handle_flow_request({"action": "data_exchange", "flow_token": token,
                                              "data": {"pin": "975310"}})["screen"], PIN_CONFIRM)
        self.assertEqual(handle_flow_request({"action": "data_exchange", "flow_token": token,
                                              "data": {"pin": "975310"}})["screen"],
                         SUCCESS_SCREEN)

        self.user.refresh_from_db()
        self.assertIsNone(self.user.pin_locked_until)
        self.assertEqual(self.user.pin_lockout_strikes, 0)
        self.assertFalse(self.user.pin_reset_required)
        # The real proof: the shared gate accepts the new PIN right now.
        from common.http import evaluate_transaction_pin
        self.assertTrue(evaluate_transaction_pin(self.user, "975310")[0])


class TheCapIsCoupledToTheScreensThatExistTests(TestCase):
    """The durable guard. Each cap is a SCREEN budget: it may never exceed the
    number of distinct ids that step can answer with, because the render after
    the last one has to repeat an id. Raising a cap without publishing another
    screen fails here instead of quietly restoring the bug."""

    def test_the_money_path_budget_matches_its_screen_count(self):
        # The root the Flow opened on (PIN_SCREEN or PIN_CHAIN), then PIN_RETRY.
        self.assertLessEqual(PIN_FLOW_ATTEMPTS, 2)

    def test_the_create_and_confirm_budgets_match_their_screen_counts(self):
        # Create: the root, then PIN_RETRY. Confirm: PIN_CONFIRM, then the twin.
        self.assertLessEqual(_PIN_CREATE_ATTEMPTS, 2)
        self.assertLessEqual(_PIN_CONFIRM_ATTEMPTS, 2)

    def test_the_published_flow_has_no_further_pin_twin_to_spend(self):
        """If someone adds PIN_RETRY_2 and republishes, this is the reminder that
        the caps above may now legitimately rise."""
        import json
        from pathlib import Path

        import whatsapp

        flow = json.loads((Path(whatsapp.__file__).parent / "flow_assets"
                           / "pin_flow.json").read_text())
        pin_twins = [s["id"] for s in flow["screens"]
                     if s["id"].startswith("PIN_RETRY")]
        self.assertEqual(pin_twins, [PIN_RETRY])
