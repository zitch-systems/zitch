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
    ("SIGNUP_SCREEN", "SIGNUP_SCREEN"),
    ("SIGNUP_SCREEN", "SIGNUP_PHONE"), ("SIGNUP_SCREEN", "SUCCESS"),
    ("SIGNUP_PHONE", "SIGNUP_PHONE"), ("SIGNUP_PHONE", "PIN_CHAIN"), ("SIGNUP_PHONE", "SUCCESS"),
    ("EMAIL_SCREEN", "EMAIL_SCREEN"), ("EMAIL_SCREEN", "IDENTITY_CHAIN"), ("EMAIL_SCREEN", "SUCCESS"),
    ("TRANSFER_FORM", "TRANSFER_FORM"), ("TRANSFER_FORM", "PIN_CHAIN"), ("TRANSFER_FORM", "SUCCESS"),
    # The airtime/data ladder. Same-screen re-renders are the right answer here
    # and NOT the defect the PIN pages have: these fields are visible, so a
    # refused value staying in the box is help rather than a trap.
    ("VTU_SCREEN", "VTU_SCREEN"), ("VTU_SCREEN", "VTU_NETWORK"), ("VTU_SCREEN", "SUCCESS"),
    ("VTU_NETWORK", "VTU_NETWORK"), ("VTU_NETWORK", "VTU_AIRTIME"),
    ("VTU_NETWORK", "VTU_DATA"), ("VTU_NETWORK", "SUCCESS"),
    ("VTU_AIRTIME", "VTU_AIRTIME"), ("VTU_AIRTIME", "PIN_CHAIN"), ("VTU_AIRTIME", "SUCCESS"),
    ("VTU_DATA", "VTU_DATA"), ("VTU_DATA", "PIN_CHAIN"), ("VTU_DATA", "SUCCESS"),
    ("IDENTITY_SCREEN", "IDENTITY_RETRY"), ("IDENTITY_SCREEN", "IDENTITY_CHAIN"), ("IDENTITY_SCREEN", "SUCCESS"),
    ("IDENTITY_RETRY", "IDENTITY_CHAIN"), ("IDENTITY_RETRY", "SUCCESS"),
    ("IDENTITY_CHAIN", "CODE_RETRY"), ("IDENTITY_CHAIN", "SUCCESS"),
    ("CODE_SCREEN", "CODE_RETRY"), ("CODE_SCREEN", "PIN_CHAIN"), ("CODE_SCREEN", "SUCCESS"),
    ("CODE_RETRY", "PIN_CHAIN"), ("CODE_RETRY", "SUCCESS"),
    ("SIGNUP_PHONE", "SIGNUP_PHONE_CODE"),
    ("SIGNUP_PHONE_CODE", "SIGNUP_PHONE_CODE"), ("SIGNUP_PHONE_CODE", "PIN_CHAIN"),
    ("SIGNUP_PHONE_CODE", "SUCCESS"),
    ("PIN_SCREEN", "PIN_RETRY"), ("PIN_SCREEN", "PIN_CONFIRM"), ("PIN_SCREEN", "SUCCESS"),
    ("PIN_CHAIN", "PIN_RETRY"), ("PIN_CHAIN", "PIN_CONFIRM"), ("PIN_CHAIN", "SUCCESS"),
    # No ("PIN_RETRY", "PIN_RETRY") and no
    # ("PIN_CONFIRM_RETRY", "PIN_CONFIRM_RETRY"): a masked box re-rendered onto
    # its own id comes back holding the digits that just failed, so the PIN steps
    # are capped in code (PIN_FLOW_ATTEMPTS, _PIN_CREATE_ATTEMPTS,
    # _PIN_CONFIRM_ATTEMPTS) to make those two moves unreachable. Listing them
    # here declared them legal and let the suite bless the defect.
    ("PIN_RETRY", "PIN_CONFIRM"),   # create path parks on the twin, then confirms
    ("PIN_RETRY", "SUCCESS"),
    ("PIN_CONFIRM", "PIN_CONFIRM_RETRY"), ("PIN_CONFIRM", "SUCCESS"),
    ("PIN_CONFIRM_RETRY", "SUCCESS"),
    # Release 2 (whatsapp/release2.py, every one behind a default-off flag).
    ("PRIVACY_NOTICE", "DETAILS_CHAIN"),
    *[(src, dst) for src in ("SIGNUP_DETAILS", "DETAILS_CHAIN")
      for dst in (src, "VERIFY_PASSWORD", "SIGNUP_PHONE_CODE", "SIGNUP_PASSWORD",
                  "PIN_CHAIN", "SUCCESS")],
    ("SIGNUP_PHONE", "VERIFY_PASSWORD"),
    # Visible code box; the password boxes are kept exactly as SIGNUP_PASSWORD keeps them.
    ("VERIFY_PASSWORD", "VERIFY_PASSWORD"), ("VERIFY_PASSWORD", "PIN_CHAIN"),
    ("VERIFY_PASSWORD", "SUCCESS"),
    # A wrong code lands on the empty twin; a right code + wrong PIN on PIN_RETRY.
    ("LOGIN_SCREEN", "LOGIN_RETRY"), ("LOGIN_SCREEN", "PIN_RETRY"), ("LOGIN_SCREEN", "RESULT"),
    ("LOGIN_RETRY", "PIN_RETRY"), ("LOGIN_RETRY", "RESULT"),
    ("MENU", "MENU"), ("MENU", "TRANSFER_CHAIN"), ("MENU", "VTU_CHAIN"), ("MENU", "BILL_CHAIN"),
    ("MENU", "RESULT"), ("MENU", "SUCCESS"),
    ("TRANSFER_CHAIN", "TRANSFER_CHAIN"), ("TRANSFER_CHAIN", "PIN_CHAIN"),
    ("TRANSFER_CHAIN", "SUCCESS"),
    ("VTU_CHAIN", "VTU_CHAIN"), ("VTU_CHAIN", "VTU_NETWORK"), ("VTU_CHAIN", "SUCCESS"),
    *[(src, dst) for src in ("BILL_SCREEN", "BILL_CHAIN")
      for dst in (src, "BILL_AMOUNT", "SUCCESS")],
    ("BILL_AMOUNT", "BILL_AMOUNT"), ("BILL_AMOUNT", "PIN_CHAIN"), ("BILL_AMOUNT", "SUCCESS"),
    # Release 3 (whatsapp/release3.py, every one behind a default-off flag).
    # Visible fields again, so same-screen re-renders keep what was typed.
    ("MENU", "SEND_MONEY_CHAIN"), ("MENU", "VTU_FORM_CHAIN"),
    *[(src, dst) for src in ("SEND_MONEY", "SEND_MONEY_CHAIN")
      for dst in (src, "PIN_CHAIN", "SUCCESS")],
    *[(src, dst) for src in ("VTU_FORM", "VTU_FORM_CHAIN")
      for dst in (src, "VTU_PLAN", "PIN_CHAIN", "SUCCESS")],
    ("VTU_PLAN", "VTU_PLAN"), ("VTU_PLAN", "PIN_CHAIN"), ("VTU_PLAN", "SUCCESS"),
]

#: Same-id answers that DO still happen, each one a masked box that comes back
#: holding a refused value. Enumerated rather than silently exempted, so this
#: list is the open-work list: it shrinks as each is capped, and
#: test_no_pin_screen_re_renders_onto_itself stops anything PIN-shaped rejoining.
KNOWN_SELF_RENDERS = [
    ("IDENTITY_RETRY", "IDENTITY_RETRY"),    # bounded by _MAX_ID_ATTEMPTS
    ("CODE_RETRY", "CODE_RETRY"),            # account_flow_otp is still uncapped
]


class FlowFileContractTests(SimpleTestCase):

    def test_only_active_partnership_screens_are_publishable(self):
        self.assertFalse(any(name.startswith("VAS_") for name in BY_ID))
        self.assertFalse(any(name.startswith("VAS_") for name in ROUTES))
        self.assertFalse(any(name.startswith("VAS_") for targets in ROUTES.values() for name in targets))

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

    def test_no_pin_screen_re_renders_onto_itself(self):
        """test_every_server_transition_is_a_legal_move exempts src == dst, so it
        fails OPEN on the one defect that matters most here: a masked PIN box
        answered with its own screen id comes back holding the digits that just
        failed — and at min/max 6 it then refuses new keystrokes until six
        invisible characters are deleted. Every PIN step is capped in code so
        those moves are unreachable; this stops one being declared legal again.
        """
        self_renders = [(a, b) for a, b in SERVER_TRANSITIONS if a == b]
        self.assertEqual([p for p in self_renders if "PIN" in p[0]], [])
        # The remaining ones are tracked, not forgotten.
        self.assertEqual(sorted(set(self_renders) & set(KNOWN_SELF_RENDERS)), [])

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

        for sid in ("PIN_SCREEN", "PIN_CHAIN", "PIN_RETRY", "PIN_CONFIRM", "PIN_CONFIRM_RETRY",
                    "LOGIN_SCREEN", "LOGIN_RETRY"):
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
        # 11 before Release 2, plus the code and the PIN on LOGIN_SCREEN and
        # on its LOGIN_RETRY twin, plus Release 5's SIGNUP_PIN / SIGNUP_PIN_RETRY
        # PIN boxes. Their confirm boxes (confirm_pin) are checked at the end.
        self.assertEqual(len(found), 17)
        for inp in found:
            # The email ADDRESS rides the same field name but is not digits —
            # it keeps the email keyboard. Everything else is a passcode pad.
            expected = "email" if inp.get("input-type") == "email" else "passcode"
            self.assertEqual(inp.get("input-type"), expected)
        self.assertEqual(sum(1 for i in found if i.get("input-type") == "passcode"), 16)
        for sid in ("SIGNUP_PIN", "SIGNUP_PIN_RETRY"):
            confirm = [c for c in BY_ID[sid]["layout"]["children"][-1]["children"]
                       if c.get("name") == "confirm_pin"]
            self.assertEqual([(c["input-type"], c.get("min-chars"), c.get("max-chars")) for c in confirm],
                             [("passcode", 6, 6)], sid)

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

        for sid in ("SIGNUP_SCREEN", "SIGNUP_DETAILS", "DETAILS_CHAIN"):
            found = list(named_inputs(BY_ID[sid]["layout"]))
            self.assertEqual(len(found), 2, sid)
            for inp in found:
                self.assertEqual(inp.get("min-chars"), 2, (sid, inp["name"]))

    def test_the_logo_is_the_same_bytes_on_every_screen(self):
        # The Zitch logo is on every screen. The Wema / CBN / NDIC logo strip
        # is only on the privacy notice (the outcome screens carry the
        # partnership caption and link instead), which also keeps the Flow JSON
        # under the upload limit. Each image must be identical wherever it appears.
        def srcs(node, alt):
            if isinstance(node, dict):
                if node.get("type") == "Image" and node.get("alt-text") == alt:
                    yield node.get("src")
                for v in node.values():
                    yield from srcs(v, alt)
            elif isinstance(node, list):
                for v in node:
                    yield from srcs(v, alt)

        found = [s for screen in FLOW["screens"] for s in srcs(screen["layout"], "Zitch")]
        self.assertEqual(len(found), len(FLOW["screens"]))
        self.assertEqual(len(set(found)), 1)
        strip = {screen["id"]: list(srcs(screen["layout"], "Wema Bank, CBN and NDIC"))
                 for screen in FLOW["screens"]}
        self.assertEqual([sid for sid, s in strip.items() if s], ["PRIVACY_NOTICE"])
        self.assertEqual(len(strip["PRIVACY_NOTICE"]), 1)

    #: Where the Wema / CBN / NDIC notice and its "Learn more" link are shown.
    #:
    #: Release 3's approved design took the notice off the FORM screens: on a
    #: phone it pushed the fields - and on the PIN pages the amount and the
    #: recipient - below the fold, on every page of every journey. It stays where
    #: it is read rather than scrolled past: PRIVACY_NOTICE, where an account is
    #: opened and the partnership is explained (with the logo strip), and the two
    #: outcome screens every journey ends on (RESULT, and SUCCESS on a Flow
    #: published before RESULT). The chat also carries it (router.PARTNER_NOTICE).
    NOTICE_SCREENS = {"PRIVACY_NOTICE", "RESULT", "SUCCESS"}
    NOTICE = "Banking services by Wema Bank Plc, licensed by the CBN. Deposits insured by NDIC."

    def _notice(self, screen):
        children = screen["layout"]["children"]
        captions = [c for c in children if c.get("type") == "TextCaption" and c.get("text") == self.NOTICE]
        links = [c for c in children if c.get("type") == "EmbeddedLink"
                 and (c.get("on-click-action") or {}).get("url") == "https://zitch.ng/partnership"]
        return captions, links

    def test_the_partnership_notice_is_on_the_privacy_notice_and_the_outcome_screens(self):
        for sid in sorted(self.NOTICE_SCREENS):
            with self.subTest(screen=sid):
                screen = BY_ID[sid]
                captions, links = self._notice(screen)
                self.assertEqual(len(captions), 1)
                self.assertEqual([link["on-click-action"] for link in links],
                                 [{"name": "open_url", "url": "https://zitch.ng/partnership"}])
                kinds = [c.get("type") for c in screen["layout"]["children"]]
                # The footer button stays last: the notice sits above it.
                self.assertIn(kinds[-1], ("Form", "Footer"))

    def test_no_form_screen_carries_the_partnership_notice(self):
        """Every other screen is a form, and the design removed it from forms."""
        for screen in FLOW["screens"]:
            if screen["id"] in self.NOTICE_SCREENS:
                continue
            with self.subTest(screen=screen["id"]):
                self.assertEqual(self._notice(screen), ([], []))
                self.assertEqual(screen["layout"]["children"][-1]["type"], "Form")

    def test_no_label_says_optional_because_whatsapp_already_does(self):
        """WhatsApp appends "(optional)" to every input that is not required, so
        a label that says it too reads "Narration (optional) (optional)"."""
        def labels(node):
            if isinstance(node, dict):
                if isinstance(node.get("label"), str):
                    yield node["label"]
                for v in node.values():
                    yield from labels(v)
            elif isinstance(node, list):
                for v in node:
                    yield from labels(v)

        found = [label for s in FLOW["screens"] for label in labels(s["layout"])]
        self.assertTrue(found)
        self.assertEqual([label for label in found if "optional" in label.lower()], [])


class BuildersMatchDeclaredKeysTests(TestCase):
    """Each screen builder must supply exactly the keys its screen declares —
    an extra key fails the exchange at Meta; a missing one renders a hole."""

    def _check(self, resp):
        from whatsapp.flows import live_data_keys

        declared = live_data_keys()[resp["screen"]]
        self.assertEqual(set(resp["data"].keys()), declared, resp["screen"])

    def test_every_builder(self):
        from transfers.models import Bank

        Bank.objects.create(code="gtb", name="GTBank", bank_code="058",
                            color="#e30613", active=True)
        for resp in (
            F._signup_screen(),
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
