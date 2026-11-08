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
from django.core.management.base import CommandError
from django.test import TestCase

from wallet.models import Wallet
from wallet.tests import make_user


def _provision(user, account_number="0123456789"):
    Wallet.objects.filter(user=user).update(
        account_reference=f"WEMA-WALLET-{user.id}", account_number=account_number)


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
