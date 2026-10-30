"""Accepted OTP -> delayed callback -> saved BVN -> usable airtime, with no replay."""
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import IdentityProof, hash_identifier, rehydrate_verified_identity_flags
from common.http import unverified_error
from whatsapp import router

from .identity import finish_accepted_identity
from .models import Transaction, WemaProvisioningAttempt
from .services import get_or_create_wallet
from .tests import make_user
from .views import complete_wema_provisioning

TOKEN = "async-identity-test-callback"
BVN = "22222222222"
LIVE = {"CHANNEL_ID": "test-channel", "KEYS": {"wallet": "test-wallet"},
        "SIMULATION": False, "CALLBACK_TOKEN": TOKEN, "CALLBACK_ENFORCE_IPS": False,
        "CALLBACK_IPS": ["135.236.18.76"]}
