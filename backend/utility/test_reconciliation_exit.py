"""Scheduled recovery must expose incomplete work without stopping other phases."""
from io import StringIO
from datetime import timedelta
from unittest.mock import patch

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
