"""No endpoint answer may be unrenderable, whatever built it.

WhatsApp shows "Couldn't load content. Try again later." for ANY disagreement
between a response's `data` and the published screen's declaration — a missing
property, an extra one, or a value that is not the declared type. The customer
has already entered their PIN by then, so the sentence they read is about money
that has in fact moved, and the device says nothing about which key was wrong.

The check used to only log the mismatch, on the reasoning that filling in a
forgotten value hides the bug. These tests fix the opposite trade: papering over
a missing subtitle costs a blank line, and refusing to costs the customer the
outcome of their payment. The ERROR line is still emitted, so the bug stays as
findable as it was.
"""
from django.test import TestCase

from whatsapp import flows


class ConformResponseTests(TestCase):

    def _keys(self, screen):
        return set(flows._screen_contract()[screen])

    def test_a_missing_declared_property_is_filled_rather_than_shipped_absent(self):
        with self.assertLogs("whatsapp", level="ERROR") as logged:
            out = flows._check_contract({"screen": flows.SUCCESS_SCREEN,
                                         "data": {"message": "Sent"}})
        self.assertEqual(set(out["data"]), self._keys(flows.SUCCESS_SCREEN))
        self.assertEqual(out["data"]["status"], "")
        self.assertEqual(out["data"]["message"], "Sent")
        self.assertIn("wa_flow_contract_mismatch", "".join(logged.output))

    def test_an_undeclared_property_is_dropped_not_forwarded(self):
        """The extra key is the half people forget: it fails exactly as loudly as
        a missing one, and it is what a hand-built error screen produces."""
        with self.assertLogs("whatsapp", level="ERROR"):
            out = flows._check_contract({
                "screen": flows.SUCCESS_SCREEN,
                "data": {"status": "✅ Successful", "message": "Sent", "debug": "x"}})
        self.assertNotIn("debug", out["data"])
        self.assertEqual(set(out["data"]), self._keys(flows.SUCCESS_SCREEN))

    def test_a_matching_response_is_left_alone_and_logs_nothing(self):
        data = {k: f"v-{k}" for k in self._keys(flows.SUCCESS_SCREEN)}
        out = flows._check_contract({"screen": flows.SUCCESS_SCREEN, "data": dict(data)})
        self.assertEqual(out["data"], data)

    def test_a_non_string_value_is_coerced_because_the_client_type_checks(self):
        """A Decimal or an int reaching a declared string property is the same
        unrenderable screen by a different route from a missing key."""
        from decimal import Decimal

        out = flows._check_contract({
            "screen": flows.SUCCESS_SCREEN,
            "data": {"status": 1, "message": Decimal("1000.00")}})
        self.assertEqual(out["data"], {"status": "1", "message": "1000.00"})

    def test_none_becomes_blank_rather_than_the_word_none(self):
        out = flows._check_contract({"screen": flows.SUCCESS_SCREEN,
                                     "data": {"status": None, "message": None}})
        self.assertEqual(out["data"], {"status": "", "message": ""})

    def test_an_outcome_survives_as_its_sentence(self):
        """router.Outcome is a str subclass carrying a status tag; str() on it
        must yield the customer-facing line, not a repr."""
        from whatsapp.router import OUTCOME_SUCCESS, Outcome

        out = flows._check_contract({
            "screen": flows.SUCCESS_SCREEN,
            "data": {"status": "✅ Successful",
                     "message": Outcome("Sent — the receipt is in your chat.",
                                        OUTCOME_SUCCESS)}})
        self.assertEqual(out["data"]["message"], "Sent — the receipt is in your chat.")

    def test_a_dropdown_source_is_never_stringified(self):
        """`banks` and `plans` are arrays of objects, not strings. A guard that
        knew only the key names turned a bank list into "[{'id': 'gtb'...}]" and
        broke every screen it existed to protect — caught by the VTU suite, and
        pinned here so the coercion stays type-aware."""
        plans = [{"id": "mtn-1gb", "title": "1GB • 30 days • ₦500.00"}]
        out = flows._check_contract({
            "screen": "VTU_DATA",
            "data": {"plans": plans, "summary": "MTN data",
                     "phone": "08031234567", "error": ""}})
        self.assertEqual(out["data"]["plans"], plans)

    def test_a_missing_array_becomes_an_empty_list_not_an_empty_string(self):
        """A blank of the wrong SHAPE takes the screen down exactly as a missing
        key does."""
        with self.assertLogs("whatsapp", level="ERROR"):
            out = flows._check_contract({"screen": "VTU_DATA", "data": {}})
        self.assertEqual(out["data"]["plans"], [])
        self.assertEqual(out["data"]["summary"], "")

    def test_a_string_where_an_array_belongs_is_replaced_not_forwarded(self):
        out = flows._check_contract({
            "screen": "VTU_DATA",
            "data": {"plans": "1GB", "summary": "", "phone": "", "error": ""}})
        self.assertEqual(out["data"]["plans"], [])

    def test_a_screen_the_contract_does_not_know_is_passed_through_untouched(self):
        """The ping and error-acknowledgement replies carry no screen at all, and
        a guard that cannot recognise a response must not rewrite it."""
        for response in ({"data": {"status": "active"}},
                         {"data": {"acknowledged": True}},
                         {"screen": "NOT_A_SCREEN", "data": {"whatever": 1}}):
            self.assertEqual(flows._check_contract(dict(response)), response)

    def test_every_live_ending_conforms_without_a_mismatch(self):
        """The endings a payment actually reaches, through the real builders."""
        for status in ("success", "pending", "failed", ""):
            for build in (flows._success_screen, flows._result_screen):
                out = flows._check_contract(build("Some outcome", status))
                self.assertEqual(set(out["data"]),
                                 self._keys(out["screen"]), (build.__name__, status))


class ResponseIsTraceableTests(TestCase):
    """Every trace of a Flow session in production was gunicorn's access log — a
    200 and a byte count. "Every request succeeded" and "the customer saw an
    error screen" were therefore both true and neither was diagnosable."""

    def test_the_screen_and_its_keys_are_logged(self):
        with self.assertLogs("whatsapp", level="INFO") as logged:
            flows.handle_flow_request({"action": "ping"})
        self.assertIn("wa_flow_response", "".join(logged.output))

    def test_no_value_is_ever_logged(self):
        """The payload carries balances, account names and the customer's own
        narration. Only the key names are safe to write down."""
        with self.assertLogs("whatsapp", level="INFO") as logged:
            flows.handle_flow_request({"action": "INIT", "flow_token": "nope"})
        line = "".join(logged.output)
        self.assertIn("wa_flow_response", line)
        self.assertNotIn("Start again in the chat", line)
