"""Tests for settlement_report — the whole-float position across every asset rail.

Since VAS moved to the partner bank there are exactly two pots: the customer NUBANs
and the pool account. The invariants pinned here are (a) the pool counts toward what
we hold even though no per-wallet check ever reads it, (b) a rail we could not read
makes the position unavailable rather than inventing a shortfall out of an outage, and
(c) VAS spend now debits the buyer's OWN NUBAN, so it drops what we hold and what we
owe together and leaves the position untouched — which is precisely why the old
third rail, an externally-held VAS float with a standing sweep obligation, is gone.
"""
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from wallet.models import Transaction, Wallet
from wallet.services import debit, wema_account_reference
from wallet.tests import make_user

POOL = "9999999999"
NUBAN = "0123456789"


def _balances(mapping):
    """A get_balance stub keyed by account number; anything unmapped is unreadable."""
    def _get(account_number):
        if account_number in mapping:
            value = mapping[account_number]
            if isinstance(value, dict):
                return value
            return {"success": True, "balance_naira": Decimal(value)}
        return {"success": False, "message": "unreachable"}
    return _get
