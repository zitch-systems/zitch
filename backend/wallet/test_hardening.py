"""Tests for the enterprise-hardening controls: ledger<->balance reconciliation
(integrity_check) and the fraud velocity guard (common.http.check_velocity)."""
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings

from common.http import check_velocity
from wallet.models import Transaction, Wallet
from wallet.services import credit, debit
from wallet.tests import make_user


class IntegrityCheckTests(TestCase):
    def setUp(self):
        self.user, _ = make_user("08031110001", "ic@zitch.app", balance="5000")

    def _run(self):
        out, err = StringIO(), StringIO()
        call_command("integrity_check", stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def test_clean_ledger_reconciles(self):
        # Seed credit (5000) + a normal debit and credit keep ledger == balance.
        debit(self.user, Decimal("1200"), "Airtime — MTN")
        credit(self.user, Decimal("300"), "Wallet top-up")
        out, err = self._run()
        self.assertIn("0 mismatch(es)", out)
        self.assertEqual(err, "")

    def test_detects_balance_drift(self):
        # Corrupt the stored balance without a ledger row — must be flagged.
        Wallet.objects.filter(user=self.user).update(balance=Decimal("9999.99"))
        out, err = self._run()
        self.assertIn("1 mismatch(es)", out)
        self.assertIn("MISMATCH", err)

    def test_failed_debit_refund_still_reconciles(self):
        from wallet.services import refund
        txn = debit(self.user, Decimal("1000"), "Transfer to ADA")
        refund(txn)  # money returned, row FAILED — excluded from the OUT sum
        out, _ = self._run()
        self.assertIn("0 mismatch(es)", out)


class VelocityGuardTests(TestCase):
    def setUp(self):
        self.user, _ = make_user("08031110002", "vg@zitch.app", balance="100000")

    @override_settings(VELOCITY_MAX_OUT_10MIN=3)
    def test_blocks_at_cap(self):
        for _ in range(3):
            debit(self.user, Decimal("100"), "Airtime — MTN")
        resp = check_velocity(self.user)
        self.assertIsNotNone(resp)
        self.assertEqual(resp.status_code, 429)

    @override_settings(VELOCITY_MAX_OUT_10MIN=3)
    def test_allows_under_cap(self):
        debit(self.user, Decimal("100"), "Airtime — MTN")
        self.assertIsNone(check_velocity(self.user))

    @override_settings(VELOCITY_MAX_OUT_10MIN=0)
    def test_disabled_when_zero(self):
        for _ in range(5):
            debit(self.user, Decimal("100"), "Airtime — MTN")
        self.assertIsNone(check_velocity(self.user))
