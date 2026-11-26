"""An answer naming a screen the page cannot route to is logged (26 Nov 2026).

Meta refuses it and the customer sees "Couldn't load content" while our access
log shows a 200, so `wa_flow_off_route` is the only trace."""
import json
import os
from unittest.mock import patch

from django.test import SimpleTestCase

from . import flows

with open(os.path.join(os.path.dirname(__file__), "flow_assets", "pin_flow.json"), encoding="utf-8") as fh:
    ROUTES = json.load(fh)["routing_model"]


class OffRouteTests(SimpleTestCase):
    def answer(self, payload, response):
        with patch.object(flows, "_handle_flow_request", return_value=response), \
             patch.object(flows, "_check_contract", side_effect=lambda r: r):
            return flows.handle_flow_request(payload)

    def exchange(self, source, target, data=None):
        return self.answer({"action": "data_exchange", "screen": source, "flow_token": "t"},
                           {"screen": target, "data": data or {}})

    def test_an_answer_the_page_cannot_route_to_is_logged(self):
        self.assertNotIn("SIGNUP_BVN", ROUTES["SIGNUP_BVN_RETRY"])
        with self.assertLogs("whatsapp", level="ERROR") as logs:
            response = self.exchange("SIGNUP_BVN_RETRY", "SIGNUP_BVN")
        self.assertEqual(response["screen"], "SIGNUP_BVN")      # logged, never altered
        self.assertIn("wa_flow_off_route from=SIGNUP_BVN_RETRY to=SIGNUP_BVN", logs.output[0])

    def test_allowed_routes_and_staying_on_the_page_are_silent(self):
        for source, target in (("SIGNUP_BVN_RETRY", "SIGNUP_ADDRESS"),
                               ("SIGNUP_BVN_RETRY", "SIGNUP_BVN_RETRY"),
                               ("PIN_SCREEN", "RESULT")):
            with self.subTest(source=source, target=target), self.assertNoLogs("whatsapp", level="ERROR"):
                self.exchange(source, target)

    def test_only_a_data_exchange_between_known_screens_is_checked(self):
        with self.assertNoLogs("whatsapp", level="ERROR"):
            self.answer({"action": "INIT", "flow_token": "t"}, {"screen": "SIGNUP_BVN", "data": {}})
            self.answer({"action": "BACK", "screen": "SIGNUP_BVN_RETRY"}, {"screen": "SIGNUP_BVN", "data": {}})
            self.exchange("NOT_A_SCREEN", "SIGNUP_BVN")
            self.exchange("SIGNUP_BVN_RETRY", "SUCCESS", {flows._TERMINATION_KEY: {}})
            self.answer(None, {"screen": "SIGNUP_BVN", "data": {}})

    def test_every_route_in_the_published_flow_is_silent(self):
        with self.assertNoLogs("whatsapp", level="ERROR"):
            for source, targets in ROUTES.items():
                for target in targets:
                    if target != "SUCCESS":
                        self.exchange(source, target)
