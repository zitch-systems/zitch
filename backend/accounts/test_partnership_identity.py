"""Tier 1 uses Partnership and historical funding holds cannot change its rail."""
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import AccessToken, IdentityProof, User
from wallet.funding_test_fixtures import funding_hold
from utility import providers
from wallet.models import Wallet
