"""Scheduled recovery must expose incomplete work without stopping other phases."""
from io import StringIO
from datetime import timedelta
from unittest.mock import patch
from types import SimpleNamespace

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from wallet.models import Wallet, WemaProvisioningAttempt
from wallet.tests import make_user
from whatsapp.models import AuditLog


class ReconciliationExitTests(TestCase):
    def setUp(self):
        cache.clear()
        user, _ = make_user("08033331888", "recon-exit@example.test")
        self.wallet = Wallet.objects.get(user=user)
        self.wallet.pnd_lifted = True
        self.wallet.save(update_fields=["pnd_lifted"])

    def run_sweep(self, **result):
        report = dict(credited=0, windows=1, error_code="", fetch_failed=False,
                      backlog=False, diagnostic={})
        report.update(result)
        with patch("utility.management.commands.reconcile_wema.wema_provisioned_wallets",
                   return_value=[self.wallet]), \
             patch("utility.management.commands.reconcile_wema.reconcile_account_history",
                   return_value=report), \
             patch("utility.alerts.alert"):
            call_command("reconcile_wema", account_recovery_limit=0,
                         stdout=StringIO(), stderr=StringIO())

    def test_each_run_reports_how_far_every_statement_spelling_reaches(self):
        # One probe per run, before any history is read, printed without
        # customer data; a probe that fails must not stop the sweep.
        found = [
            {"form": "next-day", "ok": True, "http_status": 200, "rows": 17, "credits": 4,
             "newest": "2026-11-13T23:01:33"},
            {"form": "now", "ok": True, "http_status": 200, "rows": 18, "credits": 5,
             "newest": "2026-11-14T00:10:05", "chosen": True,
             "newest_transaction_date": "14 Nov 2026 12:10 AM",
             "newest_kind": "Credit/InterBank"},
        ]
        out = StringIO()
        report = dict(credited=0, windows=1, error_code="", fetch_failed=False,
                      backlog=False, diagnostic={})
        with patch("utility.management.commands.reconcile_wema.wema_provisioned_wallets",
                   return_value=[self.wallet, self.wallet]), \
             patch("utility.management.commands.reconcile_wema.reconcile_account_history",
                   return_value=report), \
             patch("utility.management.commands.reconcile_wema.wema.probe_history_to_forms",
                   return_value=found) as probe, \
             patch("utility.alerts.alert"):
            call_command("reconcile_wema", account_recovery_limit=0,
                         stdout=out, stderr=StringIO())
        self.assertEqual(probe.call_count, 1)
        text = out.getvalue()
        self.assertIn("transhistoryV2 probe: to=next-day ok=True http=200 rows=17 "
                      "credits=4 newest=2026-11-13T23:01:33 (-; -) today=", text)
        self.assertIn("to=now ok=True http=200 rows=18 credits=5 "
                      "newest=2026-11-14T00:10:05 (14 Nov 2026 12:10 AM; Credit/InterBank)", text)
        self.assertEqual(text.count("<- used"), 1)

        with patch("utility.management.commands.reconcile_wema.wema_provisioned_wallets",
                   return_value=[self.wallet]), \
             patch("utility.management.commands.reconcile_wema.reconcile_account_history",
                   return_value=report) as sweep, \
             patch("utility.management.commands.reconcile_wema.wema.probe_history_to_forms",
                   side_effect=RuntimeError("probe broke")), \
             patch("utility.alerts.alert"):
            call_command("reconcile_wema", account_recovery_limit=0,
                         stdout=StringIO(), stderr=StringIO())
        self.assertEqual(sweep.call_count, 1)

    @override_settings(WEMA_RECONCILE_FAIL_INCOMPLETE=True)
    def test_opening_review_fails_after_durable_report_without_changing_balance(self):
        before = self.wallet.balance
        with self.assertRaises(SystemExit) as caught:
            self.run_sweep(error_code="history_opening_review")
        self.assertEqual(caught.exception.code, 1)
        report = AuditLog.objects.get(action="recon.wema_run").after
        self.assertEqual(report["history_blocked"], 1)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, before)
        self.assertIsNone(cache.get("zitch:money-reconcile:lock"))

    @override_settings(WEMA_RECONCILE_FAIL_INCOMPLETE=True)
    def test_backlog_fails_without_claiming_coverage_complete(self):
        with self.assertRaises(SystemExit):
            self.run_sweep(backlog=True)
        self.assertEqual(AuditLog.objects.get(action="recon.wema_run").after["history_backlog"], 1)

    @override_settings(WEMA_RECONCILE_FAIL_INCOMPLETE=True)
    def test_complete_sweep_succeeds(self):
        self.run_sweep()
        self.assertEqual(AuditLog.objects.get(action="recon.wema_run").after["history_blocked"], 0)

    @override_settings(WEMA_RECONCILE_FAIL_INCOMPLETE=True)
    def test_one_history_exception_does_not_stop_other_wallets_or_settlement(self):
        other, _ = make_user("08033331889", "recon-other@example.test")
        other_wallet = Wallet.objects.get(user=other)
        other_wallet.pnd_lifted = True
        good = dict(credited=1, windows=1, error_code="", fetch_failed=False,
                    backlog=False, diagnostic={})
        module = "utility.management.commands.reconcile_wema."
        with patch(module + "wema_provisioned_wallets", return_value=[self.wallet, other_wallet]), \
             patch(module + "reconcile_account_history", side_effect=[ValueError("bad provider row"), good]) as history, \
             patch(module + "pending_vas_purchases", return_value=[]) as bills, \
             patch("utility.alerts.alert"), self.assertRaises(SystemExit):
            call_command("reconcile_wema", account_recovery_limit=0,
                         stdout=StringIO(), stderr=StringIO())
        self.assertEqual(history.call_count, 2)
        bills.assert_called()
        report = AuditLog.objects.get(action="recon.wema_run").after
        self.assertEqual(report["fetch_failures"], 1)
        self.assertEqual(report["credited"], 1)

    @override_settings(WEMA_RECONCILE_FAIL_INCOMPLETE=True)
    def test_restriction_retry_exception_does_not_stop_history(self):
        self.wallet.pnd_lifted = False
        module = "utility.management.commands.reconcile_wema."
        good = dict(credited=0, windows=1, error_code="", fetch_failed=False,
                    backlog=False, diagnostic={})
        with patch(module + "wema_provisioned_wallets", return_value=[self.wallet]), \
             patch(module + "wema.lift_debit_restriction", side_effect=ValueError("bad result")), \
             patch(module + "reconcile_account_history", return_value=good) as history, \
             patch("utility.alerts.alert"), self.assertRaises(SystemExit):
            call_command("reconcile_wema", account_recovery_limit=0,
                         stdout=StringIO(), stderr=StringIO())
        history.assert_called_once()
        self.assertEqual(AuditLog.objects.get(action="recon.wema_run").after["pnd_failures"], 1)

    @override_settings(WEMA_RECONCILE_FAIL_INCOMPLETE=True)
    def test_failed_payout_lookup_does_not_stop_later_payout(self):
        module = "utility.management.commands.reconcile_wema."
        rows = [SimpleNamespace(pk=n, reference=f"PAYOUT-{n}", meta={}) for n in (1, 2)]
        with patch(module + "wema_provisioned_wallets", return_value=[]), \
             patch(module + "payout_provider", return_value="wema"), \
             patch(module + "pending_bank_payouts", side_effect=[rows, []]), \
             patch(module + "claim_status_lookup", return_value=True), \
             patch(module + "wema.confirm_transfer_status", side_effect=[
                 ValueError("malformed"), {"success": True, "status": "SUCCESS"}]), \
             patch(module + "wema.classify_transfer_status", return_value="success"), \
             patch(module + "settle_payout", return_value=object()) as settle, \
             patch(module + "reverse_transfer") as reverse, \
             patch("utility.alerts.alert"), self.assertRaises(SystemExit):
            call_command("reconcile_wema", account_recovery_limit=0,
                         stdout=StringIO(), stderr=StringIO())
        settle.assert_called_once_with("PAYOUT-2")
        reverse.assert_not_called()
        report = AuditLog.objects.get(action="recon.wema_run").after
        self.assertEqual(report["status_failures"], 1)
        self.assertEqual(report["payouts_settled"], 1)

    @override_settings(WEMA_RECONCILE_FAIL_INCOMPLETE=True)
    def test_failed_bill_lookup_does_not_refund_or_stop_later_bill(self):
        module = "utility.management.commands.reconcile_wema."
        rows = [SimpleNamespace(pk=n, reference=f"BILL-{n}", meta={}) for n in (1, 2)]
        result = {"success": True, "status": "SUCCESS"}
        with patch(module + "wema_provisioned_wallets", return_value=[]), \
             patch(module + "pending_vas_purchases", side_effect=[rows, []]), \
             patch(module + "recorded_vas_outcome", return_value=None), \
             patch(module + "claim_status_lookup", return_value=True), \
             patch(module + "vas_requery", side_effect=[ValueError("malformed"), result]), \
             patch(module + "settle_or_refund", return_value="success") as settle, \
             patch("utility.alerts.alert"), self.assertRaises(SystemExit):
            call_command("reconcile_wema", account_recovery_limit=0,
                         stdout=StringIO(), stderr=StringIO())
        settle.assert_called_once_with(rows[1], result)
        report = AuditLog.objects.get(action="recon.wema_run").after
        self.assertEqual(report["vas_status_failures"], 1)
        self.assertEqual(report["vas_settled"], 1)

    @override_settings(WEMA_RECONCILE_FAIL_INCOMPLETE=False)
    def test_interactive_reporting_preserves_existing_opt_in_contract(self):
        self.run_sweep(error_code="history_opening_review")
        self.assertEqual(AuditLog.objects.get(action="recon.wema_run").after["history_blocked"], 1)

    @override_settings(WEMA_RECONCILE_FAIL_INCOMPLETE=True)
    def test_accepted_identity_failure_is_reported_after_other_phases(self):
        self.wallet.account_number = "0453331888"
        self.wallet.save(update_fields=["account_number"])
        WemaProvisioningAttempt.objects.create(user_id=self.wallet.user_id,
            tracking_id="audit-identity", identity_type="bvn", identity_hash="a" * 64,
            identity_last4="1234", otp_verified_at=timezone.now(),
            expires_at=timezone.now() + timedelta(minutes=5))
        with patch("wallet.identity.finish_accepted_identity", return_value="pending"), \
             patch("utility.management.commands.reconcile_wema.wema_provisioned_wallets", return_value=[]), \
             patch("utility.alerts.alert"), self.assertRaises(SystemExit):
            call_command("reconcile_wema", account_recovery_limit=1,
                         stdout=StringIO(), stderr=StringIO())
        report = AuditLog.objects.get(action="recon.wema_run").after
        self.assertEqual(report["identity_completion_pending"], 1)
        self.assertIn("vas_checked", report)


_UNCONFIGURED_WEMA = {"BASE_URL": "https://bank.example", "CHANNEL_ID": "",
                      "KEYS": {"wallet": ""}, "SIMULATION": False}
_LIVE_WEMA = {"BASE_URL": "https://bank.example", "CHANNEL_ID": "test-channel",
              "KEYS": {"wallet": "test-wallet"}, "SIMULATION": False}


class UnconfiguredCronNamesTheCauseTests(TestCase):
    """A cron without the API's Wema settings must say so, not blame the bank."""

    def setUp(self):
        cache.clear()
        user, _ = make_user("08033331890", "recon-unconfigured@example.test")
        self.wallet = Wallet.objects.get(user=user)
        self.wallet.pnd_lifted = True
        self.wallet.save(update_fields=["pnd_lifted"])

    def _sweep(self):
        failed = dict(credited=0, windows=1, error_code="history_fetch_failed",
                      fetch_failed=True, backlog=False, diagnostic={})
        err = StringIO()
        module = "utility.management.commands.reconcile_wema."
        with patch(module + "wema_provisioned_wallets", return_value=[self.wallet]), \
             patch(module + "reconcile_account_history", return_value=failed), \
             patch("utility.alerts.alert") as page, self.assertRaises(SystemExit):
            call_command("reconcile_wema", account_recovery_limit=0,
                         stdout=StringIO(), stderr=err)
        return err.getvalue(), [str(c.args[0]) for c in page.call_args_list]

    @override_settings(WEMA_RECONCILE_FAIL_INCOMPLETE=True, WEMA=_UNCONFIGURED_WEMA)
    def test_missing_settings_are_named(self):
        err, pages = self._sweep()
        self.assertIn("Wema not live in this environment "
                      "(channel_id_missing, wallet_key_missing)", err)
        fetch_pages = [m for m in pages if "history fetches failed" in m]
        self.assertEqual(len(fetch_pages), 1)
        self.assertIn("channel_id_missing", fetch_pages[0])
        self.assertNotIn("unreachable", fetch_pages[0])

    @override_settings(WEMA_RECONCILE_FAIL_INCOMPLETE=True, WEMA=_LIVE_WEMA)
    def test_a_real_outage_is_still_reported_as_one(self):
        err, pages = self._sweep()
        self.assertNotIn("not live", err)
        self.assertTrue(any("unreachable or auth rejected" in m for m in pages))
