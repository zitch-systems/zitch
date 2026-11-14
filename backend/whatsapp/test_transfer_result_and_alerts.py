"""What the customer sees after a transfer, a deposit and "my account details".

- a transfer the bank answers "processing" waits a few seconds for its outcome,
  so the chat gets the receipt, not "still processing" then a second message;
- a transfer that settles later still gets its receipt image;
- an alert says each thing once;
- account details are one message;
- the statement request that finds deposits sends no empty keyWord.
"""
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings

from transfers.services import await_payout_settlement
from wallet import alerts
from wallet.models import Transaction
from wallet.services import get_or_create_wallet

from . import router
from .models import WaMessageLog
from .test_flows import MSISDN, _make_user


def _out():
    return list(WaMessageLog.objects.filter(msisdn=MSISDN, direction=WaMessageLog.OUT)
                .order_by("created").values_list("text", flat=True))


def _payout(user, status=Transaction.PENDING, **meta):
    return Transaction.objects.create(
        user=user, amount=Decimal("53.00"), direction=Transaction.OUT,
        service="Transfer to ADETAYO OGUNTIMEHIN", reference="ZTCH7B5C7D16AAA9",
        transaction_status=status, idempotency_key="wa-1",
        meta={"channel": "whatsapp", "recipient_name": "ADETAYO OGUNTIMEHIN",
              "bank": "Wema/ALAT", "account": "0228123472",
              "note": "Transfer to ADETAYO OGUNTIMEHIN", **meta})


class SettleWaitTests(TestCase):
    def setUp(self):
        self.user = _make_user()

    def test_a_transfer_the_bank_confirms_is_settled_before_it_is_reported(self):
        txn = _payout(self.user)
        with patch("transfers.services.wema_provider.confirm_transfer_status",
                   return_value={"success": True, "status": "Successful"}) as lookup, \
                patch("time.sleep"):
            await_payout_settlement(txn, 4)
        txn.refresh_from_db()
        self.assertEqual(txn.transaction_status, Transaction.SUCCESS)
        self.assertTrue(lookup.called)

    def test_an_unclear_answer_leaves_the_transfer_pending(self):
        txn = _payout(self.user)
        with patch("transfers.services.wema_provider.confirm_transfer_status",
                   return_value={"success": False, "pending": True, "status": "PENDING"}), \
                patch("time.sleep"):
            await_payout_settlement(txn, 2)
        txn.refresh_from_db()
        self.assertEqual(txn.transaction_status, Transaction.PENDING)

    def test_a_declined_status_is_not_acted_on_here(self):
        # A failure is for the callback and the reconcile job, which refund with
        # their own evidence checks. The wait only ever confirms a success.
        txn = _payout(self.user)
        before = get_or_create_wallet(self.user).balance
        with patch("transfers.services.wema_provider.confirm_transfer_status",
                   return_value={"success": False, "status": "Failed"}), \
                patch("time.sleep"):
            await_payout_settlement(txn, 2)
        txn.refresh_from_db()
        self.assertEqual(txn.transaction_status, Transaction.PENDING)
        self.assertEqual(get_or_create_wallet(self.user).balance, before)

    def test_no_budget_means_no_bank_call(self):
        txn = _payout(self.user)
        with patch("transfers.services.wema_provider.confirm_transfer_status") as lookup:
            await_payout_settlement(txn, 0)
        lookup.assert_not_called()


@override_settings(TXN_ALERTS={"EMAIL": False, "SMS": False, "WHATSAPP": True})
class AlertTextTests(TestCase):
    def setUp(self):
        self.user = _make_user()

    def _caption(self, txn):
        subject, body = alerts._describe(txn)
        return alerts._whatsapp_caption(txn, "💸", subject, body, reversal=False)

    def test_a_settled_transfer_says_each_thing_once(self):
        txn = _payout(self.user, status=Transaction.SUCCESS, wa_awaiting_settlement=True)
        caption = self._caption(txn)
        self.assertEqual(caption.count("Transfer successful"), 1)
        self.assertEqual(caption.count("ADETAYO OGUNTIMEHIN"), 1)
        self.assertIn("To: ADETAYO OGUNTIMEHIN", caption)
        self.assertNotIn("For:", caption)          # the default narration repeats "To:"
        self.assertIn("Ref: ZTCH7B5C7D16AAA9", caption)
        self.assertIn("Available balance", caption)

    def test_a_note_the_customer_wrote_is_kept(self):
        txn = _payout(self.user, status=Transaction.SUCCESS, wa_awaiting_settlement=True,
                      note="November rent")
        self.assertIn("For: November rent", self._caption(txn))

    def test_a_deposit_names_who_paid_once(self):
        txn = Transaction.objects.create(
            user=self.user, amount=Decimal("500"), direction=Transaction.IN,
            service="Wallet funding", reference="WEMA-CR-1",
            transaction_status=Transaction.SUCCESS, meta={"counterparty": "JOHN DOE"})
        subject, body = alerts._describe(txn)
        caption = alerts._whatsapp_caption(txn, "💰", subject, body, reversal=False)
        self.assertEqual(caption.count("JOHN DOE"), 1)
        self.assertIn("From: JOHN DOE", caption)
        self.assertEqual(caption.count("₦500.00"), 1)

    def test_a_transfer_that_settles_later_gets_its_receipt_image(self):
        txn = _payout(self.user, status=Transaction.SUCCESS, wa_awaiting_settlement=True)
        with patch("whatsapp.providers.wa_live", return_value=True), \
                patch("whatsapp.providers.upload_media", return_value="media-1") as upload, \
                patch("whatsapp.providers.send_image_media",
                      return_value={"success": True}) as image:
            result = alerts._whatsapp_alert(txn, *alerts._describe(txn))
        self.assertTrue(result["success"])
        self.assertEqual(upload.call_args.args[1], "image/jpeg")
        self.assertIn("Transfer successful", image.call_args.kwargs["caption"])

    def test_the_receipt_rows_come_from_the_ledger(self):
        txn = _payout(self.user, status=Transaction.SUCCESS, transfer_fee="10.75")
        rows = dict(alerts.transfer_receipt_rows(txn))
        self.assertEqual(rows["To"], "ADETAYO OGUNTIMEHIN")
        self.assertEqual(rows["Bank"], "Wema/ALAT")
        self.assertEqual(rows["Bank fee"], "₦10.75")
        self.assertEqual(rows["Total debited"], "₦63.75")
        self.assertEqual(rows["Reference"], "ZTCH7B5C7D16AAA9")
        self.assertNotIn("Narration", rows)


class AccountDetailsTests(TestCase):
    def test_account_details_are_one_message_without_instructions(self):
        user = _make_user()
        funding = {"can_fund": True, "account_number": "0458386712",
                   "bank_name": "Wema Bank", "account_name": "ADA EZE",
                   "bank_accounts": [], "account_setup_state": "ready"}
        wallet = get_or_create_wallet(user)
        wallet.account_number = "0458386712"
        wallet.save(update_fields=["account_number"])
        with patch.object(router, "customer_funding_account", return_value=funding):
            router._do_account_details(user, MSISDN)
        sent = _out()
        self.assertEqual(len(sent), 1)
        self.assertIn("My account details", sent[0])
        self.assertIn("0458386712", sent[0])
        self.assertIn("Wema Bank", sent[0])
        self.assertIn(user.phone, sent[0])
        self.assertNotIn("Transfer to your dedicated", sent[0])
        self.assertNotIn("Your funding account", sent[0])


class StatementRequestTests(TestCase):
    @override_settings(DEBUG=False, WEMA_CUTOVER_APPROVED=True)
    def test_the_deposit_statement_request_sends_no_empty_keyword(self):
        from utility import wema

        class Answer:
            status_code = 200
            content = b"{}"

            def json(self):
                return {"result": [], "successful": True, "message": ""}

        with patch.object(wema, "_product_live", return_value=True), \
                patch.object(wema, "_post", return_value=Answer()) as post, \
                patch.object(wema, "_is_empty_history_response", return_value=False):
            wema.get_transactions("0458386712", "2026-11-13", "2026-11-14")
            wema.get_transactions("0458386712", "2026-11-13", "2026-11-14", "rent")
        bodies = [call.args[2] for call in post.call_args_list]
        # A plain read never carries the field, in either spelling or empty.
        self.assertNotIn("keyWord", bodies[0])
        self.assertTrue(all("keyword" not in body for body in bodies))
        self.assertTrue(all(body.get("keyWord", "rent") == "rent" for body in bodies))
        # A real search does.
        self.assertEqual(bodies[-1]["keyWord"], "rent")
