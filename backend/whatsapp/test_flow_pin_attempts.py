"""The masked PIN box may never be answered with the screen id it is already on.

WhatsApp keeps a form's client-side value whenever the endpoint answers with the
SAME screen id. The PIN field is masked and declared min-chars/max-chars 6/6, so
a retained six-character value is both invisible AND un-typeable: the customer
cannot read what is in the box and cannot add a digit until they have deleted six
characters they cannot see. The box reads as broken, and every Confirm tap spends
another attempt from a cross-channel budget that ends in a 15-minute lockout.

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

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from transfers.models import Bank
from wallet.services import credit, get_or_create_wallet

from .flows import (FLOW_PIN_STATE, PIN_CHAIN, PIN_CONFIRM, PIN_CONFIRM_RETRY,
                    PIN_RETRY, PIN_SCREEN, SUCCESS_SCREEN, _PIN_CONFIRM_ATTEMPTS,
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


def _no_id_twice_in_a_row(screens):
    """Consecutive repeats of an INPUT-bearing screen id — the defect. SUCCESS is
    exempt: it is terminal and carries no field, so a resolved-away token
    answering it repeatedly retains nothing and strands no one."""
    return [(a, b) for a, b in zip(screens, screens[1:])
            if a == b and a != SUCCESS_SCREEN]


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

        self.assertEqual(screens, [PIN_RETRY, SUCCESS_SCREEN])
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
        self.assertEqual(resp["screen"], SUCCESS_SCREEN)
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
