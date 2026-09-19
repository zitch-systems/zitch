"""Credit and debit alerts.

Wired to the ledger row rather than to each money path, so the properties worth
pinning are about the wiring: fires on settlement not on intent, exactly once,
never for a movement the database rolled back, and never fatal.
"""
from datetime import datetime, timezone as dt_timezone
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Event
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase, override_settings

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

    def test_internal_reversal_evidence_never_sends_customer_alerts(self):
        with patch("utility.providers.send_email") as email, \
             patch("utility.providers.send_sms") as sms:
            with self.captureOnCommitCallbacks(execute=True):
                Transaction.objects.create(
                    user=self.user, amount=Decimal("500"), direction=Transaction.IN,
                    service="Payout reversal evidence", reference="REV-EVIDENCE-1",
                    transaction_status=Transaction.SUCCESS,
                    meta={"internal_evidence": True,
                          "suppress_transaction_alert": True},
                )
        email.assert_not_called()
        sms.assert_not_called()

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


class FinancialAlertTimestampTests(TestCase):
    def setUp(self):
        self.user = _user()

    def test_financial_alert_timestamps_are_lagos_time_and_labelled(self):
        """An aware UTC ledger timestamp must match the Lagos receipt time."""
        from .alerts import _describe, _email_alert_html, _sms_alert

        txn = Transaction.objects.create(
            user=self.user, amount=Decimal("1000"), direction=Transaction.OUT,
            service="transfer", reference="TZ-1",
            transaction_status=Transaction.SUCCESS,
        )
        created = datetime(2026, 9, 14, 14, 14, tzinfo=dt_timezone.utc)
        Transaction.objects.filter(pk=txn.pk).update(created=created)
        txn.refresh_from_db()

        _subject, body = _describe(txn)
        self.assertIn("14 Sep 2026, 03:14 PM WAT", body)
        self.assertIn("14-09-2026 15:14:00 WAT", _sms_alert(txn))
        self.assertIn("14 Sep 2026, 03:14 PM WAT", _email_alert_html(txn))


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

    def test_a_failed_whatsapp_alert_retries_without_dup_email(self):
        """A Meta outage must not permanently silence app-originated WhatsApp
        alerts. The email/push/SMS alert has its own dedupe, but WhatsApp remains
        owed until a send is accepted."""
        credit(self.user, Decimal("10000"), "funding")
        with patch("utility.providers.send_email") as email, \
             patch("whatsapp.router.reply",
                   return_value={"success": False, "error_code": 131000}) as wa_reply:
            with self.captureOnCommitCallbacks(execute=True):
                txn = debit(self.user, Decimal("1000"), "Transfer to Ada",
                            meta={"channel": "app"})
                txn.transaction_status = Transaction.SUCCESS
                txn.save(update_fields=["transaction_status"])
        email.assert_called_once()
        wa_reply.assert_called_once()
        txn.refresh_from_db()
        self.assertTrue(txn.meta.get("alerted"))
        self.assertFalse(txn.meta.get("whatsapp_alerted"))

        with patch("utility.providers.send_email") as email, \
             patch("whatsapp.router.reply",
                   return_value={"success": True, "message_id": "wamid.1"}) as wa_reply:
            with self.captureOnCommitCallbacks(execute=True):
                txn.save(update_fields=["meta"])
        email.assert_not_called()
        wa_reply.assert_called_once()
        txn.refresh_from_db()
        self.assertTrue(txn.meta.get("whatsapp_alerted"))

    def test_retry_sweep_sends_already_missed_app_alerts(self):
        """Rows that settled before the retry fix may already have the generic
        alert flag without the WhatsApp delivery flag. The worker sweep should
        repair those without another email."""
        from .alerts import retry_pending_whatsapp_alerts

        txn = Transaction.objects.create(
            user=self.user, amount=Decimal("1000"), direction=Transaction.OUT,
            service="Transfer to Ada", reference="APP-MISSED-1",
            transaction_status=Transaction.SUCCESS,
            meta={"channel": "app", "alerted": True})

        with patch("utility.providers.send_email") as email, \
             patch("whatsapp.router.reply",
                   return_value={"success": True, "message_id": "wamid.2"}) as wa_reply:
            sent = retry_pending_whatsapp_alerts(limit=10)
        self.assertEqual(sent, 1)
        email.assert_not_called()
        wa_reply.assert_called_once()
        txn.refresh_from_db()
        self.assertTrue(txn.meta.get("whatsapp_alerted"))

    def test_an_app_transfer_alert_identifies_the_destination(self):
        """The chat must say more than amount/ref for a debit made in the app."""
        from .alerts import _describe

        txn = Transaction.objects.create(
            user=self.user, amount=Decimal("1000"), direction=Transaction.OUT,
            service="Transfer to ADEYEMI WILLIAM", reference="APP-TR-1",
            transaction_status=Transaction.SUCCESS,
            meta={"channel": "app", "recipient_name": "ADEYEMI WILLIAM",
                  "bank": "First Bank", "account": "0228656883",
                  "narration": "For car"})
        _subject, body = _describe(txn)
        self.assertIn("to ADEYEMI WILLIAM", body)
        self.assertIn("To: ADEYEMI WILLIAM", body)
        self.assertIn("Bank: First Bank", body)
        self.assertIn("Account: 0228****83", body)
        self.assertIn("For: For car", body)

    def test_an_app_electricity_alert_carries_meter_and_address(self):
        from .alerts import _describe

        txn = Transaction.objects.create(
            user=self.user, amount=Decimal("1000"), direction=Transaction.OUT,
            service="Electricity — Ikeja", reference="APP-EL-1",
            transaction_status=Transaction.SUCCESS,
            meta={"channel": "app", "meter": "1023542134", "meter_type": "prepaid",
                  "customer_name": "ADEYEMI WILLIAM",
                  "customer_address": "12 Marina Road, Lagos"})
        _subject, body = _describe(txn)
        self.assertIn("Meter: 1023542134", body)
        self.assertIn("Customer: ADEYEMI WILLIAM", body)
        self.assertIn("Address: 12 Marina Road, Lagos", body)

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

    def test_a_previously_alerted_debit_gets_a_distinct_reversal_alert(self):
        """A reversal is a second, different customer event.

        The original debit already owns ``whatsapp_alerted``. Reusing that flag
        would suppress the later money-returned notification.
        """
        from .services import reverse_transfer

        credit(self.user, Decimal("10000"), "funding")
        with patch("utility.providers.send_email"), \
             patch("whatsapp.router.reply",
                   return_value={"success": True, "message_id": "wamid.debit"}) as wa_reply:
            with self.captureOnCommitCallbacks(execute=True):
                txn = debit(self.user, Decimal("1000"), "Transfer to Ada",
                            meta={"channel": "app"})
                txn.transaction_status = Transaction.SUCCESS
                txn.save(update_fields=["transaction_status"])

        txn.refresh_from_db()
        self.assertTrue(txn.meta.get("whatsapp_alerted"))
        wa_reply.reset_mock()

        with patch("utility.providers.send_email"), \
             patch("whatsapp.router.reply",
                   return_value={"success": True, "message_id": "wamid.reversal"}) as wa_reply:
            with self.captureOnCommitCallbacks(execute=True):
                reverse_transfer(txn.reference)

        wa_reply.assert_called_once()
        self.assertIn("Reversal", wa_reply.call_args[0][1])
        txn.refresh_from_db()
        self.assertTrue(txn.meta.get("whatsapp_alerted"))
        self.assertTrue(txn.meta.get("whatsapp_reversal_alerted"))

    def test_a_whatsapp_transfer_that_only_settles_later_is_announced(self):
        """The chat could only say "⏳ … processing", so the settlement alert is
        the ONE moment the customer can be told the money actually left. The
        channel de-dupe assumes a receipt was sent; here there wasn't one, and
        suppressing this leaves "processing" as the last word forever."""
        from .alerts import mark_awaiting_settlement

        credit(self.user, Decimal("10000"), "funding")
        with patch("whatsapp.router.reply") as wa_reply:
            with self.captureOnCommitCallbacks(execute=True):
                txn = debit(self.user, Decimal("1000"), "Transfer to Ada",
                            meta={"channel": "whatsapp"})
                mark_awaiting_settlement(txn)          # what the chat's ⏳ branch does
        wa_reply.assert_not_called()                    # nothing settled yet

        with patch("utility.providers.send_email"), \
             patch("whatsapp.router.reply") as wa_reply:
            with self.captureOnCommitCallbacks(execute=True):
                txn.refresh_from_db()
                txn.transaction_status = Transaction.SUCCESS
                txn.save(update_fields=["transaction_status"])
        wa_reply.assert_called_once()
        self.assertIn("Transfer successful", wa_reply.call_args[0][1])

    def test_a_whatsapp_transfer_that_fails_after_processing_is_announced(self):
        """A pending debit needs one reversal notice when the rail refuses it.

        The original processing message means the customer was already told about
        the debit, even though the generic ``alerted`` flag was never set.
        """
        from .alerts import mark_awaiting_settlement

        credit(self.user, Decimal("10000"), "funding")
        with patch("utility.providers.send_email") as email, \
             patch("whatsapp.router.reply",
                   return_value={"success": True, "message_id": "wamid.fail"}) as wa_reply:
            with self.captureOnCommitCallbacks(execute=True):
                txn = debit(self.user, Decimal("1000"), "Transfer to Ada",
                            meta={"channel": "whatsapp"})
                mark_awaiting_settlement(txn)
            with self.captureOnCommitCallbacks(execute=True):
                txn.transaction_status = Transaction.FAILED
                txn.save(update_fields=["transaction_status"])

        email.assert_called_once()
        wa_reply.assert_called_once()
        self.assertIn("Reversal", wa_reply.call_args[0][1])
        txn.refresh_from_db()
        self.assertTrue(txn.meta.get("reversal_alerted"))
        self.assertTrue(txn.meta.get("whatsapp_reversal_alerted"))

    def test_direct_whatsapp_exception_releases_the_matching_claim(self):
        from .alerts import send_whatsapp_transaction_alert

        txn = Transaction.objects.create(
            user=self.user, amount=Decimal("1000"), direction=Transaction.OUT,
            service="Transfer to Ada", reference="WA-DIRECT-EXCEPTION",
            transaction_status=Transaction.SUCCESS,
            meta={"channel": "app", "alerted": True})

        with patch("wallet.alerts._whatsapp_alert",
                   side_effect=RuntimeError("provider raised")):
            self.assertFalse(send_whatsapp_transaction_alert(txn))

        txn.refresh_from_db()
        self.assertNotIn("whatsapp_alerted", txn.meta)

        with patch("wallet.alerts._whatsapp_alert", return_value=True):
            self.assertTrue(send_whatsapp_transaction_alert(txn, reversal=True))
        txn.refresh_from_db()
        self.assertTrue(txn.meta.get("whatsapp_reversal_alerted"))

    def test_reconcile_retries_a_reversal_with_its_distinct_claim(self):
        from .alerts import retry_pending_whatsapp_alerts

        txn = Transaction.objects.create(
            user=self.user, amount=Decimal("1000"), direction=Transaction.OUT,
            service="Transfer to Ada", reference="WA-REVERSAL-RETRY",
            transaction_status=Transaction.PENDING,
            meta={"channel": "whatsapp", "reversal_alerted": True,
                  "wa_awaiting_settlement": True})
        Transaction.objects.filter(pk=txn.pk).update(
            transaction_status=Transaction.FAILED)

        with patch("wallet.alerts._whatsapp_alert", return_value=True) as send:
            self.assertEqual(retry_pending_whatsapp_alerts(limit=10), 1)

        send.assert_called_once()
        self.assertTrue(send.call_args.kwargs["reversal"])
        txn.refresh_from_db()
        self.assertTrue(txn.meta.get("whatsapp_reversal_alerted"))

    def test_a_failed_reconcile_retry_releases_its_claim_for_the_next_attempt(self):
        """A refused send can retry, while each attempt still claims first."""
        from .alerts import retry_pending_whatsapp_alerts

        txn = Transaction.objects.create(
            user=self.user, amount=Decimal("1000"), direction=Transaction.OUT,
            service="Transfer to Ada", reference="WA-RETRY-CLAIM",
            transaction_status=Transaction.SUCCESS,
            meta={"channel": "app", "alerted": True})

        with patch("wallet.alerts._whatsapp_alert", side_effect=[False, True]) as send:
            self.assertEqual(retry_pending_whatsapp_alerts(limit=10), 0)
            txn.refresh_from_db()
            self.assertNotIn("whatsapp_alerted", txn.meta)
            self.assertEqual(retry_pending_whatsapp_alerts(limit=10), 1)

        self.assertEqual(send.call_count, 2)
        txn.refresh_from_db()
        self.assertTrue(txn.meta.get("whatsapp_alerted"))

    def test_marking_awaiting_settlement_keeps_the_provider_payload(self):
        """It merges onto the row in the DB, not the in-memory copy — another
        writer's provider response must survive the flag."""
        from .alerts import mark_awaiting_settlement

        credit(self.user, Decimal("10000"), "funding")
        with self.captureOnCommitCallbacks(execute=True):
            txn = debit(self.user, Decimal("1000"), "transfer", meta={"channel": "whatsapp"})
        Transaction.objects.filter(pk=txn.pk).update(
            meta={"channel": "whatsapp", "provider_ref": "rail-99"})
        mark_awaiting_settlement(txn)                   # txn's in-memory meta is stale
        meta = Transaction.objects.get(pk=txn.pk).meta
        self.assertEqual(meta["provider_ref"], "rail-99")
        self.assertTrue(meta["wa_awaiting_settlement"])


@override_settings(TXN_ALERTS={"EMAIL": False, "SMS": False, "WHATSAPP": True})
class WhatsAppAlertClaimConcurrencyTests(TransactionTestCase):
    """The database claim, rather than a process-local flag, owns delivery."""

    reset_sequences = True

    def test_two_retry_workers_send_one_message(self):
        from whatsapp.models import WhatsAppLink
        from .alerts import retry_pending_whatsapp_alerts

        user = _user()
        WhatsAppLink.objects.create(
            user=user, wa_msisdn="2348012340000", status=WhatsAppLink.ACTIVE)
        txn = Transaction.objects.create(
            user=user, amount=Decimal("1000"), direction=Transaction.OUT,
            service="Transfer to Ada", reference="WA-CONCURRENT-CLAIM",
            transaction_status=Transaction.PENDING,
            meta={"channel": "app", "alerted": True})
        Transaction.objects.filter(pk=txn.pk).update(
            transaction_status=Transaction.SUCCESS)

        first_send = Event()
        release_first = Event()
        calls = []

        def fake_whatsapp_alert(*args, **kwargs):
            calls.append(True)
            if len(calls) == 1:
                first_send.set()
                self.assertTrue(release_first.wait(timeout=5))
            return True

        def worker():
            from django.db import close_old_connections

            close_old_connections()
            try:
                return retry_pending_whatsapp_alerts(limit=10)
            finally:
                close_old_connections()

        with patch("wallet.alerts._whatsapp_alert", side_effect=fake_whatsapp_alert):
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(worker)
                self.assertTrue(first_send.wait(timeout=5))
                second = pool.submit(worker)
                second_result = second.result(timeout=5)
                release_first.set()
                first_result = first.result(timeout=5)

        self.assertEqual(first_result + second_result, 1)
        self.assertEqual(len(calls), 1)
        txn.refresh_from_db()
        self.assertTrue(txn.meta.get("whatsapp_alerted"))


# A minimal WHATSAPP config carrying just the two template keys the fallback
# reads. The send legs themselves are patched (`whatsapp.router.reply` /
# `reply_template`), so no other WhatsApp credential is touched by these tests.
_WA_TMPL = {"TXN_ALERT_TEMPLATE": "txn_alert", "TXN_ALERT_TEMPLATE_LANG": "en_US"}


@override_settings(TXN_ALERTS={"EMAIL": True, "SMS": False, "WHATSAPP": True}, WHATSAPP=_WA_TMPL)
class WhatsAppAlertTemplateFallbackTests(TestCase):
    """Free-form text is delivered only inside WhatsApp's 24-hour service window.
    An alert about an app transaction — or a payout that settles hours after the
    customer last chatted — is the normal case for that window being CLOSED, and
    Meta refuses free-form text there (error 131047). The only message the
    platform still delivers is a pre-approved UTILITY template, so the alert must
    fall back to one; without it the debit/credit alert silently never lands."""

    def setUp(self):
        from whatsapp.models import WhatsAppLink

        self.user = _user()
        self.link = WhatsAppLink.objects.create(
            user=self.user, wa_msisdn="2348012340000", status=WhatsAppLink.ACTIVE)
        credit(self.user, Decimal("10000"), "funding")

    def _settle_app_debit(self):
        with self.captureOnCommitCallbacks(execute=True):
            txn = debit(self.user, Decimal("1000"), "Transfer to Ada",
                        meta={"channel": "app", "recipient_name": "ADA LOVELACE"})
            txn.transaction_status = Transaction.SUCCESS
            txn.save(update_fields=["transaction_status"])
        return txn

    def test_out_of_window_alert_falls_back_to_the_template(self):
        with patch("utility.providers.send_email") as email, \
             patch("whatsapp.router.reply",
                   return_value={"success": False, "error_code": 131047}) as wa_reply, \
             patch("whatsapp.router.reply_template",
                   return_value={"success": True, "message_id": "wamid.t"}) as wa_tmpl:
            txn = self._settle_app_debit()

        email.assert_called_once()          # email leg is unaffected
        wa_reply.assert_called_once()       # free-form tried first (cheap, in-window)
        wa_tmpl.assert_called_once()        # then the template, because Meta refused it
        msisdn, template, params = wa_tmpl.call_args[0]
        self.assertEqual(msisdn, "2348012340000")
        self.assertEqual(template, "txn_alert")
        self.assertEqual(wa_tmpl.call_args[1].get("lang"), "en_US")
        self.assertIn("Debit of", params[0])
        self.assertIn("ADA LOVELACE", params[0])
        self.assertEqual(params[1], txn.reference)
        txn.refresh_from_db()
        self.assertTrue(txn.meta.get("whatsapp_alerted"))   # counted as delivered

    def test_in_window_alert_never_touches_the_template(self):
        """When the free-form text is accepted, no template conversation is spent."""
        with patch("utility.providers.send_email"), \
             patch("whatsapp.router.reply",
                   return_value={"success": True, "message_id": "wamid.1"}) as wa_reply, \
             patch("whatsapp.router.reply_template") as wa_tmpl:
            txn = self._settle_app_debit()

        wa_reply.assert_called_once()
        wa_tmpl.assert_not_called()
        txn.refresh_from_db()
        self.assertTrue(txn.meta.get("whatsapp_alerted"))

    @override_settings(WHATSAPP={"TXN_ALERT_TEMPLATE": "", "TXN_ALERT_TEMPLATE_LANG": "en_US"})
    def test_a_blank_template_name_disables_the_fallback(self):
        """No template configured → the alert stays owed (retried later), it is
        never marked delivered off a send Meta refused."""
        with patch("utility.providers.send_email"), \
             patch("whatsapp.router.reply",
                   return_value={"success": False, "error_code": 131047}) as wa_reply, \
             patch("whatsapp.router.reply_template") as wa_tmpl:
            txn = self._settle_app_debit()

        wa_reply.assert_called_once()
        wa_tmpl.assert_not_called()
        txn.refresh_from_db()
        self.assertTrue(txn.meta.get("alerted"))
        self.assertFalse(txn.meta.get("whatsapp_alerted"))

    def test_template_parameters_are_single_line(self):
        """Meta rejects a template variable carrying a newline, a tab, or four or
        more consecutive spaces — so a recipient name with an embedded newline
        must not be able to drop the whole alert."""
        from .alerts import _whatsapp_template_summary

        txn = Transaction.objects.create(
            user=self.user, amount=Decimal("2500"), direction=Transaction.OUT,
            service="Transfer to Ada", reference="APP-LINE-1",
            transaction_status=Transaction.SUCCESS,
            meta={"channel": "app", "recipient_name": "ADA\nLOVELACE\t  ADETOLA"})
        summary = _whatsapp_template_summary(txn, reversal=False)
        self.assertNotIn("\n", summary)
        self.assertNotIn("\t", summary)
        self.assertNotIn("    ", summary)          # no run of 4+ spaces
        self.assertIn("ADA LOVELACE ADETOLA", summary)

    def test_reversal_summary_reads_as_money_returning(self):
        """A reversal is money coming back — the one-line template summary must
        say so, never repeat it as another debit."""
        from .alerts import _whatsapp_template_summary

        txn = Transaction.objects.create(
            user=self.user, amount=Decimal("1000"), direction=Transaction.OUT,
            service="Transfer to Ada", reference="APP-REV-1",
            transaction_status=Transaction.FAILED,
            meta={"channel": "app", "recipient_name": "ADA LOVELACE"})
        summary = _whatsapp_template_summary(txn, reversal=True)
        self.assertIn("Reversal", summary)
        self.assertIn("returned to your Zitch account", summary)
        self.assertNotIn("\n", summary)
