"""The outcome is shown ONCE, and Done ends the Flow.

RESULT and SUCCESS declare the same two fields and render the same sentence, so
navigating from one to the other showed every ending twice: read the outcome, tap
Done, read the identical outcome, tap Done again.

RESULT cannot simply become terminal — a terminal screen returned from a
data_exchange closes the panel as it renders, which is the vanishing-outcome bug
RESULT exists to fix (see test_flow_e2e). So Done asks the SERVER to end the Flow
instead, and the server answers with Meta's completion envelope.
"""
import json
from pathlib import Path

from django.test import SimpleTestCase

import whatsapp
from whatsapp.flows import RESULT_SCREEN, SUCCESS_SCREEN, handle_flow_request

FLOW = json.loads((Path(whatsapp.__file__).parent / "flow_assets" / "pin_flow.json").read_text())
SCREENS = {s["id"]: s for s in FLOW["screens"]}
FOOTER = next(c for c in SCREENS[RESULT_SCREEN]["layout"]["children"] if c["type"] == "Footer")


class DoneEndsTheFlowTests(SimpleTestCase):
    def test_done_asks_the_server_rather_than_navigating(self):
        self.assertEqual(FOOTER["on-click-action"]["name"], "data_exchange")

    def test_done_carries_the_close_marker(self):
        self.assertIs(FOOTER["on-click-action"]["payload"]["close"], True)

    def test_the_outcome_is_never_forwarded_to_a_second_screen(self):
        # Repeating status/message into SUCCESS is exactly what made the second
        # page a copy of the first.
        payload = FOOTER["on-click-action"]["payload"]
        self.assertNotIn("status", payload)
        self.assertNotIn("message", payload)

    def test_result_stays_non_terminal(self):
        # Guards the fix this screen was built for: a terminal screen returned by
        # the server closes the panel instead of showing the outcome.
        self.assertIsNone(SCREENS[RESULT_SCREEN].get("terminal"))

    def test_result_may_still_answer_the_terminal(self):
        # The completion envelope names SUCCESS, so the route has to exist.
        self.assertIn(SUCCESS_SCREEN, FLOW["routing_model"][RESULT_SCREEN])


class TheCloseExchangeTests(SimpleTestCase):
    def test_close_ends_the_flow_with_the_completion_envelope(self):
        res = handle_flow_request({"action": "data_exchange", "flow_token": "TOK",
                                   "data": {"close": True}})
        self.assertEqual(res["screen"], SUCCESS_SCREEN)
        self.assertEqual(res["data"]["extension_message_response"]["params"]["flow_token"],
                         "TOK")

    def test_close_works_even_when_the_session_is_long_gone(self):
        # The payment is already finished by the time this page is on screen. A
        # customer tapping Done on a resolved payment must never meet an error,
        # whatever became of the pending action behind it.
        res = handle_flow_request({"action": "data_exchange", "flow_token": "999.expired",
                                   "data": {"close": True}})
        self.assertIn("extension_message_response", res["data"])

    def test_close_is_ignored_on_a_non_exchange_action(self):
        res = handle_flow_request({"action": "INIT", "flow_token": "TOK",
                                   "data": {"close": True}})
        self.assertNotIn("extension_message_response", res.get("data", {}))
