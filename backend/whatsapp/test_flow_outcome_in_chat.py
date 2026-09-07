"""Every Flow ending says what happened somewhere the customer can still read.

The terminal screen is not a durable place to have told someone something. It
lives inside Meta's panel, it is gone the moment they tap Done or swipe the sheet
away, and when the data-exchange answer never reaches the device WhatsApp shows
"Couldn't load content. Try again later." instead — which names no outcome at
all.

Endings that moved money always wrote to the chat, because their executors send
the receipt or the failure line. The endings that did NOT were the ones most
easily misread as "nothing happened": a wrong PIN that cancelled the payment, a
lockout, a PIN that was never set. Those closed the panel and left the thread
ending on the confirm card — still there, still looking live.
"""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from whatsapp import flows, router
from whatsapp.models import PendingAction
from whatsapp.test_flows import MSISDN, _make_user


def _armed(user, **payload):
    return PendingAction.objects.create(
        user=user, msisdn=MSISDN, action_type="transfer", state=flows.FLOW_PIN_STATE,
        payload={"amount": "1000", "pin_attempts": 0, **payload},
        expires_at=timezone.now() + timedelta(minutes=5))


@override_settings(WHATSAPP_FLOW={"RESULT_SCREEN": True})
class FlowOutcomeReachesTheChatTests(TestCase):

    def setUp(self):
        self.user = _make_user()

    def _submit(self, pa, pin):
        with patch("whatsapp.router.reply") as reply:
            screen = flows._submit_pin(flows.sign_flow_token(pa), {"pin": pin})
        return screen, [c.args[1] for c in reply.call_args_list]

    def test_the_last_wrong_pin_says_so_in_the_chat_not_only_on_the_panel(self):
        """The attempt that CANCELS the payment is the one that has to survive
        the panel closing."""
        pa = _armed(self.user, flow_pin_tries=flows_last_try())
        screen, said = self._submit(pa, "000000")
        self.assertEqual(screen["screen"], flows.RESULT_SCREEN)
        self.assertEqual(screen["data"]["status"], "❌ Not completed")
        self.assertTrue(said, "the cancelling attempt left the thread silent")
        self.assertIn("cancelled", " ".join(said).lower())

    def test_an_earlier_wrong_pin_stays_on_the_pad_and_says_nothing_in_chat(self):
        """The retry render is the customer looking straight at the error. A
        message per attempt would bury the thread in copies of what is on screen,
        and this branch is not an ending — the payment is still live."""
        pa = _armed(self.user)
        screen, said = self._submit(pa, "000000")
        self.assertEqual(screen["screen"], flows.PIN_RETRY)
        self.assertEqual(said, [])

    def test_a_lockout_says_so_in_the_chat(self):
        pa = _armed(self.user)
        with patch("common.http.evaluate_transaction_pin",
                   return_value=(False, "pin_locked", "Too many wrong PINs.")):
            screen, said = self._submit(pa, "000000")
        self.assertEqual(screen["data"]["status"], "❌ Not completed")
        self.assertIn("Too many wrong PINs.", " ".join(said))

    def test_no_pin_set_says_so_in_the_chat(self):
        pa = _armed(self.user)
        with patch("common.http.evaluate_transaction_pin",
                   return_value=(False, "no_pin", "No PIN.")):
            screen, said = self._submit(pa, "000000")
        self.assertEqual(screen["data"]["status"], "❌ Not completed")
        self.assertIn("set pin", " ".join(said).lower())

    def test_an_execution_crash_says_so_in_the_chat(self):
        """The panel gets a sentence either way; without this the thread got
        nothing for a payment whose state the customer cannot see.

        The panel now HOLDS OPEN on a crash rather than closing — only a settled
        success may close itself — so the sentence lands in the payment card's
        error line instead of on a terminal heading. What this test is actually
        about, that the CHAT is told, is unchanged.
        """
        pa = _armed(self.user)
        with patch("common.http.evaluate_transaction_pin", return_value=(True, "", "")), \
             patch("whatsapp.router.authorise_flow_execution", side_effect=RuntimeError("boom")):
            screen, said = self._submit(pa, "123456")
        self.assertEqual(screen["screen"], flows.RESULT_SCREEN)
        self.assertIn("auto-reverse", screen["data"]["message"])
        self.assertIn("auto-reverse", " ".join(said))

    def test_a_send_failure_never_costs_the_customer_their_screen(self):
        """Mirroring is best-effort. A Graph outage must not turn a settled
        outcome into the error screen the mirror exists to make redundant."""
        pa = _armed(self.user)
        with patch("common.http.evaluate_transaction_pin",
                   return_value=(False, "pin_locked", "Locked.")), \
             patch("whatsapp.router.reply", side_effect=RuntimeError("graph down")):
            screen = flows._submit_pin(flows.sign_flow_token(pa), {"pin": "000000"})
        self.assertEqual(screen["screen"], flows.RESULT_SCREEN)
        self.assertEqual(screen["data"]["status"], "❌ Not completed")


def flows_last_try() -> int:
    """One below the cap, so the next wrong PIN is the one that cancels."""
    from whatsapp.router import PIN_FLOW_ATTEMPTS

    return PIN_FLOW_ATTEMPTS - 1


class ARetappedCardDoesNotReopenThePinPadTests(TestCase):
    """A Flow card cannot be recalled, expired or disabled by the business that
    sent it, so the confirm card stays in the thread and stays tappable forever.

    The money was never at risk: the flow token stops resolving the moment the
    action leaves the PIN state, so a second submit checks no PIN and moves
    nothing. What was wrong is what the customer SAW — a live-looking PIN pad
    showing a payment they had already made, which they could type their PIN into
    and only be told afterwards. Opening now asks the endpoint first.
    """

    def setUp(self):
        self.user = _make_user()

    def _open(self, pa):
        return flows.handle_flow_request(
            {"action": "INIT", "flow_token": flows.sign_flow_token(pa)})

    def test_a_live_payment_still_opens_its_pin_pad(self):
        pa = _armed(self.user)
        screen = self._open(pa)
        self.assertEqual(screen["screen"], flows.PIN_SCREEN)
        self.assertNotEqual(screen["screen"], flows.RESULT_SCREEN)

    def test_a_completed_payment_answers_its_outcome_instead(self):
        """The reported bug. The executor clears the action on success, so by the
        time the card is tapped again there is nothing to confirm."""
        pa = _armed(self.user)
        token = flows.sign_flow_token(pa)
        pa.delete()                                  # what _clear_actions does on success
        screen = flows.handle_flow_request({"action": "INIT", "flow_token": token})
        self.assertEqual(screen["screen"], flows.RESULT_SCREEN)
        self.assertEqual(screen["data"]["status"], "❌ Not completed")
        self.assertIn("already done", screen["data"]["message"])

    def test_a_payment_mid_execution_does_not_reopen_either(self):
        """Authorised and queued is past the point of confirming. Reopening the
        pad there invites a second PIN for a payment already on its way."""
        pa = _armed(self.user)
        pa.state = router.EXECUTING_STATE
        pa.save(update_fields=["state"])
        self.assertEqual(self._open(pa)["screen"], flows.RESULT_SCREEN)

    def test_the_money_confirm_asks_the_server_which_screen_to_open(self):
        """The mechanism behind all of the above: `navigate` bakes the pad into
        the message and never consults us, so it could not have been fixed
        server-side at all."""
        pa = _armed(self.user)
        sent = {}
        with patch.object(router, "flows_live", return_value=True), \
             patch.object(router, "send_flow",
                          side_effect=lambda m, t, **kw: sent.update(kw) or {"success": True}), \
             patch.object(router, "reply"):
            router._send_pin_flow(pa, self.user)
        self.assertEqual(sent["on_open"], "data_exchange")
