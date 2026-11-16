"""The WhatsApp statement PDF is the app's statement: same identity block.

Both come out of whatsapp.receipt.render_statement_pdf. The app always passed
whose account it is, its number and the period; the WhatsApp call passed none,
so the file a customer forwarded from the chat could not say whose statement it
was, which is the one thing a landlord or an embassy reads it for.
"""
import re
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from wallet.models import Transaction
from wallet.tests import make_transaction_at
from whatsapp import receipt
from whatsapp.router import _do_history
from whatsapp.tests import MSISDN, give_account, make_user


def _undated(pdf: bytes) -> bytes:
    """The PDF without the creation/modification stamps Pillow writes, which
    differ between two renders a second apart."""
    return re.sub(rb"/(CreationDate|ModDate) \(D:[^)]*\)", b"", pdf)


class WhatsAppStatementIdentityTests(TestCase):
    def setUp(self):
        self.user, _ = make_user(balance="0")
        self.user.first_name, self.user.last_name = "Adaeze", "Okafor"
        self.user.save(update_fields=["first_name", "last_name"])
        give_account(self.user, "0123456789")

    def _txn(self, days):
        return make_transaction_at(self.user, created=timezone.now() - timedelta(days=days),
                                   amount="1000", service="Airtime — MTN",
                                   status=Transaction.SUCCESS)

    def _send_statement(self):
        """Run the statement path; return (render kwargs, uploaded bytes, filename)."""
        real = receipt.render_statement_pdf
        with patch("whatsapp.receipt.render_statement_pdf", wraps=real) as render, \
             patch("whatsapp.providers.wa_live", return_value=True), \
             patch("whatsapp.providers.upload_media", return_value="mid") as upload, \
             patch("whatsapp.providers.send_document", return_value={"success": True}) as doc:
            _do_history(self.user, MSISDN, as_document=True)
        render.assert_called_once()
        doc.assert_called_once()
        pdf, mime, filename = upload.call_args.args
        self.assertEqual(mime, "application/pdf")
        self.assertEqual(doc.call_args.args[2], filename)
        return render.call_args, pdf, filename

    def _period(self, days_back):
        today = timezone.localdate()
        return (f"{today - timedelta(days=days_back):%Y-%m-%d}", f"{today:%Y-%m-%d}")

    def test_the_pdf_says_whose_account_and_which_period(self):
        self._txn(1)
        call, pdf, filename = self._send_statement()
        start, end = self._period(30)
        self.assertEqual(call.kwargs["holder"], "Adaeze Okafor")
        self.assertEqual(call.kwargs["account"], "0123456789")
        self.assertEqual(call.kwargs["period"], f"{start} to {end}")
        # Named as the app names its statement.
        self.assertEqual(filename, f"Zitch-Statement-{start}_{end}.pdf")
        # And the file sent is exactly the one drawn with that identity block:
        # the same sheet the app renders for the same inputs.
        expected = receipt.render_statement_pdf(call.args[0], **call.kwargs)
        self.assertEqual(_undated(pdf), _undated(expected))
        bare = receipt.render_statement_pdf(
            call.args[0], balance=call.kwargs["balance"], generated=call.kwargs["generated"])
        self.assertNotEqual(_undated(pdf), _undated(bare))

    def test_the_period_never_excludes_a_row_it_lists(self):
        self._txn(1)
        self._txn(45)
        call, _pdf, filename = self._send_statement()
        start, end = self._period(45)
        self.assertEqual(call.kwargs["period"], f"{start} to {end}")
        self.assertEqual(filename, f"Zitch-Statement-{start}_{end}.pdf")

    def test_an_unnamed_account_falls_back_to_the_email_as_the_app_does(self):
        self.user.first_name = self.user.last_name = ""
        self.user.save(update_fields=["first_name", "last_name"])
        self._txn(1)
        call, _pdf, _filename = self._send_statement()
        self.assertEqual(call.kwargs["holder"], self.user.email)
