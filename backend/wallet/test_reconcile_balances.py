"""Tests for reconcile_balances — the ledger-vs-bank (Wema NUBAN) integrity check.

Compares each provisioned wallet's ledger balance to the real NUBAN balance and
PAGES only when the ledger EXCEEDS the bank (the float-risk direction). The benign
direction (bank ahead of ledger — an unswept deposit) is logged, not paged. Runs
only when Wema is live; no-ops in simulation/mock.
"""
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from wallet.models import Wallet
from wallet.services import wema_account_reference
from wallet.tests import make_user


def _provision(user, account_number="0123456789"):
    Wallet.objects.filter(user=user).update(
        account_reference=wema_account_reference(user), account_number=account_number)


def _bank(naira):
    return {"success": True, "balance_naira": Decimal(naira)}


class ReconcileBalancesTests(TestCase):
    def setUp(self):
        # Seed credit => ledger == 5000; provision a NUBAN so the wallet is swept.
        self.user, _ = make_user("08033330001", "rb@zitch.app", balance="5000")
        _provision(self.user)

    def _run(self, *args):
        out, err = StringIO(), StringIO()
        code = 0
        with mock.patch("utility.wema.wema_live", return_value=True), \
             mock.patch("utility.alerts.alert") as alert_mock:
            try:
                call_command("reconcile_balances", *args, stdout=out, stderr=err)
            except SystemExit as exc:
                code = exc.code
        return out.getvalue(), err.getvalue(), alert_mock, code

    def test_aligned_no_divergence(self):
        with mock.patch("utility.wema.get_balance", return_value=_bank("5000")):
            out, _err, alert_mock, code = self._run()
        self.assertIn("0 over / 0 under", out)
        alert_mock.assert_not_called()
        self.assertEqual(code, 0)

    def test_ledger_over_bank_pages(self):
        # Bank holds LESS than our ledger — dangerous (float leak / double-credit).
        with mock.patch("utility.wema.get_balance", return_value=_bank("4000")):
            out, err, alert_mock, code = self._run("--fail-nonzero")
        self.assertIn("1 over", out)
        self.assertIn("OVER", err)
        alert_mock.assert_called_once()
        self.assertEqual(code, 1)

    def test_bank_over_ledger_no_page(self):
        # Bank holds MORE (unswept deposit) — benign: logged, not paged, but still a
        # divergence, so --fail-nonzero trips for investigation.
        with mock.patch("utility.wema.get_balance", return_value=_bank("6000")):
            out, _err, alert_mock, code = self._run("--fail-nonzero")
        self.assertIn("1 under", out)
        alert_mock.assert_not_called()
        self.assertEqual(code, 1)

    def test_fail_over_trips_on_over(self):
        # --fail-over is the cron flag: the dangerous ledger>bank direction exits 1.
        with mock.patch("utility.wema.get_balance", return_value=_bank("4000")):
            _out, _err, _alert, code = self._run("--fail-over")
        self.assertEqual(code, 1)

    def test_fail_over_ignores_benign_under(self):
        # A benign unswept-deposit divergence must NOT fail the cron under --fail-over.
        with mock.patch("utility.wema.get_balance", return_value=_bank("6000")):
            out, _err, alert_mock, code = self._run("--fail-over")
        self.assertIn("1 under", out)
        alert_mock.assert_not_called()
        self.assertEqual(code, 0)

    def test_tolerance_absorbs_small_delta(self):
        with mock.patch("utility.wema.get_balance", return_value=_bank("4999.50")):
            out, _err, alert_mock, code = self._run("--tolerance=1.00", "--fail-nonzero")
        self.assertIn("0 over / 0 under", out)
        alert_mock.assert_not_called()
        self.assertEqual(code, 0)

    def test_skips_when_not_live(self):
        out, err = StringIO(), StringIO()
        with mock.patch("utility.wema.wema_live", return_value=False), \
             mock.patch("utility.wema.get_balance") as gb:
            call_command("reconcile_balances", stdout=out, stderr=err)
        self.assertIn("not live", out.getvalue().lower())
        gb.assert_not_called()
