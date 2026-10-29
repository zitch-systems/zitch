"""Partnership ownership choices and interrupted account setup in WhatsApp."""
from banking.test_fixtures import bank_channel_case
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from wallet.models import WemaFaceSession, WemaProvisioningAttempt
from wallet.services import get_or_create_wallet
from . import router
from .flows import ACCOUNT_OTP, FLOW_ID_STATE, _submit_identity
from .models import PendingAction
from .test_face_kyc import FACE_ON, MSISDN, VERIFIED_BVN, _user
