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
    MAX_ALERT_ATTEMPTS, _dispatch_alert, _email_alert_html, _prepare_alert,
    pending_alert_delivery_ids, retry_pending_whatsapp_alerts, send_transaction_alert,
    whatsapp_alert_failed,
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
        self.link = WhatsAppLink.objects.create(
            user=self.user, wa_msisdn="2348010000651", status=WhatsAppLink.ACTIVE)
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

    def test_credit_alert_prefers_inline_image_with_one_provider_acceptance(self):
        txn = self.movement()
        with (patch("whatsapp.providers.wa_live", return_value=True),
              patch("whatsapp.providers.upload_media", return_value="media-1") as upload,
              patch("whatsapp.providers.send_image_media",
                    return_value={"success": True, "message_id": "image-1"}) as image):
            self.assertTrue(_dispatch_alert(txn.alert_deliveries.get(channel="whatsapp").pk))
        self.assertTrue(upload.call_args.args[0].startswith(b"\xff\xd8"))
        self.assertEqual(image.call_args.args, (self.link.wa_msisdn, "media-1"))
        self.assertIn("Credit alert", image.call_args.kwargs["caption"])
        self.whatsapp.assert_not_called()
        self.assertEqual(txn.alert_deliveries.get(channel="whatsapp").provider_reference, "image-1")

    def test_credit_alert_does_not_fall_back_after_an_uncertain_image_send(self):
        """No text right behind an image that may have landed; the image goes
        again after a wait instead."""
        txn = self.movement()
        with (patch("whatsapp.providers.wa_live", return_value=True),
              patch("whatsapp.providers.upload_media", return_value="media-1"),
              patch("whatsapp.providers.send_image_media", return_value={"success": False, "uncertain": True})):
            self.assertFalse(_dispatch_alert(txn.alert_deliveries.get(channel="whatsapp").pk))
        self.whatsapp.assert_not_called()
        row = txn.alert_deliveries.get(channel="whatsapp")
        self.assertEqual((row.state, row.error_code), (Delivery.RETRY, "outcome_unknown"))

    def test_credit_alert_uses_the_template_when_the_service_window_is_closed(self):
        txn = self.movement()
        with (patch("whatsapp.providers.wa_live", return_value=True),
              patch("whatsapp.providers.upload_media", return_value="media-1"),
              patch("whatsapp.providers.send_image_media",
                    return_value={"success": False, "uncertain": False, "error_code": 131047}),
              patch("wallet.alerts._whatsapp_alert_via_template",
                    return_value={"success": True, "message_id": "template-1"}) as template):
            self.assertTrue(_dispatch_alert(txn.alert_deliveries.get(channel="whatsapp").pk))
        template.assert_called_once()
        self.whatsapp.assert_not_called()

    @override_settings(DEBUG=False, TESTING=False)
    def test_production_commit_queues_alerts_without_sending_then_worker_delivers(self):
        committed = []
        with self.captureOnCommitCallbacks(execute=True):
            txn = self.movement()
            transaction.on_commit(lambda: committed.append(True))
        self.assertEqual(committed, [True])
        self.assertEqual(txn.alert_deliveries.filter(state=Delivery.READY).count(), 3)
        self.email.assert_not_called()
        self.sms.assert_not_called()
        self.whatsapp.assert_not_called()
        with (patch("utility.providers.email_live", return_value=True),
              patch("utility.providers.sms_live", return_value=True),
              patch("whatsapp.providers.wa_live", return_value=True)):
            self.assertEqual(retry_pending_whatsapp_alerts(limit=10), 3)
        self.assertEqual(txn.alert_deliveries.filter(state=Delivery.ACCEPTED).count(), 3)
        self.email.assert_called_once()
        self.sms.assert_called_once()
        self.whatsapp.assert_called_once()

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

    def test_explicit_refusals_stop_after_the_attempt_cap(self):
        txn = self.movement()
        self.email.return_value = {"success": False, "uncertain": False}
        for _ in range(MAX_ALERT_ATTEMPTS):
            self.make_due(txn)
            _dispatch_alert(txn.alert_deliveries.get(channel="email").pk)
        self.assertEqual(self.email.call_count, MAX_ALERT_ATTEMPTS)
        row = txn.alert_deliveries.get(channel="email")
        self.assertEqual(row.state, Delivery.EXHAUSTED)
        self.assertEqual(row.attempts, MAX_ALERT_ATTEMPTS)
        self.make_due(txn)
        self.assertFalse(_dispatch_alert(row.pk))
        self.assertEqual(self.email.call_count, MAX_ALERT_ATTEMPTS)

    def test_refusals_back_off_to_ten_minutes_and_keep_trying_for_over_an_hour(self):
        """Five tries used to run out in fifteen minutes, inside one Meta incident."""
        txn = self.movement()
        self.email.return_value = {"success": False, "uncertain": False, "retryable": True}
        waits = []
        for _ in range(MAX_ALERT_ATTEMPTS - 1):
            self.make_due(txn)
            before = timezone.now()
            _dispatch_alert(txn.alert_deliveries.get(channel="email").pk)
            row = txn.alert_deliveries.get(channel="email")
            self.assertEqual(row.state, Delivery.RETRY)
            waits.append(round((row.next_attempt_at - before).total_seconds() / 60))
        self.assertEqual(waits[:5], [1, 2, 4, 8, 10])
        self.assertEqual(max(waits), 10)
        self.assertGreater(sum(waits), 60)

    def test_an_unknown_outcome_is_sent_again_after_a_wait(self):
        """A timeout or a 5xx used to park the alert for review, where nothing
        sent it again: one Meta hiccup and the customer was never told."""
        txn = self.movement()
        self.email.return_value = {"success": False, "uncertain": True, "retryable": True}
        row = txn.alert_deliveries.get(channel="email")
        self.assertFalse(_dispatch_alert(row.pk))
        row.refresh_from_db()
        self.assertEqual((row.state, row.error_code), (Delivery.RETRY, "outcome_unknown"))
        self.assertGreaterEqual(row.next_attempt_at, timezone.now() + timedelta(seconds=80))
        self.assertFalse(_dispatch_alert(row.pk))       # not before the wait
        self.email.assert_called_once()
        self.email.return_value = {"success": True, "raw": {"id": "email-2"}}
        self.make_due(txn)
        self.assertTrue(_dispatch_alert(row.pk))
        self.assertEqual(self.email.call_count, 2)

    def test_a_result_with_no_flags_at_all_is_also_retried(self):
        """A connection error that came back as a bare failure."""
        txn = self.movement()
        self.email.return_value = {"success": False, "message": "delivery failed"}
        row = txn.alert_deliveries.get(channel="email")
        self.assertFalse(_dispatch_alert(row.pk))
        row.refresh_from_db()
        self.assertEqual(row.state, Delivery.RETRY)

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

    def test_crash_after_dispatch_boundary_is_sent_again(self):
        # 14 November 2026: a deploy restarted the worker one second after three
        # deposits were credited, and all nine alerts it had claimed were parked
        # for review and never sent. A send the process never finished is retried.
        txn = self.movement()
        row = txn.alert_deliveries.get(channel="email")
        Delivery.objects.filter(pk=row.pk).update(
            state=Delivery.DISPATCHING, attempts=1, claim_token="in-flight-token",
            lease_expires_at=timezone.now() - timedelta(seconds=1),
        )
        self.assertFalse(_dispatch_alert(row.pk))
        row.refresh_from_db()
        self.assertEqual(row.state, Delivery.RETRY)
        self.assertEqual(row.error_code, "dispatch_lease_expired")
        self.email.assert_not_called()
        self.assertIn(row.pk, pending_alert_delivery_ids(limit=10))
        self.assertTrue(_dispatch_alert(row.pk))
        self.email.assert_called_once()
        row.refresh_from_db()
        self.assertEqual(row.state, Delivery.ACCEPTED)
        self.assertEqual(row.attempts, 2)

    def test_a_send_cut_off_on_its_last_attempt_is_exhausted_and_reported(self):
        txn = self.movement()
        row = txn.alert_deliveries.get(channel="email")
        Delivery.objects.filter(pk=row.pk).update(
            state=Delivery.DISPATCHING, attempts=MAX_ALERT_ATTEMPTS, claim_token="in-flight-token",
            lease_expires_at=timezone.now() - timedelta(seconds=1),
        )
        with patch("utility.alerts.alert") as ops, self.captureOnCommitCallbacks(execute=True):
            self.assertFalse(_dispatch_alert(row.pk))
        row.refresh_from_db()
        self.assertEqual(row.state, Delivery.EXHAUSTED)
        self.email.assert_not_called()
        ops.assert_called_once()

    def test_an_alert_that_runs_out_of_tries_is_reported_to_the_operator(self):
        txn = self.movement()
        self.email.return_value = {"success": False, "uncertain": True}
        row = txn.alert_deliveries.get(channel="email")
        Delivery.objects.filter(pk=row.pk).update(attempts=MAX_ALERT_ATTEMPTS - 1)
        with patch("utility.alerts.alert") as ops:
            self.assertFalse(_dispatch_alert(row.pk))
        row.refresh_from_db()
        self.assertEqual((row.state, row.error_code), (Delivery.EXHAUSTED, "retry_limit"))
        ops.assert_called_once()
        self.assertEqual(ops.call_args.kwargs["reason"], "retry_limit")

    @override_settings(DEBUG=False, TESTING=False)
    def test_an_alert_left_for_review_is_reported_to_the_operator(self):
        txn = self.movement()
        self.email.return_value = {"success": True, "uncertain": False, "raw": {}}
        row = txn.alert_deliveries.get(channel="email")
        with patch("utility.providers.email_live", return_value=True), \
             patch("utility.alerts.alert") as ops:
            self.assertFalse(_dispatch_alert(row.pk))
        row.refresh_from_db()
        self.assertEqual(row.state, Delivery.REVIEW)
        ops.assert_called_once()
        self.assertEqual(ops.call_args.kwargs["reason"], "dispatch_outcome_unknown")

    def test_a_whatsapp_alert_meta_fails_for_a_closed_window_goes_again_as_the_template(self):
        """Meta answers 200 and a message id outside the 24-hour window, and the
        refusal (131047) comes later on the status webhook."""
        from whatsapp.views import _apply_status

        txn = self.movement()
        self.whatsapp.return_value = {"success": True, "message_id": "wamid.LATE"}
        row = txn.alert_deliveries.get(channel="whatsapp")
        self.assertTrue(_dispatch_alert(row.pk))
        _apply_status({"id": "wamid.LATE", "status": "failed", "errors": [{"code": 131047}]})
        row.refresh_from_db()
        self.assertEqual((row.state, row.error_code), (Delivery.RETRY, "window_closed"))
        with patch("wallet.alerts._whatsapp_alert_via_template",
                   return_value={"success": True, "message_id": "wamid.TEMPLATE"}) as template:
            self.assertTrue(_dispatch_alert(row.pk))
        template.assert_called_once()
        self.assertEqual(self.whatsapp.call_count, 1)     # the free-form text was not repeated
        row.refresh_from_db()
        self.assertEqual((row.state, row.provider_reference), (Delivery.ACCEPTED, "wamid.TEMPLATE"))

    def test_other_late_failures_retry_and_unknown_message_ids_change_nothing(self):
        txn = self.movement()
        self.whatsapp.return_value = {"success": True, "message_id": "wamid.ONE"}
        row = txn.alert_deliveries.get(channel="whatsapp")
        self.assertTrue(_dispatch_alert(row.pk))
        self.assertFalse(whatsapp_alert_failed("wamid.SOMEONE-ELSE", 131047))
        self.assertTrue(whatsapp_alert_failed("wamid.ONE", 131026))
        row.refresh_from_db()
        self.assertEqual((row.state, row.error_code), (Delivery.RETRY, "delivery_failed_131026"))
        self.assertFalse(whatsapp_alert_failed("wamid.ONE", 131047))   # already reopened

    def test_an_alert_with_no_contact_route_is_dropped_after_thirty_minutes(self):
        self.user.email_verified = False
        self.user.save(update_fields=["email_verified"])
        txn = self.movement()
        row = txn.alert_deliveries.get(channel="email")
        self.assertFalse(_dispatch_alert(row.pk))
        row.refresh_from_db()
        self.assertEqual((row.state, row.error_code), (Delivery.READY, "email_unverified"))
        Delivery.objects.filter(pk=row.pk).update(
            created=timezone.now() - timedelta(minutes=31), next_attempt_at=None)
        self.assertFalse(_dispatch_alert(row.pk))
        row.refresh_from_db()
        self.assertEqual((row.state, row.error_code), (Delivery.SKIPPED, "email_unverified"))
        self.email.assert_not_called()

    def test_the_operator_command_lists_then_requeues_alerts_that_need_review(self):
        txn = self.movement()
        row = txn.alert_deliveries.get(channel="email")
        Delivery.objects.filter(pk=row.pk).update(
            state=Delivery.REVIEW, attempts=1, error_code="dispatch_outcome_unknown")
        out = StringIO()
        call_command("requeue_transaction_alerts", stdout=out)
        self.assertIn(f"delivery {row.pk}: email", out.getvalue())
        self.assertIn("would be requeued", out.getvalue())
        row.refresh_from_db()
        self.assertEqual(row.state, Delivery.REVIEW)
        self.assertNotIn("outbox@zitch.test", out.getvalue())

        out = StringIO()
        call_command("requeue_transaction_alerts", "--apply", stdout=out)
        row.refresh_from_db()
        self.assertEqual((row.state, row.attempts), (Delivery.READY, 0))
        self.assertTrue(_dispatch_alert(row.pk))
        self.email.assert_called_once()

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
