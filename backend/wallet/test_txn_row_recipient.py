"""The history row carries who a bank transfer went to, for the receipt."""
from decimal import Decimal

from django.test import TestCase

from wallet.models import Transaction
from wallet.tests import make_user
from wallet.views import _txn_row


class TxnRowRecipientTests(TestCase):
    def setUp(self):
        self.user, _token = make_user("08071260001", "rowrecipient@zitch.test")

    def row(self, meta):
        txn = Transaction.objects.create(
            user=self.user, service="Transfer to CHIDI EZE", amount=Decimal("15000.00"),
            direction=Transaction.OUT, transaction_status=Transaction.SUCCESS,
            reference="ZTCHROW1", meta=meta)
        return _txn_row(txn, review_kind="")

    def test_a_bank_transfer_names_its_recipient_account_bank_and_fee(self):
        row = self.row({"recipient_name": "CHIDI EZE", "account": "0123456789",
                        "bank": "Access Bank", "transfer_fee": "10.75"})
        self.assertEqual((row["recipient_name"], row["recipient_account"], row["recipient_bank"], row["fee"]),
                         ("CHIDI EZE", "0123456789", "Access Bank", "10.75"))

    def test_anything_else_leaves_them_blank(self):
        row = self.row({"narration": "MTN airtime"})
        self.assertEqual((row["recipient_name"], row["recipient_account"], row["recipient_bank"], row["fee"]),
                         ("", "", "", ""))
