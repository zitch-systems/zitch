"""Disabled savings only no-ops when no unpaid obligations exist."""
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone

from savings.models import FixedSave
from wallet.models import Wallet
from wallet.tests import make_user
from whatsapp.models import AuditLog


class DisabledMaturityCommandTests(TestCase):
    def setUp(self):
        self.user, _ = make_user("08033331889", "maturity-command@example.test")

    def run_disabled(self):
        with patch("savings.management.commands.run_maturities.product_available", return_value=False), \
             patch("savings.management.commands.run_maturities.run_maturities") as settle:
            try:
                call_command("run_maturities", stdout=StringIO())
            finally:
                settle.assert_not_called()

    def test_disabled_empty_product_succeeds_with_explicit_audit(self):
        self.run_disabled()
        self.assertEqual(AuditLog.objects.get(action="recon.maturities_run").after,
                         {"paid_out": 0, "product_disabled": True, "unpaid_plans": 0})

    def test_unpaid_plans_remain_failed_even_if_future_or_marked_matured(self):
        plan = FixedSave.objects.create(user=self.user, principal=1000, interest=10,
            rate=Decimal("0.12"), duration_days=30, reference="maturity-audit",
            matures_at=timezone.now() + timedelta(days=5), status=FixedSave.MATURED)
        balance = Wallet.objects.get(user=self.user).balance
        with self.assertRaisesMessage(CommandError, "unpaid plans"):
            self.run_disabled()
        plan.refresh_from_db()
        self.assertFalse(plan.paid_out)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, balance)
        self.assertEqual(AuditLog.objects.get(action="recon.maturities_run").after["unpaid_plans"], 1)
