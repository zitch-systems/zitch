"""Every settled outcome gets its own page.

Success, pending and failure each land on RESULT with the heading that names what
happened. Closing the panel outright on success removed the duplicate page but also
removed the confirmation — the panel vanished the instant the money moved, and the
customer had to go and find the receipt in the chat to learn whether their payment
had worked.
"""
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from whatsapp import flows
from whatsapp.flows import RESULT_SCREEN, SUCCESS_SCREEN, handle_flow_request
from whatsapp.models import PendingAction
from whatsapp.tests import make_user

TERMINATION = "extension_message_response"


@override_settings(WHATSAPP_FLOW={"RESULT_SCREEN": True})
class OutcomeScreenTests(TestCase):
    def setUp(self):
        self.user, _ = make_user(phone="08010000031", email="out@zitch.test")
        self.pa = PendingAction.objects.create(
            user=self.user, msisdn="2348011115555", action_type="transfer",
            state=flows.FLOW_PIN_STATE,
            payload={"amount": "1000", "recipient": "ADA EZE", "bank": "Wema",
                     "account": "0123456789"},
            expires_at=timezone.now() + timezone.timedelta(minutes=10))
        self.token = flows.sign_flow_token(self.pa)

    def _exchange(self, outcome):
        with patch("whatsapp.router.authorise_flow_execution", return_value=outcome):
            return handle_flow_request({"action": "data_exchange",
                                        "flow_token": self.token,
                                        "data": {"pin": "1234"}})

    def _tagged(self, text, status):
        from whatsapp.router import Outcome
        return Outcome(text, status=status)

    def test_a_success_shows_a_success_page(self):
        res = self._exchange(self._tagged("₦1,000.00 sent to ADA EZE.", "success"))
        self.assertEqual(res["screen"], RESULT_SCREEN)
        self.assertEqual(res["data"]["status"], "✅ Successful")
        self.assertIn("ADA EZE", res["data"]["message"])
        self.assertNotIn(TERMINATION, res["data"])

    def test_a_pending_payment_says_pending(self):
        res = self._exchange(self._tagged("Still processing.", "pending"))
        self.assertEqual(res["screen"], RESULT_SCREEN)
        self.assertEqual(res["data"]["status"], "⏳ Pending")

    def test_a_failure_says_not_completed(self):
        res = self._exchange(self._tagged("That didn't go through.", "failed"))
        self.assertEqual(res["screen"], RESULT_SCREEN)
        self.assertEqual(res["data"]["status"], "❌ Not completed")

    def test_the_panel_never_closes_on_the_outcome_itself(self):
        # The close comes from the customer tapping Done, never from the server
        # ending the Flow underneath them at the moment money moves.
        for status in ("success", "pending", "failed"):
            res = self._exchange(self._tagged("x", status))
            self.assertNotIn(TERMINATION, res["data"], status)
            self.assertEqual(res["screen"], RESULT_SCREEN, status)

    def test_done_then_ends_it(self):
        self._exchange(self._tagged("₦1,000.00 sent.", "success"))
        closed = handle_flow_request({"action": "data_exchange",
                                      "flow_token": self.token,
                                      "data": {"close": True}})
        self.assertEqual(closed["screen"], SUCCESS_SCREEN)
        self.assertIn(TERMINATION, closed["data"])
