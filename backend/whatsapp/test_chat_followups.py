"""The chat carries a request across messages, and reads the bank out of it.

From production, 14 November 2026:

1. "Transfer to access 1668720613 centrac enterprise" was answered "How much
   would you like to transfer to Centrac Enterprise?" with the whole menu under
   it. Nothing remembered the question, so the "1800" that answered it was read
   as a stray PIN: "That looks like a PIN or code, and nothing here was waiting
   for one."
2. "Transfer to access 1668720613 centrac enterprise 1800" was answered "Which
   bank?" with AAA Finance, ABU Microfinance Bank and Access Bank, alphabetically.
"""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from transfers.models import Bank
from whatsapp import ai, router
from whatsapp.models import PendingAction, SystemSetting, WaMessageLog, WhatsAppLink
from whatsapp.test_flows import MSISDN, _make_user

ACCOUNT = "1668720613"
BANKS = [
    ("access", "Access Bank", "044", True), ("gtb", "GTBank", "058", True),
    ("firstbank", "First Bank", "011", True), ("uba", "UBA", "033", True),
    ("palmpay", "PalmPay", "999991", True), ("wema", "Wema Bank", "035", True),
    ("nip090000", "AAA FINANCE", "090000", False),
    ("nip090197", "ABU Microfinance bank", "090197", False),
    ("nip090483", "ADA MFB", "090483", False),
]
NAME_ENQUIRY = {"success": True, "name": "CENTRAC ENTERPRISE", "mock": True}


def _out():
    return list(WaMessageLog.objects.filter(msisdn=MSISDN, direction=WaMessageLog.OUT)
                .order_by("created").values_list("text", flat=True))


class ChatTestCase(TestCase):
    def setUp(self):
        self.user = _make_user()
        for code, name, bank_code, popular in BANKS:
            Bank.objects.create(code=code, name=name, bank_code=bank_code, popular=popular,
                                active=True, color="#000")
        enquiry = patch.object(router, "payout_resolve_account", return_value=NAME_ENQUIRY)
        self.enquiry = enquiry.start()
        self.addCleanup(enquiry.stop)

    def say(self, text):
        before = len(_out())
        router.handle_inbound(MSISDN, text)
        return "\n".join(_out()[before:])


class ScreenshotTests(ChatTestCase):

    def test_the_amount_question_is_remembered_and_1800_is_the_amount(self):
        asked = self.say("Transfer to access 1668720613 centrac enterprise")
        self.assertIn(f"How much would you like to send to Access Bank {ACCOUNT}?", asked)
        self.assertNotIn(router.menu_text(), asked)

        answered = self.say("1800")
        self.assertNotIn("PIN or code", answered)
        self.assertIn("Confirm transfer", answered)
        self.assertIn("₦1,800.00", answered)
        self.assertIn("CENTRAC ENTERPRISE", answered)
        self.assertIn("Access Bank", answered)
        self.enquiry.assert_called_once_with(ACCOUNT, "044")

    def test_the_bank_in_the_sentence_is_the_bank(self):
        out = self.say("Transfer to access 1668720613 centrac enterprise 1800")
        self.assertNotIn("Which bank", out)
        self.assertNotIn("AAA FINANCE", out)
        self.assertIn("Confirm transfer", out)
        self.assertIn("₦1,800.00", out)
        self.enquiry.assert_called_once_with(ACCOUNT, "044")

    def test_short_bank_names_in_a_sentence(self):
        for word, code in (("gt", "058"), ("tg", "058"), ("first", "011"), ("uba", "033")):
            PendingAction.objects.all().delete()
            self.enquiry.reset_mock()
            acct = _nuban_for(code)
            self.say(f"send 2000 to {acct} {word}")
            self.enquiry.assert_called_once_with(acct, code)

    def test_no_bank_named_asks_with_the_banks_people_use_not_the_alphabet(self):
        out = self.say(f"send 5k to {ACCOUNT}")
        self.assertIn(f"Which bank is {ACCOUNT} with?", out)
        self.assertNotIn("AAA FINANCE", out)
        self.assertLess(out.index("Access Bank"), out.index("UBA"))
        self.enquiry.assert_not_called()

        self.say("access")          # the pick list takes a typed name as well
        self.enquiry.assert_called_once_with(ACCOUNT, "044")


class AmountQuestionTests(ChatTestCase):

    def ask(self):
        self.say("Transfer to access 1668720613 centrac enterprise")

    def test_an_amount_with_words_around_it(self):
        self.ask()
        self.assertIn("₦1,800.00", self.say("1800 naira"))

    def test_a_new_request_leaves_the_question(self):
        self.ask()
        out = self.say("I want to buy airtime instead")
        self.assertIn("leaving that", out.lower())
        self.assertFalse(PendingAction.objects.filter(state="amount").exists())

    def test_the_question_names_no_bank_it_could_not_read(self):
        out = self.say(f"send money to {ACCOUNT}")
        self.assertIn(f"How much would you like to send to {ACCOUNT}?", out)
        out = self.say("5k")
        self.assertIn(f"Which bank is {ACCOUNT} with?", out)

    def test_airtime_to_a_phone_number_is_not_a_transfer(self):
        Bank.objects.create(code="momo", name="MoMo PSB (MTN)", bank_code="120003", active=True)
        self.assertFalse(router._start_transfer_from_paste(
            self.user, MSISDN, "send 500 airtime to 08031234567 mtn"))

    def test_your_own_account_number_is_not_a_transfer(self):
        from whatsapp.tests import give_account

        give_account(self.user, "0100000123")
        self.assertFalse(router._start_transfer_from_paste(self.user, MSISDN, "0100000123 wema"))

    def test_the_model_transfer_without_an_amount_asks_for_it(self):
        with patch.object(router, "reply") as reply:
            router.dispatch_intent(self.user, MSISDN, {
                "name": "transfer",
                "input": {"amount": None, "account_number": ACCOUNT, "bank_name": "Access Bank"}})
        self.assertIn(f"How much would you like to send to Access Bank {ACCOUNT}?",
                      reply.call_args[0][1])


@patch("whatsapp.ai.llm_available", return_value=True)
class ModelQuestionTests(ChatTestCase):
    """A question from the model is asked on its own and its answer is read with it."""

    def setUp(self):
        super().setUp()
        WhatsAppLink.objects.filter(wa_msisdn=MSISDN).update(ai_enabled=True)
        SystemSetting.set("ai_enabled_global", "true")

    def clarify(self, reason):
        return {"name": "clarify", "input": {"reason": reason}, "masked_input": {"reason": reason}}

    def test_a_question_goes_out_without_the_menu_and_is_remembered(self, _avail):
        with patch("whatsapp.ai.extract_intent",
                   return_value=self.clarify("Who should I send it to, and how much?")):
            out = self.say("send money to my landlord")
        self.assertIn("Who should I send it to, and how much?", out)
        self.assertNotIn(router.menu_text(), out)
        pa = PendingAction.objects.get(msisdn=MSISDN)
        self.assertEqual((pa.action_type, pa.payload["text"]), ("clarify", "send money to my landlord"))

        # The answer completes the request without the model: it is a paste now.
        with patch("whatsapp.ai.extract_intent") as model:
            out = self.say(f"5k to {ACCOUNT} access")
        model.assert_not_called()
        self.assertIn("Confirm transfer", out)
        self.assertIn("₦5,000.00", out)

    def test_the_model_reads_the_answer_with_its_question(self, _avail):
        with patch("whatsapp.ai.extract_intent",
                   return_value=self.clarify("How much would you like to send to Tunde?")):
            self.say("send money to Tunde")
        with patch("whatsapp.ai.extract_intent",
                   return_value=self.clarify("Which account number should I use?")) as model:
            out = self.say("1800")
        self.assertNotIn("PIN or code", out)
        context = model.call_args.kwargs["context"]
        self.assertEqual(context["text"], "send money to Tunde")
        self.assertEqual(context["question"], "How much would you like to send to Tunde?")
        self.assertEqual(model.call_args.args[0], "1800")
        # Asked again, about the whole request so far.
        self.assertEqual(PendingAction.objects.get(msisdn=MSISDN).payload["text"],
                         "send money to Tunde 1800")

    def test_a_statement_still_shows_the_menu_and_remembers_nothing(self, _avail):
        with patch("whatsapp.ai.extract_intent",
                   return_value=self.clarify("I can only help with Zitch account actions.")):
            out = self.say("tell me a joke")
        self.assertIn(router.menu_text(), out)
        self.assertFalse(PendingAction.objects.filter(msisdn=MSISDN).exists())

    def test_a_keyword_after_a_question_is_its_own_request(self, _avail):
        with patch("whatsapp.ai.extract_intent",
                   return_value=self.clarify("How much would you like to send?")):
            self.say("send money to my landlord")
        with patch("whatsapp.ai.extract_intent") as model:
            self.say("transfer")
        model.assert_not_called()
        self.assertFalse(PendingAction.objects.filter(action_type="clarify").exists())

    def test_a_pin_after_a_question_that_is_not_about_money_is_still_warned(self, _avail):
        with patch("whatsapp.ai.extract_intent",
                   return_value=self.clarify("Which bank is the account with?")):
            self.say("send money to my landlord")
        self.assertIn("PIN or code", self.say("4821"))

    def test_an_unanswered_question_expires_without_a_timeout_notice(self, _avail):
        PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="clarify", state="answer",
            payload={"text": "send money", "question": "How much?", "rounds": 1},
            expires_at=timezone.now() - timedelta(minutes=1))
        with patch("whatsapp.ai.extract_intent", return_value=None):
            out = self.say("hello there")
        self.assertNotIn("timed out", out)
        self.assertFalse(PendingAction.objects.filter(action_type="clarify").exists())

    def test_follow_ups_stop_after_three(self, _avail):
        with patch("whatsapp.ai.extract_intent", return_value=self.clarify("And then?")):
            for word in ("pay my landlord", "the usual one", "soon", "please"):
                out = self.say(word)
        self.assertIn(router.menu_text(), out)
        self.assertFalse(PendingAction.objects.filter(action_type="clarify").exists())


class ModelInputTests(TestCase):

    def test_a_reply_reaches_the_model_with_its_question(self):
        text = ai.conversation_text("1800", {"text": "Transfer to access 1668720613",
                                             "question": "How much would you like to send?"})
        masked, mapping = ai.sanitize_for_model(text)
        self.assertNotIn("1668720613", masked)
        self.assertIn("1668720613", mapping.values())
        self.assertIn("You asked them: How much would you like to send?", masked)
        self.assertTrue(masked.endswith("Their reply: 1800"))

    def test_a_transfer_no_longer_needs_an_amount_and_knows_the_short_names(self):
        tool = next(t for t in ai.TOOLS if t["name"] == "transfer")
        self.assertNotIn("amount", tool["input_schema"].get("required", []))
        for phrase in ('"tg"', '"first"', '"access"', "centrac enterprise"):
            self.assertIn(phrase, ai.SYSTEM_PROMPT.lower())


def _nuban_for(bank_code, serial="221794052"):
    seq = bank_code + serial
    total = sum(int(c) * (3, 7, 3)[i % 3] for i, c in enumerate(seq))
    return serial + str((10 - total % 10) % 10)
