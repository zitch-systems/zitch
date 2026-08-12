"""The full contract between the published Flow and the code that drives it.

Every failure class that has reached production is an invariant here:
screens opened on must be routing roots (#307), payloads must match declared
keys, masked retries must land on a fresh screen id (#309, and the confirm
pair here), and every screen the server can answer with must be a legal move
from the screen the customer is standing on — the class Meta only reveals on
a live device.
"""
import collections
import json
from pathlib import Path

from django.test import SimpleTestCase, TestCase

import whatsapp
from whatsapp import flows as F

FLOW = json.loads((Path(whatsapp.__file__).parent / "flow_assets" / "pin_flow.json").read_text())
BY_ID = {s["id"]: s for s in FLOW["screens"]}
ROUTES = FLOW["routing_model"]

#: Every (from, to) move a server handler can answer with. Same-screen
#: re-renders are always legal; anything else must be a declared route.
SERVER_TRANSITIONS = [
    ("SIGNUP_SCREEN", "SIGNUP_SCREEN"), ("SIGNUP_SCREEN", "SIGNUP_EMAIL_CODE"),
    ("SIGNUP_SCREEN", "SIGNUP_PHONE"), ("SIGNUP_SCREEN", "SUCCESS"),
    ("SIGNUP_EMAIL_CODE", "SIGNUP_EMAIL_CODE"), ("SIGNUP_EMAIL_CODE", "SIGNUP_PHONE"),
    ("SIGNUP_EMAIL_CODE", "SUCCESS"),
    ("SIGNUP_PHONE", "SIGNUP_PHONE"), ("SIGNUP_PHONE", "PIN_CHAIN"), ("SIGNUP_PHONE", "SUCCESS"),
    ("EMAIL_SCREEN", "EMAIL_SCREEN"), ("EMAIL_SCREEN", "IDENTITY_CHAIN"), ("EMAIL_SCREEN", "SUCCESS"),
    ("TRANSFER_FORM", "TRANSFER_FORM"), ("TRANSFER_FORM", "PIN_CHAIN"), ("TRANSFER_FORM", "SUCCESS"),
    ("IDENTITY_SCREEN", "IDENTITY_RETRY"), ("IDENTITY_SCREEN", "IDENTITY_CHAIN"), ("IDENTITY_SCREEN", "SUCCESS"),
    ("IDENTITY_RETRY", "IDENTITY_RETRY"), ("IDENTITY_RETRY", "IDENTITY_CHAIN"), ("IDENTITY_RETRY", "SUCCESS"),
    ("IDENTITY_CHAIN", "CODE_RETRY"), ("IDENTITY_CHAIN", "SUCCESS"),
    ("CODE_SCREEN", "CODE_RETRY"), ("CODE_SCREEN", "PIN_CHAIN"), ("CODE_SCREEN", "SUCCESS"),
    ("CODE_RETRY", "CODE_RETRY"), ("CODE_RETRY", "PIN_CHAIN"), ("CODE_RETRY", "SUCCESS"),
    ("SIGNUP_PHONE", "SIGNUP_PHONE_CODE"),
    ("SIGNUP_PHONE_CODE", "SIGNUP_PHONE_CODE"), ("SIGNUP_PHONE_CODE", "PIN_CHAIN"),
    ("SIGNUP_PHONE_CODE", "SUCCESS"),
    ("PIN_SCREEN", "PIN_RETRY"), ("PIN_SCREEN", "PIN_CONFIRM"), ("PIN_SCREEN", "SUCCESS"),
    ("PIN_CHAIN", "PIN_RETRY"), ("PIN_CHAIN", "PIN_CONFIRM"), ("PIN_CHAIN", "SUCCESS"),
    ("PIN_RETRY", "PIN_RETRY"), ("PIN_RETRY", "SUCCESS"),
    ("PIN_CONFIRM", "PIN_CONFIRM_RETRY"), ("PIN_CONFIRM", "SUCCESS"),
    ("PIN_CONFIRM_RETRY", "PIN_CONFIRM_RETRY"), ("PIN_CONFIRM_RETRY", "SUCCESS"),
]


class FlowFileContractTests(SimpleTestCase):

    def test_every_declared_property_carries_an_example(self):
        missing = [(s["id"], k) for s in FLOW["screens"]
                   for k, spec in (s.get("data") or {}).items() if "__example__" not in spec]
        self.assertEqual(missing, [])

    def test_screen_ids_are_unique(self):
        ids = [s["id"] for s in FLOW["screens"]]
        self.assertEqual([k for k, v in collections.Counter(ids).items() if v > 1], [])

    def test_every_screen_reaches_a_terminal(self):
        terminal = {s["id"] for s in FLOW["screens"] if s.get("terminal")}

        def reaches(sid, seen=frozenset()):
            if sid in terminal:
                return True
            if sid in seen:
                return False
            return any(reaches(t, seen | {sid}) for t in ROUTES.get(sid, []))

        self.assertEqual([s["id"] for s in FLOW["screens"] if not reaches(s["id"])], [])

    def test_every_server_transition_is_a_legal_move(self):
        illegal = [(src, dst) for src, dst in SERVER_TRANSITIONS
                   if src != dst and dst not in ROUTES.get(src, [])]
        self.assertEqual(illegal, [], "Meta refuses these navigations on the device")

    def test_every_transition_target_actually_exists(self):
        self.assertEqual([t for _, t in SERVER_TRANSITIONS if t not in BY_ID], [])

    def test_pin_inputs_enforce_exactly_six_digits_client_side(self):
        """A masked box cannot show what it holds, so a length error must be
        impossible to submit rather than re-rendered."""
        def pin_inputs(node):
            if isinstance(node, dict):
                if node.get("type") == "TextInput" and node.get("name") == "pin":
                    yield node
                for v in node.values():
                    yield from pin_inputs(v)
            elif isinstance(node, list):
                for v in node:
                    yield from pin_inputs(v)

        for sid in ("PIN_SCREEN", "PIN_CHAIN", "PIN_RETRY", "PIN_CONFIRM", "PIN_CONFIRM_RETRY"):
            for inp in pin_inputs(BY_ID[sid]["layout"]):
                self.assertEqual((inp.get("min-chars"), inp.get("max-chars")), (6, 6), sid)
                self.assertNotIn("4–6", inp.get("helper-text", ""))

    def test_every_secret_digit_field_raises_the_numeric_keypad(self):
        """PIN, BVN, NIN and code entries are all digits — "passcode" is masked
        AND numeric, so the customer is never handed a full text keyboard for a
        number."""
        def secret_inputs(node):
            if isinstance(node, dict):
                if node.get("type") == "TextInput" and node.get("name") in ("pin", "number"):
                    yield node
                for v in node.values():
                    yield from secret_inputs(v)
            elif isinstance(node, list):
                for v in node:
                    yield from secret_inputs(v)

        found = [inp for s in FLOW["screens"] for inp in secret_inputs(s["layout"])]
        self.assertEqual(len(found), 11)
        for inp in found:
            # The email ADDRESS rides the same field name but is not digits —
            # it keeps the email keyboard. Everything else is a passcode pad.
            expected = "email" if inp.get("input-type") == "email" else "passcode"
            self.assertEqual(inp.get("input-type"), expected)
        self.assertEqual(sum(1 for i in found if i.get("input-type") == "passcode"), 10)

    def test_identity_fields_are_eleven_and_code_fields_six(self):
        """An incomplete BVN is refused AT THE FIELD (red outline), and a code
        field cannot accept eleven digits — which is why codes have their own
        screens instead of riding IDENTITY_SCREEN."""
        def number_input(sid):
            def walk(node):
                if isinstance(node, dict):
                    if node.get("type") == "TextInput" and node.get("name") == "number":
                        return node
                    for v in node.values():
                        r = walk(v)
                        if r: return r
                elif isinstance(node, list):
                    for v in node:
                        r = walk(v)
                        if r: return r
            return walk(BY_ID[sid]["layout"])

        for sid in ("IDENTITY_SCREEN", "IDENTITY_RETRY"):
            inp = number_input(sid)
            self.assertEqual((inp["min-chars"], inp["max-chars"]), (11, 11), sid)
        for sid in ("IDENTITY_CHAIN", "CODE_SCREEN", "CODE_RETRY"):
            inp = number_input(sid)
            self.assertEqual((inp["min-chars"], inp["max-chars"]), (6, 6), sid)

    def test_signup_name_fields_refuse_a_single_letter_client_side(self):
        """The server requires >= 2 characters (the name must match the BVN
        record later); without the client constraint a one-letter entry submits,
        is refused, and the re-render reads as "unable to proceed"."""
        def named_inputs(node):
            if isinstance(node, dict):
                if node.get("type") == "TextInput" and node.get("name") in ("first_name", "last_name"):
                    yield node
                for v in node.values():
                    yield from named_inputs(v)
            elif isinstance(node, list):
                for v in node:
                    yield from named_inputs(v)

        found = list(named_inputs(BY_ID["SIGNUP_SCREEN"]["layout"]))
        self.assertEqual(len(found), 2)
        for inp in found:
            self.assertEqual(inp.get("min-chars"), 2, inp["name"])

    def test_the_logo_is_the_same_bytes_on_every_screen(self):
        def srcs(node):
            if isinstance(node, dict):
                if node.get("type") == "Image":
                    yield node.get("src")
                for v in node.values():
                    yield from srcs(v)
            elif isinstance(node, list):
                for v in node:
                    yield from srcs(v)

        all_srcs = {s for screen in FLOW["screens"] for s in srcs(screen["layout"])}
        self.assertEqual(len(all_srcs), 1)


class BuildersMatchDeclaredKeysTests(TestCase):
    """Each screen builder must supply exactly the keys its screen declares —
    an extra key fails the exchange at Meta; a missing one renders a hole."""

    def _check(self, resp):
        declared = set(BY_ID[resp["screen"]].get("data", {}).keys())
        self.assertEqual(set(resp["data"].keys()), declared, resp["screen"])

    def test_every_builder(self):
        from transfers.models import Bank

        Bank.objects.create(code="gtb", name="GTBank", bank_code="058",
                            color="#e30613", active=True)
        import types

        ob = types.SimpleNamespace(payload={"email": "a@b.test"})
        for resp in (
            F._signup_screen(),
            F._signup_email_code_screen(ob),
            F._signup_email_code_screen(ob, error="wrong"),
            F._signup_phone_screen(),
            F._signup_phone_screen(error="taken"),
            F._email_screen(),
            F._transfer_form_screen(),
            F._identity_screen("bvn"),
            F._identity_screen("bvn", screen=F.IDENTITY_RETRY),
            F._identity_screen("bvn", screen=F.IDENTITY_CHAIN),
            F._pin_screen("s"),
            F._pin_screen("s", screen=F.PIN_CHAIN),
            F._pin_screen("s", screen=F.PIN_RETRY),
            F._confirm_pin_screen(),
            F._confirm_pin_screen(error="mismatch"),
            F._success_screen("done"),
        ):
            self._check(resp)

    def test_an_error_render_of_the_confirm_pair_lands_on_the_retry_twin(self):
        """The mismatch found in production on BVN, closed on the creation pair:
        an error must never re-render the screen whose masked box holds the
        digits that just failed."""
        self.assertEqual(F._confirm_pin_screen()["screen"], F.PIN_CONFIRM)
        self.assertEqual(F._confirm_pin_screen(error="x")["screen"], F.PIN_CONFIRM_RETRY)
