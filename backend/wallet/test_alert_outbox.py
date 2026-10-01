"""Durable per-channel acceptance, bounded retries and ambiguous dispatch review."""
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.db import transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from wallet.alerts import (
    _dispatch_alert, _email_alert_html, _prepare_alert, retry_pending_whatsapp_alerts,
    send_transaction_alert,
)
from wallet.models import Transaction, TransactionAlertDelivery as Delivery
from wallet.services import get_or_create_wallet
from whatsapp.models import WhatsAppLink


@override_settings(TXN_ALERTS={"EMAIL": True, "SMS": True, "WHATSAPP": True, "PUSH": False})
class TransactionAlertOutboxTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="outbox-customer", phone="08010000651", email="outbox@zitch.test",
            email_verified=True, phone_verified=True, first_name="Ada",
        )
        get_or_create_wallet(self.user)
        WhatsAppLink.objects.create(user=self.user, wa_msisdn="2348010000651", status=WhatsAppLink.ACTIVE)
        self.email = self.enterContext(patch("utility.providers.send_email", return_value={"success": True, "raw": {"id": "email-1"}}))
        self.sms = self.enterContext(patch("utility.providers.send_sms", return_value={"success": True, "message_id": "sms-1"}))
        self.whatsapp = self.enterContext(patch("whatsapp.router.reply", return_value={"success": True, "message_id": "wa-1"}))

    def movement(self, reference="OUTBOX-1", meta=None, direction=Transaction.IN):
        return Transaction.objects.create(
            user=self.user, service="funding", amount=Decimal("500.00"), direction=direction,
            transaction_status=Transaction.SUCCESS, reference=reference, meta=meta or {},
        )

    def make_due(self, txn, channel="email"):
        txn.alert_deliveries.filter(channel=channel).update(next_attempt_at=timezone.now() - timedelta(seconds=1))

    def test_outbox_is_durable_before_commit_callback_and_rolled_back_with_movement(self):
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            txn = self.movement()
        self.assertEqual(txn.alert_deliveries.count(), 3)
        self.assertTrue(callbacks)
        self.email.assert_not_called()
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                self.movement("OUTBOX-ROLLBACK")
                raise RuntimeError("rollback")
        self.assertFalse(Delivery.objects.filter(transaction__reference="OUTBOX-ROLLBACK").exists())
        self.email.assert_not_called()

    def test_worker_recovers_queued_work_even_without_on_commit_callback(self):
        txn = self.movement()
        self.assertEqual(retry_pending_whatsapp_alerts(limit=10), 3)
        self.assertEqual(txn.alert_deliveries.filter(state=Delivery.ACCEPTED).count(), 3)
        self.email.assert_called_once()
        self.sms.assert_called_once()
        self.whatsapp.assert_called_once()
        self.assertEqual(retry_pending_whatsapp_alerts(limit=10), 0)

    def test_one_refused_channel_retries_without_repeating_accepted_channels(self):
        txn = self.movement()
        self.email.return_value = {"success": False, "uncertain": False, "retryable": True, "http_status": 429}
        self.assertEqual(retry_pending_whatsapp_alerts(limit=10), 2)
        self.assertEqual(txn.alert_deliveries.get(channel="email").state, Delivery.RETRY)
        self.email.return_value = {"success": True, "raw": {"id": "email-2"}}
        self.assertEqual(retry_pending_whatsapp_alerts(limit=10), 0)
        self.make_due(txn)
        self.assertEqual(retry_pending_whatsapp_alerts(limit=10), 1)
        self.assertEqual(self.email.call_count, 2)
        self.sms.assert_called_once()
        self.whatsapp.assert_called_once()

    def test_explicit_refusals_stop_after_five_dispatches(self):
        txn = self.movement()
        self.email.return_value = {"success": False, "uncertain": False}
        for _ in range(5):
            self.make_due(txn)
            _dispatch_alert(txn.alert_deliveries.get(channel="email").pk)
        self.assertEqual(self.email.call_count, 5)
        row = txn.alert_deliveries.get(channel="email")
        self.assertEqual(row.state, Delivery.EXHAUSTED)
        self.assertEqual(row.attempts, 5)
        self.make_due(txn)
        self.assertFalse(_dispatch_alert(row.pk))
        self.assertEqual(self.email.call_count, 5)

    def test_unknown_provider_outcome_is_not_blindly_retried(self):
        txn = self.movement()
        self.email.return_value = {"success": False, "uncertain": True, "retryable": True}
        row = txn.alert_deliveries.get(channel="email")
        self.assertFalse(_dispatch_alert(row.pk))
        row.refresh_from_db()
        self.assertEqual(row.state, Delivery.REVIEW)
        self.assertFalse(_dispatch_alert(row.pk))
        self.email.assert_called_once()

    @override_settings(DEBUG=False, TESTING=False)
    def test_positive_response_without_acceptance_id_is_reviewed_never_retried(self):
        txn = self.movement()
        self.email.return_value = {"success": True, "uncertain": False, "raw": {}}
        row = txn.alert_deliveries.get(channel="email")
        with patch("utility.providers.email_live", return_value=True):
            self.assertFalse(_dispatch_alert(row.pk))
            self.assertFalse(_dispatch_alert(row.pk))
        row.refresh_from_db()
        self.assertEqual(row.state, Delivery.REVIEW)
        self.assertEqual(row.error_code, "dispatch_outcome_unknown")
        self.email.assert_called_once()

    def test_crash_before_dispatch_can_recover_after_preparation_lease(self):
        txn = self.movement()
        row = txn.alert_deliveries.get(channel="email")
        self.assertIsNotNone(_prepare_alert(row.pk))
        self.assertFalse(_dispatch_alert(row.pk))
        self.email.assert_not_called()
        Delivery.objects.filter(pk=row.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
        self.assertTrue(_dispatch_alert(row.pk))
        self.email.assert_called_once()

    def test_crash_after_dispatch_boundary_is_visible_for_review(self):
        txn = self.movement()
        row = txn.alert_deliveries.get(channel="email")
        Delivery.objects.filter(pk=row.pk).update(
            state=Delivery.DISPATCHING, attempts=1, claim_token="in-flight-token",
            lease_expires_at=timezone.now() - timedelta(seconds=1),
        )
        self.assertFalse(_dispatch_alert(row.pk))
        row.refresh_from_db()
        self.assertEqual(row.state, Delivery.REVIEW)
        self.assertEqual(row.error_code, "dispatch_lease_expired")
        self.email.assert_not_called()

    def test_mutable_ledger_meta_cannot_recreate_an_accepted_delivery(self):
        txn = self.movement()
        send_transaction_alert(txn)
        Transaction.objects.filter(pk=txn.pk).update(meta={"bank_result": "later"})
        txn.refresh_from_db()
        txn.save(update_fields=["meta"])
        send_transaction_alert(txn)
        self.email.assert_called_once()
        self.sms.assert_called_once()
        self.whatsapp.assert_called_once()
        self.assertEqual(txn.alert_deliveries.count(), 3)

    def test_legacy_claims_are_neither_backfilled_as_accepted_nor_mass_replayed(self):
        txn = self.movement(meta={"alerted": True, "whatsapp_alerted": True})
        self.assertEqual(txn.alert_deliveries.filter(state=Delivery.REVIEW).count(), 3)
        self.assertEqual(retry_pending_whatsapp_alerts(limit=10), 0)
        self.email.assert_not_called()
        self.sms.assert_not_called()
        self.whatsapp.assert_not_called()

    def test_unverified_inbox_never_receives_private_financial_details(self):
        self.user.email_verified = False
        self.user.save(update_fields=["email_verified"])
        txn = self.movement()
        row = txn.alert_deliveries.get(channel="email")
        self.assertFalse(_dispatch_alert(row.pk))
        row.refresh_from_db()
        self.assertEqual(row.state, Delivery.READY)
        self.assertEqual(row.attempts, 0)
        self.assertEqual(row.error_code, "email_unverified")
        self.email.assert_not_called()

    @override_settings(DEBUG=False, TESTING=False)
    def test_missing_provider_route_waits_without_spending_a_dispatch_attempt(self):
        txn = self.movement()
        row = txn.alert_deliveries.get(channel="email")
        with patch("utility.providers.email_live", return_value=False):
            self.assertFalse(_dispatch_alert(row.pk))
        row.refresh_from_db()
        self.assertEqual(row.state, Delivery.READY)
        self.assertEqual(row.attempts, 0)
        self.assertEqual(row.error_code, "email_unconfigured")
        self.email.assert_not_called()

    def test_waiting_contacts_cannot_starve_fresh_alerts_in_bounded_sweep(self):
        old = self.movement("OUTBOX-OLD")
        old.alert_deliveries.update(next_attempt_at=timezone.now() - timedelta(seconds=1))
        fresh = self.movement("OUTBOX-FRESH")
        self.assertEqual(retry_pending_whatsapp_alerts(limit=1), 1)
        self.assertEqual(fresh.alert_deliveries.filter(state=Delivery.ACCEPTED).count(), 1)
        self.assertEqual(old.alert_deliveries.filter(state=Delivery.ACCEPTED).count(), 0)

    def test_distinct_reversal_outbox_survives_erased_legacy_marker(self):
        txn = self.movement(direction=Transaction.OUT)
        send_transaction_alert(txn)
        Transaction.objects.filter(pk=txn.pk).update(meta={})
        txn.transaction_status = Transaction.FAILED
        txn.save(update_fields=["transaction_status"])
        send_transaction_alert(txn, reversal=True)
        self.assertEqual(txn.alert_deliveries.filter(reversal=True, state=Delivery.ACCEPTED).count(), 3)
        self.assertEqual(self.email.call_count, 2)
        self.assertEqual(self.whatsapp.call_count, 2)

    def test_status_diagnostics_expose_counts_without_contacts_or_payloads(self):
        txn = self.movement(meta={"alerted": True})
        out = StringIO()
        call_command("transaction_alerts_status", stdout=out)
        self.assertIn("review_or_exhausted: 3", out.getvalue())
        self.assertNotIn(self.user.email, out.getvalue())
        self.assertNotIn(self.user.phone, out.getvalue())
        self.assertNotIn(txn.reference, out.getvalue())
        self.email.assert_not_called()

    def test_transaction_description_cannot_inject_html_into_financial_email(self):
        self.user.first_name = '<a href="https://attacker.test">Bank</a>'
        self.user.save(update_fields=["first_name"])
        txn = self.movement(meta={"counterparty": '<img src=x onerror="steal()">'})
        html = _email_alert_html(txn)
        self.assertNotIn('<img src=x', html)
        self.assertNotIn('<a href="https://attacker.test">', html)
        self.assertIn("&lt;img", html)
