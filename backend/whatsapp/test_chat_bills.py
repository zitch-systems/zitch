"""Electricity named in the message is a bill, never airtime (16 Nov 2026).

"Recharge 45058723664 Ikeja electric 1050" was answered by the model with
"Did you mean airtime of N1,050 for 45058723664, or an Ikeja Electric bill?",
twice. 45058723664 has no Nigerian phone's shape (0... or 234...), and Ikeja
Electric is a biller; the message was complete.
"""
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from whatsapp import router
from whatsapp.models import SystemSetting, WhatsAppLink
from whatsapp.test_chat_followups import ChatTestCase
from whatsapp.test_flows import MSISDN

#: (message, disco menu id, meter, amount or None, meter type or None)
BILLS = [
    ("Recharge 45058723664 Ikeja electric 1050", "1", "45058723664", "1050", None),
    ("Ikeja electric 45058723664 1050", "1", "45058723664", "1050", None),
    ("pay IKEDC 45058723664 5k", "1", "45058723664", "5000", None),
    ("ikedc prepaid 45058723664 N2,000", "1", "45058723664", "2000", "prepaid"),
    ("buy light 45058723664 ikeja 3000", "1", "45058723664", "3000", None),
    ("Eko electric 0101234567 2000", "2", "0101234567", "2000", None),
    ("EKEDC postpaid meter 12345678901 10000", "2", "12345678901", "10000", "postpaid"),
    ("abuja electricity 04123456789 1500", "3", "04123456789", "1500", None),
    ("AEDC 04123456789", "3", "04123456789", None, None),
    ("ibadan disco 54123456789 #2500", "9", "54123456789", "2500", None),
    ("load my meter 45058723664 ikeja electric", "1", "45058723664", None, None),
    ("ikeja electric token 45058723664 for 1000", "1", "45058723664", "1000", None),
    ("Port Harcourt electric 12345678 4000", "5", "12345678", "4000", None),
    ("token for ikeja 45058723664 1050", "1", "45058723664", "1050", None),
]


class BillTextTests(TestCase):
    def test_named_disco_and_meter_make_a_bill(self):
        for text, disco, meter, amount, meter_type in BILLS:
            with self.subTest(text=text):
                got = router.bill_text_intent(text)
                self.assertIsNotNone(got)
                p = got["input"]
                self.assertEqual(got["name"], "pay_bill")
                self.assertEqual(router._disco_id(p["biller"]), disco)
                self.assertEqual(p["customer_id"], meter)
                self.assertEqual(Decimal(p["amount"]) if p["amount"] else None,
                                 Decimal(amount) if amount else None)
                self.assertEqual(p["variation"], meter_type)

    def test_a_named_disco_is_never_airtime(self):
        for text, *_ in BILLS:
            with self.subTest(text=text):
                self.assertIsNone(router.vas_text_intent(text))

    def test_airtime_and_addresses_are_left_alone(self):
        for text in ("recharge 08037217665 1050", "airtime 500",
                     "send 5000 to 0123456789 gtb", "I live in Ikeja, recharge 08037217665 200",
                     "how much is ikeja electric 45058723664?"):
            with self.subTest(text=text):
                self.assertIsNone(router.bill_text_intent(text))

    def test_two_meter_numbers_are_not_guessed_between(self):
        self.assertIsNone(router.bill_text_intent("ikedc 45058723664 or 45058723665 1000"))


@patch("whatsapp.ai.llm_available", return_value=True)
class BillBeforeModelTests(ChatTestCase):
    def setUp(self):
        super().setUp()
        WhatsAppLink.objects.filter(wa_msisdn=MSISDN).update(ai_enabled=True)
        SystemSetting.set("ai_enabled_global", "true")

    def test_the_screenshot_message_opens_the_ikeja_bill_without_the_model(self, _avail):
        confused = {"name": "clarify", "input": {
            "reason": "Did you mean airtime of N1,050 for 45058723664, or an Ikeja bill?"}}
        with patch("whatsapp.ai.extract_intent", return_value=confused) as model, \
                patch.object(router, "_begin_electricity") as begin:
            out = self.say("Recharge 45058723664 Ikeja electric 1050")
        model.assert_not_called()
        self.assertNotIn("airtime", out.lower())
        _user, _msisdn, biller, meter, _variation, amount = begin.call_args.args
        self.assertEqual(router._disco_id(biller), "1")
        self.assertEqual(meter, "45058723664")
        self.assertEqual(Decimal(amount), Decimal("1050"))
