"""The partner bank's own tier caps.

ALAT runs its own tier ladder on the NUBAN (Tier 1 = BVN or NIN, Tier 2 = BVN and
NIN, Tier 3 = both plus address verification) with its own inflow / daily-spend /
balance limits, and enforces them regardless of our KYC tier. Where ours is looser,
the gateway refuses the transfer — so we check the bank's ceiling too and turn that
into a clear message instead of a failed payout.
"""
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from common.http import send_limit_error
from utility import wema
from wallet.models import Transaction, Wallet
from wallet.services import bank_spend_error, bank_spent_today, sync_bank_tier
from wallet.tests import make_user
