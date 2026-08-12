"""Credit and debit alerts.

Wired to the ledger row rather than to each money path, so the properties worth
pinning are about the wiring: fires on settlement not on intent, exactly once,
never for a movement the database rolled back, and never fatal.
"""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from .models import Transaction
from .services import credit, debit, get_or_create_wallet

User = get_user_model()

_ON = {"EMAIL": True, "SMS": True}


def _user():
    u = User.objects.create(username="08012340000", phone="08012340000",
                            email="ada@zitch.test", first_name="Ada", tier=1,
                            email_verified=True, phone_verified=True,
                            bvn_verified=True, nin_verified=True)
    get_or_create_wallet(u)
    return u


@override_settings(TXN_ALERTS=_ON)
class TransactionAlertTests(TestCase):
    def setUp(self):
        self.user = _user()

    def test_a_credit_alerts_on_both_channels(self):
        with patch("utility.providers.send_email") as email, \
             patch("utility.providers.send_sms") as sms:
            with self.captureOnCommitCallbacks(execute=True):
                credit(self.user, Decimal("5000"), "funding")
        email.assert_called_once()
        sms.assert_called_once()
        self.assertIn("Credit", email.call_args[0][1])
        self.assertIn("5,000.00", email.call_args[0][1])

    def test_a_pending_debit_does_not_alert_until_it_settles(self):
        """debit() writes PENDING and the caller flips it later. Alerting at debit
        time would announce spends that then fail and reverse."""
        credit(self.user, Decimal("10000"), "funding")
        with patch("utility.providers.send_email") as email:
            with self.captureOnCommitCallbacks(execute=True):
                txn = debit(self.user, Decimal("1000"), "transfer")
        email.assert_not_called()

        with patch("utility.providers.send_email") as email:
            with self.captureOnCommitCallbacks(execute=True):
                txn.transaction_status = Transaction.SUCCESS
                txn.save(update_fields=["transaction_status"])
        email.assert_called_once()
        self.assertIn("Debit", email.call_args[0][1])

    def test_a_failed_debit_never_alerts(self):
        credit(self.user, Decimal("10000"), "funding")
        txn = debit(self.user, Decimal("1000"), "transfer")
        with patch("utility.providers.send_email") as email:
            with self.captureOnCommitCallbacks(execute=True):
                txn.transaction_status = Transaction.FAILED
                txn.save(update_fields=["transaction_status"])
        email.assert_not_called()

    def test_the_same_row_alerts_once_however_often_it_is_saved(self):
        """The status flip is itself a save, and settlement and reconciliation
        touch the row again afterwards."""
        with patch("utility.providers.send_email") as email:
            with self.captureOnCommitCallbacks(execute=True):
                txn = credit(self.user, Decimal("5000"), "funding")
            with self.captureOnCommitCallbacks(execute=True):
                txn.refresh_from_db()
                txn.save()
                txn.save()
        email.assert_called_once()

    def test_house_keeping_rows_are_silent(self):
        """A reversal or settlement is not something the customer did, and
        alerting on it reads as a second, unexplained movement."""
        with patch("utility.providers.send_email") as email:
            with self.captureOnCommitCallbacks(execute=True):
                credit(self.user, Decimal("5000"), "reversal:transfer")
        email.assert_not_called()

    def test_a_provider_outage_does_not_break_the_payment(self):
        with patch("utility.providers.send_email", side_effect=RuntimeError("mail down")), \
             patch("utility.providers.send_sms", side_effect=RuntimeError("sms down")):
            with self.captureOnCommitCallbacks(execute=True):
                txn = credit(self.user, Decimal("5000"), "funding")   # must not raise
        self.assertEqual(get_or_create_wallet(self.user).balance, Decimal("5000"))
        self.assertEqual(txn.transaction_status, Transaction.SUCCESS)

    def test_the_alert_does_not_carry_the_full_phone_or_account(self):
        """It is a lock-screen notice; the detail belongs on the receipt."""
        with patch("utility.providers.send_email") as email:
            with self.captureOnCommitCallbacks(execute=True):
                credit(self.user, Decimal("5000"), "funding")
        body = email.call_args[0][2]
        self.assertNotIn(self.user.phone, body)

    @override_settings(TXN_ALERTS={"EMAIL": True, "SMS": False})
    def test_sms_is_off_unless_switched_on(self):
        """One SMS per transaction is a real recurring cost at Nigerian rates, so
        it is a deliberate choice rather than a default."""
        with patch("utility.providers.send_email") as email, \
             patch("utility.providers.send_sms") as sms:
            with self.captureOnCommitCallbacks(execute=True):
                credit(self.user, Decimal("5000"), "funding")
        email.assert_called_once()
        sms.assert_not_called()

    def test_a_settled_debit_that_later_reverses_says_so(self):
        """A provider-confirmed reversal flips the settled row to Failed rather
        than writing a new one, so the customer must be told money came back.

        The generic ``refund`` deliberately refuses settled rows: a stale
        request failure must not undo a callback-confirmed payout.  A late bank
        reversal goes through the reference-bound ``reverse_transfer`` path.
        """
        from .services import reverse_transfer

        credit(self.user, Decimal("10000"), "funding")
        with self.captureOnCommitCallbacks(execute=True):
            txn = debit(self.user, Decimal("1000"), "transfer")
            txn.transaction_status = Transaction.SUCCESS
            txn.save(update_fields=["transaction_status"])

        with patch("utility.providers.send_email") as email:
            with self.captureOnCommitCallbacks(execute=True):
                reverse_transfer(txn.reference)
        email.assert_called_once()
        self.assertIn("Reversal", email.call_args[0][1])
        self.assertIn("returned to your Zitch account", email.call_args[0][2])

    def test_a_debit_that_fails_without_ever_settling_stays_silent(self):
        """Nothing was announced, so there is nothing to walk back."""
        from .services import refund

        credit(self.user, Decimal("10000"), "funding")
        txn = debit(self.user, Decimal("1000"), "transfer")
        with patch("utility.providers.send_email") as email:
            with self.captureOnCommitCallbacks(execute=True):
                refund(txn)
        email.assert_not_called()


@override_settings(TXN_ALERTS={"EMAIL": True, "SMS": False, "WHATSAPP": True})
class WhatsAppChannelAlertTests(TestCase):
    """A WhatsApp-originated transfer/purchase already gets a receipt and a
    "balance is now" line inside the same chat (whatsapp/router.py
    `reply_receipt`). The generic post-save alert must not repeat that as a
    second "Debit alert" message in the same thread — but it must still reach
    every other channel, and it must still fire on a later reversal, since
    nothing else in that chat ever announces one."""

    def setUp(self):
        from whatsapp.models import WhatsAppLink

        self.user = _user()
        self.link = WhatsAppLink.objects.create(
            user=self.user, wa_msisdn="2348012340000", status=WhatsAppLink.ACTIVE)

    def test_a_whatsapp_channel_debit_does_not_duplicate_in_chat(self):
        credit(self.user, Decimal("10000"), "funding")
        with patch("utility.providers.send_email") as email, \
             patch("whatsapp.router.reply") as wa_reply:
            with self.captureOnCommitCallbacks(execute=True):
                txn = debit(self.user, Decimal("1000"), "Transfer to Ada",
                           meta={"channel": "whatsapp"})
                txn.transaction_status = Transaction.SUCCESS
                txn.save(update_fields=["transaction_status"])
        email.assert_called_once()
        wa_reply.assert_not_called()

    def test_a_non_whatsapp_channel_debit_still_alerts_in_chat(self):
        """A transfer made in the app still reaches a linked WhatsApp customer —
        that chat never saw a receipt for it."""
        credit(self.user, Decimal("10000"), "funding")
        with patch("utility.providers.send_email") as email, \
             patch("whatsapp.router.reply") as wa_reply:
            with self.captureOnCommitCallbacks(execute=True):
                txn = debit(self.user, Decimal("1000"), "Transfer to Ada")
                txn.transaction_status = Transaction.SUCCESS
                txn.save(update_fields=["transaction_status"])
        email.assert_called_once()
        wa_reply.assert_called_once()
        self.assertIn("Debit alert", wa_reply.call_args[0][1])

    def test_a_reversed_whatsapp_channel_debit_still_alerts_in_chat(self):
        """The reversal happens later, out of band — the original chat never
        says the money came back unless this does."""
        from .services import reverse_transfer

        credit(self.user, Decimal("10000"), "funding")
        with self.captureOnCommitCallbacks(execute=True):
            txn = debit(self.user, Decimal("1000"), "transfer", meta={"channel": "whatsapp"})
            txn.transaction_status = Transaction.SUCCESS
            txn.save(update_fields=["transaction_status"])

        with patch("utility.providers.send_email") as email, \
             patch("whatsapp.router.reply") as wa_reply:
            with self.captureOnCommitCallbacks(execute=True):
                reverse_transfer(txn.reference)
        email.assert_called_once()
        wa_reply.assert_called_once()
        self.assertIn("Reversal", wa_reply.call_args[0][1])
