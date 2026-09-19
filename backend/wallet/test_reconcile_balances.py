"""Tests for reconcile_balances — the ledger-vs-bank (Wema NUBAN) integrity check.

Compares each provisioned wallet's ledger balance to the real NUBAN balance.
Neither discrepancy direction changes money automatically: ledger-over-bank is
an immediate float risk, while bank-over-ledger is escalated for provenance
review after the normal funding sweep has had time to catch up. Runs only when
Wema is live; no-ops in simulation/mock.
"""
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.cache import cache
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


class LedgerCurrencyScopeTests(TestCase):
    """wallet_expected_balance feeds two NAIRA comparisons — Wallet.balance
    (integrity_check) and the Wema NUBAN balance (reconcile_balances). An FX row
    summed into it compares two currencies as one number, and a permanently-red
    alarm hides the double-credit it exists to catch."""

    def test_a_foreign_currency_row_does_not_move_the_naira_ledger(self):
        from wallet.models import Transaction
        from wallet.services import wallet_expected_balance

        user, _ = make_user("08010000077", "fx@zitch.test")
        before = wallet_expected_balance(user.id)

        Transaction.objects.create(
            user=user, amount=Decimal("500.00"), direction=Transaction.IN,
            transaction_status=Transaction.SUCCESS, currency="USD",
            service="FX", reference="FX-TEST-1")

        self.assertEqual(wallet_expected_balance(user.id), before,
                         "a USD row must not count toward the naira ledger")


class ReconcileBalancesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
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

    def test_bank_over_ledger_alerts_for_operator_review(self):
        # A six-hour check must surface a bank-ahead discrepancy, but may never
        # make an arithmetic correction or select a payment outcome.
        with mock.patch("utility.wema.get_balance", return_value=_bank("6000")):
            out, _err, alert_mock, code = self._run("--fail-nonzero")
        self.assertIn("1 under", out)
        alert_mock.assert_called_once()
        self.assertIn("bank NUBAN balance exceeds ledger", alert_mock.call_args.args[0])
        self.assertEqual(alert_mock.call_args.kwargs["level"], "error")
        self.assertEqual(code, 1)

    def test_bank_over_ledger_alert_is_rate_limited_until_the_case_changes(self):
        with (
            mock.patch("utility.wema.wema_live", return_value=True),
            mock.patch("utility.wema.get_balance", return_value=_bank("6000")),
            mock.patch("utility.alerts.alert") as alert_mock,
        ):
            call_command("reconcile_balances", stdout=StringIO(), stderr=StringIO())
            call_command("reconcile_balances", stdout=StringIO(), stderr=StringIO())
        alert_mock.assert_called_once()

    def test_fail_over_trips_on_over(self):
        # --fail-over is the cron flag: the dangerous ledger>bank direction exits 1.
        with mock.patch("utility.wema.get_balance", return_value=_bank("4000")):
            _out, _err, _alert, code = self._run("--fail-over")
        self.assertEqual(code, 1)

    def test_fail_over_fails_under_without_changing_money(self):
        # Legacy production cron flags must hold on a bank-ahead discrepancy as
        # well as ledger-over-bank; the escalation remains strictly read-only.
        with mock.patch("utility.wema.get_balance", return_value=_bank("6000")):
            out, _err, alert_mock, code = self._run("--fail-over")
        self.assertIn("1 under", out)
        alert_mock.assert_called_once()
        self.assertEqual(code, 1)

    def test_fail_over_fails_when_all_bank_reads_are_unreachable(self):
        with mock.patch("utility.wema.get_balance",
                        return_value={"success": False, "message": "unreachable"}):
            out, _err, _alert, code = self._run("--fail-over")
        self.assertIn("1 unreachable", out)
        self.assertEqual(code, 1)

    def test_fail_nonzero_fails_when_any_bank_read_is_unreachable(self):
        other_user, _ = make_user("08033330002", "rb2@zitch.app", balance="2000")
        _provision(other_user, account_number="0123456790")

        def balance_for(account_number):
            if account_number == "0123456789":
                return _bank("5000")
            return {"success": False, "message": "unreachable"}

        with mock.patch("utility.wema.get_balance", side_effect=balance_for):
            out, _err, _alert, code = self._run("--fail-nonzero")
        self.assertIn("1 unreachable", out)
        self.assertEqual(code, 1)

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
