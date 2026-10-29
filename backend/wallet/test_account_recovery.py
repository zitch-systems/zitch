from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import AccessToken, User, hash_identifier
from utility import wema
from wallet.account_recovery import active_account_recovery, prepare_account_recovery
from wallet.models import WemaFaceSession, WemaProvisioningAttempt
from wallet.services import get_or_create_wallet
from wallet.views import _account_setup_state
from whatsapp.models import AuditLog, PendingAction


FACE = {"KEYS": {"wallet": "k", "face_account": "f"}, "CHANNEL_ID": "c",
        "BASE_URL": "https://bank.example", "SIMULATION": False,
        "FACE_VERIFY_URL": "https://face.example/", "FACE_CB_MODE": "registered",
        "FACE_CALLBACK_IPS": ["9.9.9.9"], "FACE_CALLBACK_ORIGINS": ["https://face.example"],
        "CALLBACK_TOKEN": "test-token"}
ABSENT = {"success": False, "account_number": "", "account_lookup_state": "not_found", "http_status": 200}
BVN = "22222222222"
