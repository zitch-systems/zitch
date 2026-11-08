"""Restore both channels while collection liabilities remain isolated."""
import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from accounts.models import IdentityProof, hash_identifier

from utility.providers import partnership_new_business_allowed, _partnership_reference_blocked
from wallet.models import Transaction, Wallet
from wallet.services import (LimitExceeded, biller_source_for_transaction, customer_funding_account,
    debit, settle_or_refund, wallet_balance_payload, wallet_expected_balance)
from wallet.tests import make_user
from wallet.partnership import first_partnership_setup, legacy_funding_blockers
from wallet.funding_test_fixtures import funding_hold
from whatsapp import router
