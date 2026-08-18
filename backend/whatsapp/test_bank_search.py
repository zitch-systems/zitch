"""Searching the bank list on WhatsApp, given a Dropdown that cannot be searched.

Flow JSON has no filter on a Dropdown and no way for a TextInput to narrow one on
the device, so a list of every Nigerian bank is something you scroll and nothing
else. The only place it can be searched at all is the server, on a submit: type a
name, tap Continue, and the list comes back short.

Two properties this must hold, both learned the hard way in this repo:

* A Dropdown bound to an EMPTY array does not render — the customer gets a blank
  sheet with no way forward but the X (#357). So a search that matches nothing
  must degrade to the full list, never to an empty one.
* Re-rendering the same screen id keeps the form's typed values, so the amount and
  account survive a narrowing. That retention is the reason PIN_RETRY is a
  separate screen; here it is the feature.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from transfers.models import Bank
from whatsapp import flows, router
from whatsapp.models import PendingAction
from whatsapp.test_flows import MSISDN, _make_user

BANKS = [
    ("kuda", "Kuda", "090267"), ("opay", "OPay", "999992"),
    ("gtb", "GTBank", "058"), ("first", "First Bank", "011"),
    ("uba", "UBA", "033"), ("access", "Access Bank", "044"),
]


class BankSearchTests(TestCase):

    def setUp(self):
        self.user = _make_user()
        for code, name, bank_code in BANKS:
            Bank.objects.get_or_create(code=code, defaults={
                "name": name, "bank_code": bank_code, "color": "#000", "active": True})
        self.pa = PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="transfer",
            state=flows.FLOW_FORM_STATE, payload={"pin_attempts": 0},
            expires_at=timezone.now() + timedelta(minutes=5))
        self.token = flows.sign_flow_token(self.pa)

    def _submit(self, **data):
        return flows._submit_transfer_form(self.token, data)

    # ---- the filter itself ----

    def test_a_query_narrows_the_list(self):
        items = router._bank_items(query="kuda")
        self.assertEqual([i["title"] for i in items], ["Kuda"])

    def test_matching_is_case_insensitive_and_substring(self):
        """People type "kuda" lower case, and look for "bank" mid-name."""
        self.assertEqual(len(router._bank_items(query="KUDA")), 1)
        titles = [i["title"] for i in router._bank_items(query="bank")]
        self.assertIn("GTBank", titles)
        self.assertIn("First Bank", titles)

    def test_a_query_matching_nothing_returns_the_FULL_list(self):
        """An empty Dropdown does not render at all — a bad search must not hand
        the customer a blank sheet."""
        full = router._bank_items()
        self.assertEqual(len(router._bank_items(query="zzzzz")), len(full))
        self.assertGreater(len(full), 0)

    def test_a_blank_query_changes_nothing(self):
        for blank in ("", "   ", None):
            self.assertEqual(len(router._bank_items(query=blank)), len(router._bank_items()))

    # ---- the submit branch ----

    def test_a_search_re_renders_the_form_narrowed(self):
        r = self._submit(bank_search="kuda", amount="", account_number="", bank="")
        self.assertEqual(r["screen"], flows.TRANSFER_FORM)
        self.assertEqual([b["title"] for b in r["data"]["banks"]], ["Kuda"])
        self.assertIn("1 bank", r["data"]["hint"])
        self.assertEqual(r["data"]["error"], "")

    def test_a_search_does_not_demand_a_valid_amount_first(self):
        """The customer hunting for their bank has not finished the form. Telling
        them the amount is wrong is refusing what they asked for."""
        r = self._submit(bank_search="opay")
        self.assertEqual(r["screen"], flows.TRANSFER_FORM)
        self.assertEqual(r["data"]["error"], "")
        self.assertIn("OPay", [b["title"] for b in r["data"]["banks"]])

    def test_a_picked_bank_wins_over_leftover_search_text(self):
        """Otherwise stale text traps someone in a loop of narrowing instead of
        paying. A search is a state, not a mode."""
        r = self._submit(bank_search="kuda", bank="gtb", amount="abc", account_number="")
        # Fell through to validation, i.e. it was treated as a real submit.
        self.assertEqual(r["screen"], flows.TRANSFER_FORM)
        self.assertTrue(r["data"]["error"])
        # No SEARCH hint: the line is the standing explainer, not a report about
        # a narrowing that did not happen. (It is never blank — the two
        # bank-shaped fields need one sentence saying they are one question.)
        self.assertNotIn("kuda", r["data"]["hint"])
        self.assertNotIn("matching", r["data"]["hint"])
        self.assertEqual(r["data"]["hint"], flows._DEFAULT_TRANSFER_HINT)

    def test_a_failed_search_says_so_and_still_shows_every_bank(self):
        r = self._submit(bank_search="zzzzz")
        self.assertEqual(len(r["data"]["banks"]), len(router._bank_items()))
        self.assertIn("No bank name contains", r["data"]["hint"])

    def test_the_search_text_is_bounded(self):
        r = self._submit(bank_search="x" * 500)
        self.assertLessEqual(len(r["data"]["hint"]), 200)
        self.assertEqual(r["screen"], flows.TRANSFER_FORM)

    # ---- the contract, which is what breaks the whole Flow when wrong ----

    def test_every_transfer_form_response_matches_the_published_screen(self):
        """A declared-but-absent property is what WhatsApp renders as "Couldn't
        load content. Try again later." — on every ending of the Flow."""
        import json
        import pathlib

        doc = json.loads((pathlib.Path(flows.__file__).parent / "flow_assets"
                          / "pin_flow.json").read_text(encoding="utf-8"))
        declared = {s["id"]: set(s.get("data") or {}) for s in doc["screens"]}
        for label, r in (
            ("search", self._submit(bank_search="kuda")),
            ("no match", self._submit(bank_search="zzzzz")),
            ("bad amount", self._submit(bank="gtb", amount="abc")),
            ("plain", flows._transfer_form_screen()),
            ("error", flows._transfer_form_screen(error="nope")),
        ):
            self.assertEqual(set(r["data"]), declared[r["screen"]], label)


class BankNamedInChatTests(TestCase):
    """A customer who says "send 5k to my Kuda account" has already told us the
    bank. Opening the form on all ~200 of them asks the same question again, which
    is the channel telling them it did not read their message.

    The AI already extracts bank_name; _start_transfer discarded it — the same
    discard that lost the narration on this exact path.
    """

    def setUp(self):
        self.user = _make_user()
        for code, name, bank_code in BANKS:
            Bank.objects.get_or_create(code=code, defaults={
                "name": name, "bank_code": bank_code, "color": "#000", "active": True})

    def _open_form(self, intent_input):
        from unittest.mock import patch

        sent = {}
        with patch.object(router, "flows_live", return_value=True), \
             patch.object(router, "send_flow",
                          side_effect=lambda m, t, **kw: sent.update(kw) or {"success": True}), \
             patch.object(router, "_begin_bank_transfer", return_value=False), \
             patch.object(router, "reply"):
            router.dispatch_intent(self.user, MSISDN,
                                   {"name": "transfer", "input": intent_input})
        return sent

    def test_a_named_bank_narrows_the_form_to_it(self):
        sent = self._open_form({"amount": 5000, "bank_name": "Kuda"})
        self.assertEqual([b["title"] for b in sent["screen_data"]["banks"]], ["Kuda"])
        self.assertIn("Kuda", sent["screen_data"]["hint"])

    def test_no_named_bank_leaves_the_list_whole_and_silent(self):
        sent = self._open_form({"amount": 5000})
        self.assertEqual(len(sent["screen_data"]["banks"]), len(router._bank_items()))
        self.assertEqual(sent["screen_data"]["hint"], "")

    def test_an_unmatched_bank_name_still_shows_every_bank(self):
        """A misheard bank must not strand someone on a list without theirs."""
        sent = self._open_form({"amount": 5000, "bank_name": "Barclays"})
        self.assertEqual(len(sent["screen_data"]["banks"]), len(router._bank_items()))
        self.assertIn("could not match", sent["screen_data"]["hint"])

    def test_the_send_payload_still_matches_the_published_screen(self):
        import json
        import pathlib

        doc = json.loads((pathlib.Path(flows.__file__).parent / "flow_assets"
                          / "pin_flow.json").read_text(encoding="utf-8"))
        declared = {s["id"]: set(s.get("data") or {}) for s in doc["screens"]}
        for label, inp in (("named", {"amount": 1, "bank_name": "Kuda"}),
                           ("unnamed", {"amount": 1})):
            sent = self._open_form(inp)
            self.assertEqual(set(sent["screen_data"]), declared[sent["screen"]], label)

    def test_it_does_not_outlive_the_message(self):
        self._open_form({"amount": 1, "bank_name": "Kuda"})
        self.assertEqual(router._ai_bank.get(""), "")
