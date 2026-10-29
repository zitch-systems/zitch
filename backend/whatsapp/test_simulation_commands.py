"""No-CLI WhatsApp commands for a deliberately simulated deployment.

The linked WhatsApp number identifies the only account the commands may change.
Both simulation switches must be on; turning WEMA_SIMULATION off makes every
command inert. Interactive test digits never leave Zitch and are not used to
derive the identity hashes stored on the account.
"""
from banking.test_fixtures import bank_channel_case
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from wallet.models import Transaction
from wallet.services import get_or_create_wallet

from .flows import FLOW_ID_STATE
from .models import PendingAction, WaMessageLog, WhatsAppLink
from .router import handle_inbound


User = get_user_model()
MSISDN = "2348012345678"
PHONE = "08012345678"
_SIM_ON = {"SIMULATION": True}
_SIM_OFF = {"SIMULATION": False}
_REAL_TERMII = {
    "BASE_URL": "https://v3.api.termii.com",
    "API_KEY": "termii-live",
    "SENDER_ID": "Zitch",
    "CHANNEL": "dnd",
}
_REAL_RESEND = {
    "BASE_URL": "https://api.resend.com",
    "API_KEY": "resend-live",
    "FROM_EMAIL": "Zitch <no-reply@send.zitch.ng>",
}
_NO_TEST_OTP = {"PHONE": "", "CODE": ""}
