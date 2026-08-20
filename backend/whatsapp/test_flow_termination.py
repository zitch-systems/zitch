"""Ending a Flow is a completion ENVELOPE, not a terminal screen.

Meta reserves the response value "SUCCESS" to mean "this Flow is finished", and
expects the completion envelope beside it:

    {"screen": "SUCCESS",
     "data": {"extension_message_response": {"params": {"flow_token": ...}}}}

Answering {"screen": "SUCCESS", "data": {"status": ..., "message": ...}} collides
with that reserved name: WhatsApp reads it as a completion, finds a screen payload
where the envelope should be, and draws "Couldn't load content. Try again later."
That is the failure customers hit on every SUCCESSFUL payment for weeks — the
production trace in commit "Stop ending the Flow on the terminal screen" shows every
ordinary screen in the session rendering and only the ending failing.

These lock the envelope down, because nothing else can: the endpoint returns a dict
and never learns what the device did with it.
"""
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings

from whatsapp import flows
from whatsapp.flows import SUCCESS_SCREEN, _check_contract, _close_flow

TERMINATION = "extension_message_response"


class TerminationEnvelopeTests(TestCase):
    def test_the_envelope_matches_metas_documented_shape(self):
        resp = _close_flow("TOK123")
        self.assertEqual(resp["screen"], SUCCESS_SCREEN)
        self.assertEqual(resp["data"][TERMINATION]["params"]["flow_token"], "TOK123")

    def test_it_carries_no_screen_payload(self):
        # status/message here is exactly the malformed completion that failed.
        data = _close_flow("TOK123")["data"]
        self.assertNotIn("status", data)
        self.assertNotIn("message", data)

    def test_the_contract_conformer_leaves_it_alone(self):
        # SUCCESS declares `status` and `message`. Conforming a completion to that
        # would drop the envelope as "undeclared" and fill the two with "" —
        # silently rebuilding the broken response this fix removes.
        out = _check_contract(_close_flow("TOK123"))
        self.assertIn(TERMINATION, out["data"])
        self.assertEqual(out["data"][TERMINATION]["params"]["flow_token"], "TOK123")

    def test_an_ordinary_screen_is_still_conformed(self):
        # The pass-through must be scoped to completions, not a hole in the check.
        out = _check_contract({"screen": SUCCESS_SCREEN, "data": {"status": "x"}})
        self.assertEqual(set(out["data"]), {"status", "message"})


@override_settings(WHATSAPP_FLOW={"RESULT_SCREEN": True})
class SettledOutcomeTests(TestCase):
    """The end-to-end property: a successful payment ends the Flow."""

    def setUp(self):
        from whatsapp.tests import make_user
        from whatsapp.models import PendingAction
        from django.utils import timezone

        self.user, _ = make_user()
        self.pa = PendingAction.objects.create(
            user=self.user, msisdn="2348011112222", action_type="transfer",
            state=flows.FLOW_PIN_STATE,
            payload={"amount": "1000", "recipient": "ADA EZE", "bank": "Wema",
                     "account": "0123456789"},
            expires_at=timezone.now() + timezone.timedelta(minutes=10))
        self.token = flows.sign_flow_token(self.pa)

    def _exchange(self, outcome):
        with patch("whatsapp.router.authorise_flow_execution", return_value=outcome):
            return flows.handle_flow_request(
                {"action": "data_exchange", "flow_token": self.token, "data": {"pin": "1234"}})

    def test_a_settled_success_shows_its_outcome_page(self):
        # The success no longer ends the Flow by itself. Closing the panel the
        # instant the money moved removed the duplicate page and the confirmation
        # with it — the customer had to go and find the receipt to learn whether
        # their payment had worked. The Flow ends when they tap Done; see
        # test_done_ends_the_flow_from_the_outcome_page below.
        from whatsapp.router import Outcome

        resp = self._exchange(Outcome("₦1,000.00 sent to ADA EZE.", status="success"))
        self.assertEqual(resp["screen"], flows.RESULT_SCREEN)
        self.assertEqual(resp["data"]["status"], "✅ Successful")
        self.assertNotIn(TERMINATION, resp["data"])

    def test_done_ends_the_flow_from_the_outcome_page(self):
        resp = flows.handle_flow_request({"action": "data_exchange",
                                          "flow_token": self.token,
                                          "data": {"close": True}})
        self.assertEqual(resp["screen"], SUCCESS_SCREEN)
        self.assertEqual(resp["data"][TERMINATION]["params"]["flow_token"], self.token)

    def test_a_pending_outcome_still_holds_the_panel_open(self):
        from whatsapp.router import Outcome

        resp = self._exchange(Outcome("Still processing.", status="pending"))
        self.assertEqual(resp["screen"], flows.RESULT_SCREEN)
        self.assertNotIn(TERMINATION, resp["data"])
        self.assertEqual(resp["data"]["status"], "⏳ Pending")

    def test_a_failure_still_holds_the_panel_open(self):
        from whatsapp.router import Outcome

        resp = self._exchange(Outcome("That didn't go through.", status="failed"))
        self.assertEqual(resp["screen"], flows.RESULT_SCREEN)
        self.assertNotIn(TERMINATION, resp["data"])
