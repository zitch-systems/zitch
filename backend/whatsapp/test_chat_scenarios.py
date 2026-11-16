"""Fifty more ways the details arrive in the chat, and what each must become.

Each scenario is one line of the table below and one subTest, so a failure
names the exact message. Grouped by the part of the chat that reads them:
  * what the model is allowed to see (numbers masked, amounts kept),
  * airtime and data read without the model,
  * transfers pasted in one line,
  * bank names inside a sentence,
  * answers to "enter the account number".
"""
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from whatsapp import ai, router
from whatsapp.models import PendingAction
from whatsapp.test_chat_understanding import _banks
from whatsapp.tests import make_user

MSISDN = "2348011118888"

# --- 1. masking: (message, numbers the model must receive, text it must still see)
MASKING = [
    ("Recharge 08037217665 100naira", ["08037217665"], "100naira"),
    ("5476951723 3100", ["5476951723"], "3100"),
    ("3100 5476951723", ["5476951723"], "3100"),
    ("recharge 0803 721 7665 200", ["08037217665"], "200"),
    ("200 airtime 0803 721 7665", ["08037217665"], "200"),
    ("547 695 1723 3100 opay", ["5476951723"], "3100"),
    ("send 5k to 0123456789 and 2k to 0987654321", ["0123456789", "0987654321"], "5k"),
    ("0803-721-7665 500", ["08037217665"], "500"),
]

# --- 2. airtime/data without the model: (message, intent, amount, phone)
VAS = [
    ("pls recharge 08037217665 with 200", "buy_airtime", "200", "08037217665"),
    ("Recharge my line 500", "buy_airtime", "500", None),
    ("RECHARGE 08037217665 1K", "buy_airtime", "1000", "08037217665"),
    ("airtime N1,500 08037217665", "buy_airtime", "1500", "08037217665"),
    ("08037217665 recharge 300 naira", "buy_airtime", "300", "08037217665"),
    ("load 08037217665 airtime 250", "buy_airtime", "250", "08037217665"),
    ("airtime for 08037217665 #500", "buy_airtime", "500", "08037217665"),
    ("Top-up 08037217665 ₦2,000", "buy_airtime", "2000", "08037217665"),
    ("recharge +2348037217665 200", "buy_airtime", "200", "+2348037217665"),
    ("recharge 2348037217665 200", "buy_airtime", "200", "2348037217665"),
    ("airtime 200 to 0803 721 7665 pls", "buy_airtime", "200", "08037217665"),
    ("Recharge 08037217665 100naira.", "buy_airtime", "100", "08037217665"),
    ("recharge 08037217665 n200", "buy_airtime", "200", "08037217665"),
    ("Data 08037217665", "buy_data", None, "08037217665"),
    ("2gb data 08037217665", "buy_data", None, "08037217665"),
    ("data bundle for 08037217665", "buy_data", None, "08037217665"),
]

# --- 3. one-line transfers: (message, amount, account, last_resort)
TRANSFERS = [
    ("send 5000 to 5476951723 opay", "5000", "5476951723", False),
    ("Send 3k to 5476951723 moniepoint", "3000", "5476951723", False),
    ("transfer 2,500 to 5476951723 gtb", "2500", "5476951723", False),
    ("5476951723 kuda 1500", "1500", "5476951723", False),
    ("5476951723\nOpay\n2000", "2000", "5476951723", False),
    ("pay 5476951723 access 10k", "10000", "5476951723", False),
    ("send N4,000 to 5476951723 zenith", "4000", "5476951723", False),
    ("5476951723 2000", "2000", "5476951723", True),
    ("2000 5476951723", "2000", "5476951723", True),
    ("send 08031234567 opay 1000", "1000", "08031234567", False),
    ("547-695-1723 palmpay 700", "700", "5476951723", False),
    ("Send 5k to 5476951723 monipont", "5000", "5476951723", False),
    ("send #2500 to 5476951723 uba", "2500", "5476951723", False),
    ("5476951723 fidelity 1500naira", "1500", "5476951723", False),
]

# --- 4. bank named in a sentence: (message, bank slug)
BANK_IN_SENTENCE = [
    ("send 5k to 5476951723 money point", "moniepoint"),
    ("5476951723 frist bank 2000", "firstbank"),
    ("5476951723 o pay 2000", "opay"),
    ("transfer 3000 to 5476951723 guaranty trust bank", "gtb"),
    ("5476951723 united bank for africa 1000", "uba"),
    ("send 2k to 5476951723 stanbic ibtc", "stanbic"),
    ("5476951723 alat 1000", "wema"),
]

# --- 5. answers to "enter the account number": (answer, account, bank slug or None)
ACCOUNT_ANSWERS = [
    ("5476951723", "5476951723", None),
    ("547 695 1723", "5476951723", None),
    ("acct: 5476951723", "5476951723", None),
    ("Send 3100 to 5476951723 moniepoint", "5476951723", "moniepoint"),
    ("it's 5476951723 opay", "5476951723", "opay"),
]


class ScenarioCountTests(TestCase):
    def test_there_are_at_least_fifty_scenarios(self):
        total = (len(MASKING) + len(VAS) + len(TRANSFERS) + len(BANK_IN_SENTENCE)
                 + len(ACCOUNT_ANSWERS))
        self.assertGreaterEqual(total, 50)


class MaskingScenarios(TestCase):
    def test_numbers_are_masked_and_amounts_kept(self):
        for text, numbers, kept in MASKING:
            with self.subTest(text=text):
                masked, mapping = ai.sanitize_for_model(text)
                self.assertEqual(sorted(mapping.values()), sorted(numbers))
                self.assertIn(kept, masked)
                for number in numbers:
                    self.assertNotIn(number, masked)


class VasScenarios(TestCase):
    def test_airtime_and_data(self):
        for text, name, amount, phone in VAS:
            with self.subTest(text=text):
                got = router.vas_text_intent(text)
                self.assertIsNotNone(got)
                self.assertEqual(got["name"], name)
                if amount is None:
                    self.assertNotIn("amount", got["input"])
                else:
                    self.assertEqual(Decimal(got["input"]["amount"]), Decimal(amount))
                self.assertEqual(got["input"].get("phone"), phone)


class TransferScenarios(TestCase):
    def setUp(self):
        _banks()
        self.user, _ = make_user(phone="08010000081", email="scen@zitch.test")

    def test_one_line_transfers(self):
        for text, amount, account, last_resort in TRANSFERS:
            with self.subTest(text=text), \
                    patch.object(router, "_begin_bank_transfer", return_value=True) as begin:
                handled = router._start_transfer_from_paste(self.user, MSISDN, text,
                                                            last_resort=last_resort)
                self.assertTrue(handled)
                self.assertEqual(begin.call_args.args[2], Decimal(amount))
                self.assertEqual(begin.call_args.args[3], account)

    def test_the_bank_is_read_out_of_the_sentence(self):
        for text, slug in BANK_IN_SENTENCE:
            with self.subTest(text=text):
                res = router._bank_resolution(text, "5476951723")
                found = res.bank.code if res.bank is not None else [b.code for b in res.choices]
                self.assertEqual(found, slug)

    def test_answers_to_the_account_question(self):
        for text, account, slug in ACCOUNT_ANSWERS:
            with self.subTest(text=text):
                PendingAction.objects.filter(msisdn=MSISDN).delete()
                pa = router._new_flow(self.user, MSISDN, "transfer", "account",
                                      {"amount": "3100", "pin_attempts": 0})
                with patch.object(router, "_offer_bank") as offer, \
                        patch.object(router, "reply") as said:
                    router._advance_transfer(pa, self.user, MSISDN, text)
                pa = PendingAction.objects.filter(msisdn=MSISDN).order_by("-pk").first()
                self.assertEqual(pa.payload.get("account"), account)
                said_text = " ".join(str(c.args[1]) for c in said.call_args_list)
                self.assertNotIn("doesn't look like", said_text)
                if slug:
                    self.assertEqual(offer.call_args.args[3].bank.code, slug)
