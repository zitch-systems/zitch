"""A deposit's receipt names the account it reached and, where the bank says so,
who paid."""
from decimal import Decimal

from django.test import TestCase

from wallet.alerts import credit_receipt_rows, deposit_sender
from wallet.models import Wallet
from wallet.services import apply_wema_credit, credit, statement_note
from wallet.tests import make_user


def _row(ref, amount, **extra):
    return {"referenceId": ref, "tranId": ref, "rrn": ref, "amount": str(amount),
            "balance": "1", "creditType": "Credit", "status": "Successful",
            "date": "2026-11-12T00:00:00", **extra}


class StatementNoteTests(TestCase):
    def test_keeps_the_banks_words_trimmed_and_bounded(self):
        note = statement_note({"title": "  Transfer   from  BOLA JOHNSON ",
                               "narration": "x" * 300, "type": "NIP", "amount": "100"})
        self.assertEqual(note["title"], "Transfer from BOLA JOHNSON")
        self.assertEqual(len(note["narration"]), 140)
        self.assertEqual(note["type"], "NIP")
        self.assertNotIn("amount", note)
        self.assertEqual(statement_note({}), {})

    def test_a_credited_deposit_keeps_what_the_bank_said(self):
        user, _ = make_user("08011119001", "note@example.test", balance="0")
        Wallet.objects.filter(user=user).update(account_number="0123456789")
        wallet = Wallet.objects.get(user=user)
        txn = apply_wema_credit(wallet, _row("NOTE1", 3900, title="Transfer from BOLA JOHNSON",
                                             narration="for rent", type="NIP"))
        self.assertIsNotNone(txn)
        txn.refresh_from_db()
        self.assertEqual(txn.meta["statement"], {
            "title": "Transfer from BOLA JOHNSON", "narration": "for rent", "type": "NIP"})
        self.assertEqual(txn.amount, Decimal("3900"))


class DepositSenderTests(TestCase):
    def test_a_zitch_sender_is_named_from_the_ledger(self):
        self.assertEqual(deposit_sender({"counterparty": "Ada Eze", "narration": "lunch"}),
                         ("ADA EZE", "Zitch", "lunch"))

    def test_the_banks_heading_names_the_payer(self):
        meta = {"statement": {"title": "Transfer from Bola Johnson", "narration": "rent",
                              "type": "NIP"}}
        self.assertEqual(deposit_sender(meta), ("BOLA JOHNSON", "Bank transfer", "rent"))
        meta = {"statement": {"title": "Transfer from BOLA JOHNSON to ADA EZE"}}
        self.assertEqual(deposit_sender(meta)[0], "BOLA JOHNSON")

    def test_the_payers_own_words_name_nobody(self):
        """The narration is free text the payer typed: "transfer from friend"
        is shown as written, never turned into a sender called FRIEND."""
        meta = {"statement": {"title": "Inward transfer", "narration": "transfer from friend"}}
        self.assertEqual(deposit_sender(meta), ("", "", "transfer from friend"))

    def test_digits_are_never_a_name(self):
        self.assertEqual(deposit_sender({"statement": {"title": "Transfer from 0123456789"}})[0], "")
        self.assertEqual(deposit_sender({"statement": {"sender": "0123456789"}})[0], "")

    def test_nothing_recorded_names_nobody(self):
        self.assertEqual(deposit_sender({}), ("", "", ""))
        self.assertEqual(deposit_sender("not a dict"), ("", "", ""))


class CreditReceiptRowsTests(TestCase):
    def setUp(self):
        self.user, _ = make_user("08011119002", "rows@example.test", balance="0")
        Wallet.objects.filter(user=self.user).update(
            account_number="0123456789", bank_name="Wema Bank", account_name="Ada Eze")

    def _credit(self, ref, meta):
        return credit(self.user, Decimal("3900"), "Wallet funding", meta=meta, reference=ref)

    def test_names_the_account_reached_and_who_paid(self):
        txn = self._credit("CR-1", {"statement": {
            "title": "Transfer from BOLA JOHNSON", "narration": "for rent", "type": "NIP"}})
        rows = credit_receipt_rows(txn)
        self.assertEqual([k for k, _ in rows], [
            "Amount", "To", "Bank", "Account", "From", "Sender", "Narration", "Reference", "Date"])
        values = dict(rows)
        self.assertEqual(values["To"], "ADA EZE")
        self.assertEqual(values["Account"], "0123****89")
        self.assertEqual(values["From"], "BOLA JOHNSON")
        self.assertEqual(values["Sender"], "Bank transfer")
        self.assertEqual(values["Narration"], "for rent")
        # The customer's own number is masked: the receipt gets forwarded.
        self.assertNotIn("0123456789", " ".join(v for _, v in rows))

    def test_a_heading_that_only_repeats_the_payer_is_not_printed_twice(self):
        txn = self._credit("CR-2", {"statement": {"title": "Transfer from BOLA JOHNSON"}})
        values = dict(credit_receipt_rows(txn))
        self.assertEqual(values["From"], "BOLA JOHNSON")
        self.assertNotIn("Narration", values)

    def test_a_deposit_the_bank_did_not_describe_still_has_its_facts(self):
        txn = self._credit("CR-3", {"reference": "CR-3"})
        self.assertEqual([k for k, _ in credit_receipt_rows(txn)],
                         ["Amount", "To", "Bank", "Account", "Reference", "Date"])
