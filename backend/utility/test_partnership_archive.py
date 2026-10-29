"""An archived bank rail rejects new work but still resolves accepted work."""
from decimal import Decimal
from unittest import mock

from django.test import SimpleTestCase, override_settings

from utility import wema


LIVE = {
    "BASE_URL": "https://bank.example", "CHANNEL_ID": "test-channel",
    "KEYS": {"wallet": "test-wallet", "face_account": "test-face",
             "upgrade": "test-upgrade", "remita": "test-remita",
             "bnpl": "test-bnpl", "card": "test-card"},
    "BNPL_MERCHANT_ID": "test-merchant", "BNPL_AUTH_KEY": "test-auth",
    "SIMULATION": False, "VAS_STATUS_LEGEND": "1=success 2=failed",
}
