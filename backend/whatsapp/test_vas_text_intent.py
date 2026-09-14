"""The menu promises natural language. It has to work with the model switched off.

Every menu ends with 'Or just type what you want - "send 5k to Ada", "2k airtime"'.
That promise routed through ai_active(), which also requires the ``ai_enabled_global``
SystemSetting — read with a default of False. On a deploy where nobody had flipped it,
every one of those sentences answered "Sorry, I didn't get that", including the menu's
own worked example printed one line above the customer's attempt.

The reported transcript is exactly that: "Recharge me 55 naira airtime", then
"55naira airtime for me", both answered with the menu.
"""
from decimal import Decimal

from django.test import SimpleTestCase

from whatsapp.router import vas_text_intent


class ParsesWhatCustomersActuallyTypeTests(SimpleTestCase):

    def _amount(self, text):
        intent = vas_text_intent(text)
        self.assertIsNotNone(intent, f"{text!r} should have been understood")
        return intent["name"], Decimal(intent["input"]["amount"])

    def test_the_exact_sentences_from_the_report(self):
        for text in ("Recharge me 55 naira airtime", "55naira airtime for me"):
            with self.subTest(text=text):
                name, amount = self._amount(text)
                self.assertEqual(name, "buy_airtime")
                self.assertEqual(amount, Decimal("55"))

    def test_the_menus_own_worked_example(self):
        """If this one fails the menu is lying to every customer who reads it."""
        name, amount = self._amount("2k airtime")
        self.assertEqual(name, "buy_airtime")
        self.assertEqual(amount, Decimal("2000"))

    def test_the_shapes_customers_write_an_amount_in(self):
        for text, expected in (
            ("buy 500 airtime", "500"),
            ("₦1,500 airtime", "1500"),
            ("airtime 200", "200"),
            ("top up 1k", "1000"),
            ("topup 750", "750"),
            ("recharge N300", "300"),
        ):
            with self.subTest(text=text):
                _, amount = self._amount(text)
                self.assertEqual(amount, Decimal(expected))

    def test_a_stated_number_is_read_as_the_line_not_as_the_price(self):
        """The 11-digit line must never be priced. This is the whole reason the
        phone is matched and removed before any amount is looked for."""
        intent = vas_text_intent("recharge 08012345678 with 500")
        self.assertEqual(intent["input"]["phone"], "08012345678")
        self.assertEqual(Decimal(intent["input"]["amount"]), Decimal("500"))

    def test_data_is_routed_as_data(self):
        name, amount = self._amount("2k data")
        self.assertEqual(name, "buy_data")
        self.assertEqual(amount, Decimal("2000"))

    def test_a_bundle_size_is_not_a_price(self):
        """"1gb data for 500" prices at 500, never at 1."""
        _, amount = self._amount("1gb data for 500")
        self.assertEqual(amount, Decimal("500"))


class RefusesWhatItShouldNotGuessAtTests(SimpleTestCase):
    """A parser that opened a money flow off half a sentence would be a worse
    failure than the menu it replaces."""

    def test_a_product_word_with_no_amount_is_left_alone(self):
        for text in ("airtime", "recharge", "data", "I want airtime"):
            with self.subTest(text=text):
                self.assertIsNone(vas_text_intent(text))

    def test_an_amount_with_no_product_word_is_left_alone(self):
        """"send 5k to Ada" is a transfer and must stay one."""
        for text in ("send 5k to Ada", "5000", "pay 2k"):
            with self.subTest(text=text):
                self.assertIsNone(vas_text_intent(text))

    def test_a_question_about_a_top_up_is_not_a_purchase(self):
        for text in ("how much is 2k airtime?", "what is 500 airtime",
                     "can I buy 200 airtime?"):
            with self.subTest(text=text):
                self.assertIsNone(vas_text_intent(text))

    def test_an_amount_below_the_airtime_floor_is_not_a_top_up(self):
        self.assertIsNone(vas_text_intent("airtime 10"))

    def test_an_implausibly_large_amount_is_left_to_a_human(self):
        self.assertIsNone(vas_text_intent("airtime 5000000"))

    def test_prose_is_the_models_to_read_not_this(self):
        long_text = ("hello there I was wondering whether it would be possible for "
                     "me to get some airtime worth 500 naira today if that is okay "
                     "with you and the team thanks very much indeed")
        self.assertIsNone(vas_text_intent(long_text))

    def test_empty_input_is_safe(self):
        for text in ("", "   ", None):
            with self.subTest(text=text):
                self.assertIsNone(vas_text_intent(text))
