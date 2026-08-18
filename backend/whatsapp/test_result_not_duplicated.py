"""The outcome must be shown once.

RESULT and SUCCESS render the same two fields, and RESULT's Done button carried
`status` and `message` straight into SUCCESS — so a customer read the outcome,
tapped Done, and read the identical outcome again.

RESULT cannot simply become terminal: a terminal screen returned from a
data_exchange CLOSES the panel as it renders, which is the vanishing-outcome bug
RESULT exists to fix (see test_flow_e2e). So Done still navigates to the terminal;
it just stops repeating the verdict on the way.
"""
import json
from pathlib import Path

from django.test import SimpleTestCase

import whatsapp
from whatsapp.flows import RESULT_SCREEN

FLOW = json.loads((Path(whatsapp.__file__).parent / "flow_assets" / "pin_flow.json").read_text())
SCREENS = {s["id"]: s for s in FLOW["screens"]}
FOOTER = next(c for c in SCREENS[RESULT_SCREEN]["layout"]["children"] if c["type"] == "Footer")


class ResultNotDuplicatedTests(SimpleTestCase):
    def test_done_does_not_carry_the_outcome_into_the_next_screen(self):
        payload = FOOTER["on-click-action"]["payload"]
        for key in ("status", "message"):
            self.assertNotIn("${data.", payload[key],
                             f"RESULT still forwards its own {key} — the customer sees "
                             "the same verdict twice")

    def test_the_close_out_says_where_the_receipt_is(self):
        self.assertIn("receipt", FOOTER["on-click-action"]["payload"]["message"].lower())

    def test_result_stays_non_terminal(self):
        # Guards the fix this screen was built for: a terminal screen returned by
        # the server closes the panel instead of showing the outcome.
        self.assertIsNone(SCREENS[RESULT_SCREEN].get("terminal"))
        self.assertIn("SUCCESS", FLOW["routing_model"][RESULT_SCREEN])
