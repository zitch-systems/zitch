from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from utility.reconciliation import alert_due, claim_status_lookup
from wallet.models import Transaction, Wallet
from wallet.services import debit, settle_or_refund
from wallet.tests import make_user
