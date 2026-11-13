"""On WhatsApp the bank's transfer fee is shown before the PIN and charged as shown."""
import json
from decimal import Decimal

from django.test import Client, TestCase

from transfers.models import Bank
from wallet.models import Transaction
from wallet.services import get_or_create_wallet
from wallet.transfer_fees import FEE_FOR_KEY

from .models import WaMessageLog, WhatsAppLink
from .tests import MSISDN, make_user


class WhatsAppTransferFeeTests(TestCase):
    def setUp(self):
        self.client = Client()
        Bank.objects.create(code="gtb", name="GTBank", bank_code="058", color="#e30613", active=True)

    def _customer(self, balance):
        self.user, _ = make_user(balance=balance)
        WhatsAppLink.objects.create(user=self.user, wa_msisdn=MSISDN, status=WhatsAppLink.ACTIVE)

    def inbound(self, text, mid):
        event = {"entry": [{"changes": [{"value": {"messages": [
            {"from": MSISDN, "id": mid, "type": "text", "text": {"body": text}}]}}]}]}
        self.client.post("/webhooks/whatsapp", data=json.dumps(event), content_type="application/json")

    def replies(self):
        return list(WaMessageLog.objects.filter(msisdn=MSISDN, direction=WaMessageLog.OUT)
                    .order_by("created").values_list("text", flat=True))

    def _start_transfer(self):
        for mid, text in (("f1", "2"), ("f2", "5000"), ("f3", "0123456789"), ("f4", "GTBank")):
            self.inbound(text, mid)

    def test_the_confirm_card_shows_the_fee_and_the_receipt_names_it(self):
        self._customer("50000")
        self._start_transfer()
        confirm = self.replies()[-1]
        self.assertIn("Confirm transfer", confirm)
        self.assertIn("Bank fee ₦10.75 • Total ₦5,010.75", confirm)

        self.inbound("1234", "f5")

        receipt = next(text for text in reversed(self.replies()) if "receipt" in text.lower())
        self.assertIn("Bank fee", receipt)
        self.assertIn("₦10.75", receipt)
        self.assertEqual(get_or_create_wallet(self.user).balance, Decimal("44989.25"))
        self.assertTrue(Transaction.objects.filter(
            user=self.user, meta__has_key=FEE_FOR_KEY, transaction_status=Transaction.SUCCESS).exists())

    def test_a_balance_short_of_the_fee_is_refused_before_the_pin(self):
        self._customer("5005")
        self._start_transfer()

        last = self.replies()[-1]
        self.assertIn("this payment needs ₦5,010.75", last)
        self.assertNotIn("Confirm transfer", last)
        self.assertFalse(Transaction.objects.filter(user=self.user, direction=Transaction.OUT).exists())
