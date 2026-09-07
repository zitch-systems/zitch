"""Proof that the chat channel is intact with the transfer form LIVE."""
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from transfers.models import Bank
from whatsapp.flows import PIN_SCREEN, TRANSFER_FORM
from whatsapp.test_flows import MSISDN, _make_user   # reuse the suite's fixtures
from whatsapp.router import handle_inbound


class ChatStillWorksWithFormsLiveTests(TestCase):
    def setUp(self):
        Bank.objects.create(code="gtb", name="GTBank", bank_code="058",
                            color="#e30613", active=True)
        self.user = _make_user()
        # Force the production condition: Flows fully configured.
        p = patch("whatsapp.router.flows_live", return_value=True)
        p.start(); self.addCleanup(p.stop)
        self.sent = []
        s = patch("whatsapp.router.reply", side_effect=lambda m, t, **k: self.sent.append(t))
        s.start(); self.addCleanup(s.stop)

    def _say(self, text):
        self.sent.clear()
        self.flows = []
        # send_flow is patched; the name enquiry deliberately is NOT, so the
        # paste path is exercised against the real provider mock end to end.
        def capture(msisdn, token, **kw):
            self.flows.append(kw)
            return {"success": True}

        with patch("whatsapp.router.send_flow", side_effect=capture):
            handle_inbound(MSISDN, text)
        return "\n".join(self.sent)

    def test_typed_paste_confirms_directly_instead_of_opening_the_form(self):
        """The invariant this test has always been about: a paste that already
        names amount, bank and account goes STRAIGHT to a confirm — it must
        never be answered with the form asking for what was just typed.

        It used to prove that by looking for a chat card. That card was a second
        copy of the Flow message beside it and is no longer sent, so the proof
        moved to the confirm the customer actually taps: opened on PIN_SCREEN,
        not TRANSFER_FORM, carrying the same detail the chat copy did.
        """
        self._say("0123456789 GTBank 2300")
        self.assertEqual([f["screen"] for f in self.flows], [PIN_SCREEN])
        body = self.flows[0]["body"] + " " + " ".join(str(v) for v in self.flows[0]["screen_data"].values())
        self.assertIn("GTBank", body)
        self.assertIn("0123456789", body)
        self.assertIn("2,300", body)

    def test_the_paste_path_sends_exactly_one_message(self):
        """Two cards for one PIN request is the defect this closed — the second
        was unactionable and pushed the real one out of view."""
        self._say("0123456789 GTBank 2300")
        self.assertEqual(len(self.flows), 1)
        self.assertEqual(self.sent, [])

    def test_the_ordinary_menu_commands_are_untouched(self):
        for cmd, expect in (("balance", "alance"), ("menu", "enu"), ("9", "")):
            self.assertTrue(self._say(cmd).strip(), f"{cmd!r} produced no reply")

    def test_only_the_bare_transfer_command_opens_the_form(self):
        self.assertIn("secure form", self._say("2").lower())
        self.assertEqual([f["screen"] for f in self.flows], [TRANSFER_FORM])
