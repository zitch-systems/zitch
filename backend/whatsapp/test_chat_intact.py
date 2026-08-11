"""Proof that the chat channel is intact with the transfer form LIVE."""
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from transfers.models import Bank
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
        # send_flow is patched; the name enquiry deliberately is NOT, so the
        # paste path is exercised against the real provider mock end to end.
        with patch("whatsapp.router.send_flow", return_value={"success": True}):
            handle_inbound(MSISDN, text)
        return "\n".join(self.sent)

    def test_typed_paste_still_confirms_in_chat_not_a_form(self):
        out = self._say("0123456789 GTBank 2300")
        self.assertIn("Confirm transfer", out)      # the chat card, not a form
        self.assertIn("GTBank", out)
        self.assertIn("0123456789", out)
        self.assertIn("2,300", out)

    def test_the_ordinary_menu_commands_are_untouched(self):
        for cmd, expect in (("balance", "alance"), ("menu", "enu"), ("9", "")):
            self.assertTrue(self._say(cmd).strip(), f"{cmd!r} produced no reply")

    def test_only_the_bare_transfer_command_opens_the_form(self):
        self.assertIn("secure form", self._say("2").lower())
