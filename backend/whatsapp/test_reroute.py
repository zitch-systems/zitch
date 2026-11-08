"""A new instruction cancels the half-finished one.

From a production thread: the customer was picking a data plan, changed their mind
and typed "500 airtime for me", and was answered twice with "Reply with a plan
number from the list, or cancel". The plan picker had no way to notice it was no
longer wanted, so the channel repeated itself at somebody who had plainly moved on.

The guard is deliberately narrow. These states accept free text — a bank name, a
meter number — so it triggers only on a word that names a DIFFERENT thing to do.
A half-typed bank name must still re-prompt, not cancel the transfer it belongs to.
"""
from unittest.mock import patch

from django.test import TestCase

from . import router


class NamesAnotherIntentTests(TestCase):
    def test_a_different_service_is_recognised(self):
        for text in ("500 airtime for me", "buy data", "check my balance",
                     "send 2k to ada", "pay electricity", "scan qr", "my statement"):
            self.assertTrue(router._names_another_intent(text), text)

    def test_a_half_typed_bank_name_is_not_a_new_intent(self):
        # The failure this guard must not cause: cancelling a transfer because the
        # customer misspelled the bank they were entering.
        for text in ("Zenit", "acces", "gtb", "moniepoin", "0123456789", "3"):
            self.assertFalse(router._names_another_intent(text), text)

    def test_an_empty_message_is_not_a_new_intent(self):
        self.assertFalse(router._names_another_intent(""))
        self.assertFalse(router._names_another_intent("   "))


class RerouteTests(TestCase):
    def test_a_new_intent_cancels_and_starts_the_new_one(self):
        with patch.object(router, "_clear_actions") as clear, \
             patch.object(router, "handle_inbound") as inbound, \
             patch.object(router, "reply") as rep:
            router._reroute_or_reprompt(None, "234801", "500 airtime for me", "PROMPT")
        clear.assert_called_once()
        inbound.assert_called_once_with("234801", "500 airtime for me")
        # Named, not silent: swapping one half-finished money action for another
        # without saying so is its own way to lose somebody.
        self.assertIn("leaving that", str(rep.call_args.args[1]).lower())

    def test_anything_else_just_re_prompts(self):
        with patch.object(router, "_clear_actions") as clear, \
             patch.object(router, "handle_inbound") as inbound, \
             patch.object(router, "reply") as rep:
            router._reroute_or_reprompt(None, "234801", "Zenit", "PROMPT")
        clear.assert_not_called()
        inbound.assert_not_called()
        self.assertEqual(rep.call_args.args[1], "PROMPT")
