"""What customers actually type, and what the chat must make of it (16 Nov 2026).

From production screenshots:
  * "Recharge 08037217665 100naira" was answered "How much airtime would you like
    to buy for 08037217665100?" - the model glued the number to the amount, and
    the parser that reads it correctly only ran after the model.
  * "Send 3100 to 5476951723 moniepoint", typed while the chat waited for an
    account number, was refused as "not a 10-digit account number": every digit
    in the sentence was joined into one 14-digit number.
And from the request that followed: an account number and an amount alone is a
transfer, and bank names arrive misspelt.
"""
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from transfers.models import Bank
from whatsapp import router
from whatsapp.models import PendingAction
from whatsapp.tests import make_user
from whatsapp.test_flows import MSISDN as MSISDN_FLOW

MSISDN = "2348011117777"

BANKS = [
    ("moniepoint", "Moniepoint MFB", "50515"), ("opay", "OPay", "999992"),
    ("palmpay", "PalmPay", "999991"), ("kuda", "Kuda", "090267"),
    ("gtb", "GTBank", "058"), ("firstbank", "First Bank", "011"),
    ("uba", "UBA", "033"), ("access", "Access Bank", "044"),
    ("zenith", "Zenith Bank", "057"), ("wema", "Wema Bank", "035"),
    ("fidelity", "Fidelity Bank", "070"), ("stanbic", "Stanbic IBTC", "221"),
    ("sterling", "Sterling Bank", "232"), ("ecobank", "Ecobank", "050"),
    ("union", "Union Bank", "032"), ("polaris", "Polaris Bank", "076"),
    ("fcmb", "FCMB", "214"),
]


def _banks():
    for code, name, bank_code in BANKS:
        Bank.objects.get_or_create(code=code, defaults={
            "name": name, "bank_code": bank_code, "color": "#000", "active": True})


class AirtimeAndDataTextTests(TestCase):
    def intent(self, text):
        return router.vas_text_intent(text)

    def test_phone_and_amount_with_the_currency_word_glued_on(self):
        for text in ("Recharge 08037217665 100naira", "recharge 08037217665 100 naira",
                     "Recharge 08037217665 N100", "recharge 08037217665 ₦100",
                     "airtime 100 08037217665", "100 airtime to 08037217665",
                     "08037217665 airtime 100", "buy 100 airtime for 08037217665"):
            with self.subTest(text=text):
                got = self.intent(text)
                self.assertEqual(got["name"], "buy_airtime")
                self.assertEqual(Decimal(got["input"]["amount"]), Decimal("100"))
                self.assertEqual(got["input"]["phone"], "08037217665")

    def test_a_spaced_or_international_number_is_one_phone(self):
        for text, phone in (("recharge 0803 721 7665 200", "08037217665"),
                            ("recharge 0803-721-7665 200", "08037217665"),
                            ("airtime 200 to +234 803 721 7665", "+2348037217665"),
                            ("airtime 200 to 2348037217665", "2348037217665")):
            with self.subTest(text=text):
                got = self.intent(text)
                self.assertEqual(Decimal(got["input"]["amount"]), Decimal("200"))
                self.assertEqual(got["input"]["phone"], phone)

    def test_misspelt_product_words(self):
        for text in ("airtym 200 08037217665", "air time 200 08037217665",
                     "rechage 08037217665 200", "recharg 08037217665 200",
                     "top up 08037217665 200", "topup 08037217665 200",
                     "airtme 200 08037217665"):
            with self.subTest(text=text):
                got = self.intent(text)
                self.assertIsNotNone(got)
                self.assertEqual(got["name"], "buy_airtime")
                self.assertEqual(got["input"]["phone"], "08037217665")

    def test_data(self):
        self.assertEqual(self.intent("data 08037217665"),
                         {"name": "buy_data", "input": {"phone": "08037217665"}})
        got = self.intent("1gb data for 08037217665")
        self.assertEqual(got["name"], "buy_data")
        self.assertEqual(got["input"]["phone"], "08037217665")

    def test_own_line_without_a_number(self):
        got = self.intent("recharge me 300")
        self.assertEqual(Decimal(got["input"]["amount"]), Decimal("300"))
        self.assertIsNone(got["input"]["phone"])

class TransferTextTests(TestCase):
    def setUp(self):
        _banks()
        self.user, _ = make_user(phone="08010000072", email="send@zitch.test")

    def paste(self, text, last_resort=False):
        with patch.object(router, "_begin_bank_transfer", return_value=True) as begin:
            handled = router._start_transfer_from_paste(self.user, MSISDN, text,
                                                        last_resort=last_resort)
        return handled, begin

    def test_account_number_and_amount_only(self):
        for text in ("5476951723 3100", "3100 5476951723", "5476951723 3,100",
                     "5476951723 3.1k", "₦3100 5476951723"):
            with self.subTest(text=text):
                handled, begin = self.paste(text, last_resort=True)
                self.assertTrue(handled)
                self.assertEqual(begin.call_args.args[2], Decimal("3100"))
                self.assertEqual(begin.call_args.args[3], "5476951723")

    def test_a_whole_instruction_typed_while_waiting_for_the_account(self):
        pa = router._new_flow(self.user, MSISDN, "transfer", "account",
                              {"amount": "3100", "pin_attempts": 0})
        with patch.object(router, "_offer_bank") as offer, \
                patch.object(router, "reply") as said:
            router._advance_transfer(pa, self.user, MSISDN,
                                     "Send 3100 to 5476951723 moniepoint")
        pa = PendingAction.objects.filter(msisdn=MSISDN).order_by("-pk").first()
        self.assertEqual(pa.payload.get("account"), "5476951723")
        texts = " ".join(str(c.args[1]) for c in said.call_args_list)
        self.assertNotIn("doesn't look like a 10-digit", texts)
        offer.assert_called_once()
        self.assertEqual(offer.call_args.args[3].bank.code, "moniepoint")

    def test_a_spaced_account_number_is_still_ten_digits(self):
        pa = router._new_flow(self.user, MSISDN, "transfer", "account",
                              {"amount": "3100", "pin_attempts": 0})
        with patch.object(router, "reply"):
            router._advance_transfer(pa, self.user, MSISDN, "547 695 1723")
        pa.refresh_from_db()
        self.assertEqual(pa.payload.get("account"), "5476951723")


class MisspeltBankTests(TestCase):
    def setUp(self):
        _banks()

    def test_common_misspellings_name_one_bank(self):
        cases = {
            "moniepoint": ["monipoint", "moneypoint", "moniepiont", "monie point", "moni point",
                           "monipont", "money point", "moniepoit"],
            "opay": ["opey", "o pay", "opai", "0pay", "oplay"],
            "palmpay": ["palmpey", "palm pay", "plampay", "pampay", "palmapay"],
            "kuda": ["kudda", "kuda bank", "kudabank"],
            "gtb": ["gtbank", "gt bank", "gtbnak", "guaranty trust", "gurantee trust", "gtb"],
            "firstbank": ["firstbnk", "frist bank", "firstbank", "first bank"],
            "access": ["acess", "acces", "axcess", "access bank"],
            "zenith": ["zenit", "zenth", "zenith bank", "zeenith"],
            "uba": ["uba", "united bank for africa"],
            "fidelity": ["fidelty", "fidility", "fedelity"],
            "stanbic": ["stanbick", "stanbik", "stambic"],
            "sterling": ["sterlin", "starling"],
            "ecobank": ["ecobnk", "eco bank"],
            "polaris": ["polaris", "polaris bank", "polarise"],
            "wema": ["wema", "alat", "wemma"],
        }
        for slug, spellings in cases.items():
            for text in spellings:
                with self.subTest(text=text):
                    found = [b.code for b in router._match_banks(text)]
                    self.assertEqual(found[:1], [slug], found)


from whatsapp.models import SystemSetting, WhatsAppLink  # noqa: E402
from whatsapp.test_chat_followups import ChatTestCase  # noqa: E402


@patch("whatsapp.ai.llm_available", return_value=True)
class ParserBeforeModelTests(ChatTestCase):
    """With the model switched on, the parser that reads the number correctly
    answers first: the model's question named 08037217665100."""

    def setUp(self):
        super().setUp()
        WhatsAppLink.objects.filter(wa_msisdn=MSISDN_FLOW).update(ai_enabled=True)
        SystemSetting.set("ai_enabled_global", "true")

    def test_the_number_wins_over_the_model_that_garbled_it(self, _avail):
        garbled = {"name": "clarify", "input": {
            "reason": "How much airtime would you like to buy for 08037217665100?"}}
        with patch("whatsapp.ai.extract_intent", return_value=garbled) as model, \
                patch.object(router, "dispatch_intent", return_value=True) as dispatch:
            out = self.say("Recharge 08037217665 100naira")
        model.assert_not_called()
        self.assertNotIn("08037217665100", out)
        intent = dispatch.call_args.args[2]
        self.assertEqual(intent["input"]["phone"], "08037217665")
        self.assertEqual(Decimal(intent["input"]["amount"]), Decimal("100"))

    def test_without_a_number_the_model_reads_whose_line_it_is(self, _avail):
        with patch("whatsapp.ai.extract_intent", return_value=None) as model, \
                patch.object(router, "dispatch_intent", return_value=False):
            self.say("load Farida 200 airtime")
        model.assert_called_once()

    def test_a_sentence_the_parser_cannot_finish_still_reaches_the_model(self, _avail):
        with patch("whatsapp.ai.extract_intent", return_value=None) as model:
            self.say("can you help me recharge my mum's line later")
        model.assert_called_once()
