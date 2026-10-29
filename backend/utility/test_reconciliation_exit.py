"""Scheduled recovery must expose incomplete work without stopping other phases."""
from io import StringIO
from datetime import timedelta
from unittest.mock import patch
from types import SimpleNamespace

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from wallet.models import Wallet, WemaProvisioningAttempt
from wallet.tests import make_user
from whatsapp.models import AuditLog
