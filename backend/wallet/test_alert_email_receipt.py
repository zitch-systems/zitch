"""The alert email is laid out as the receipt.

The WhatsApp receipt and the app's were redesigned together: a teal header
naming the document, the amount and its outcome on a white ticket, one entry
per party, then a row for everything else and the WhatsApp banking panel. The
email alert kept its older card, so the same payment looked like two products
depending on where the customer read about it.
"""
from decimal import Decimal

from django.test import TestCase, override_settings

from accounts.models import User
from wallet.alerts import _email_alert_html
from wallet.models import Transaction
from wallet.services import get_or_create_wallet


class AlertEmailReceiptLayoutTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="08030000001", phone="08030000001",
                                        first_name="Ada", email="ada@example.test")
        get_or_create_wallet(self.user)

    def movement(self, **fields):
        values = dict(user=self.user, service="Bank transfer", amount=Decimal("1800.00"),
                      direction=Transaction.OUT, transaction_status=Transaction.SUCCESS,
                      reference="ZTCH-EMAIL-1", meta={})
        values.update(fields)
        return Transaction.objects.create(**values)

    def test_a_transfer_names_the_recipient_as_one_entry_with_a_masked_account(self):
        html = _email_alert_html(self.movement(meta={
            "recipient_name": "CENTRAC ENTERPRISE", "bank": "Access Bank",
            "account": "0061230613", "transfer_fee": "10.75", "note": "POS"}))
        self.assertIn("Debit alert", html)
        self.assertIn("Successful", html)
        self.assertIn("Recipient", html)
        self.assertIn("CENTRAC ENTERPRISE", html)
        self.assertIn("Access Bank · 0061****13", html)
        # An email is read in more places than the chat the transfer was made in.
        self.assertNotIn("0061230613", html)
        self.assertIn("Bank fee", html)
        self.assertIn("₦10.75", html)
        self.assertIn("Total debited", html)
        self.assertIn("₦1,810.75", html)
        self.assertIn("Narration", html)
        self.assertIn("POS", html)
        self.assertIn("ZTCH-EMAIL-1", html)

    def test_the_ticket_carries_the_receipts_tear_line_and_no_greeting_row(self):
        html = _email_alert_html(self.movement(meta={"recipient_name": "CENTRAC ENTERPRISE"}))
        self.assertIn("dashed", html)
        self.assertNotIn("here are the details", html)

    def test_a_deposit_names_who_paid(self):
        html = _email_alert_html(self.movement(
            direction=Transaction.IN, service="Bank deposit", amount=Decimal("21000.00"),
            meta={"statement": {"title": "NIP:ADETAYO OGUNTIMEHIN-Transfer from OPay",
                                "narration": ""}}))
        self.assertIn("Credit alert", html)
        self.assertIn("+₦21,000.00", html)
        self.assertIn("Sender", html)
        self.assertIn("ADETAYO OGUNTIMEHIN", html)
        self.assertIn("Bank transfer", html)
        self.assertNotIn("Recipient", html)

    def test_a_deposit_nobody_is_named_on_is_described_by_its_service(self):
        html = _email_alert_html(self.movement(direction=Transaction.IN, service="Bank deposit"))
        self.assertIn("Description", html)
        self.assertIn("Bank deposit", html)
        self.assertNotIn("Sender", html)

    def test_a_purchase_is_described_by_its_service(self):
        html = _email_alert_html(self.movement(service="Airtime — MTN", amount=Decimal("70.00")))
        self.assertIn("Description", html)
        self.assertIn("Airtime — MTN", html)
        self.assertNotIn("Recipient", html)

    def test_a_reversal_says_reversed_not_successful(self):
        html = _email_alert_html(self.movement(meta={"recipient_name": "CENTRAC ENTERPRISE"}),
                                 reversal=True)
        self.assertIn("Reversal alert", html)
        self.assertIn("Reversed", html)
        self.assertNotIn("Successful", html)
        self.assertIn("+₦1,800.00", html)

    def test_an_unreadable_fee_does_not_cost_the_customer_their_alert(self):
        html = _email_alert_html(self.movement(meta={
            "recipient_name": "CENTRAC ENTERPRISE", "transfer_fee": "ten naira"}))
        self.assertIn("CENTRAC ENTERPRISE", html)
        self.assertNotIn("Bank fee", html)

    @override_settings(WHATSAPP={"BUSINESS_NUMBER": "2348012345678"})
    def test_the_whatsapp_panel_links_to_the_banking_number(self):
        html = _email_alert_html(self.movement())
        self.assertIn("Bank wherever you chat", html)
        self.assertIn("https://wa.me/2348012345678?text=Hi", html)
        self.assertIn("+234 801 234 5678", html)

    @override_settings(WHATSAPP={"BUSINESS_NUMBER": ""})
    def test_the_whatsapp_panel_prints_no_number_when_none_is_configured(self):
        html = _email_alert_html(self.movement())
        self.assertIn("Bank wherever you chat", html)
        self.assertNotIn("wa.me", html)
