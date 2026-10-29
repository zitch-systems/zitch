from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from accounts.models import AccessToken, User
from wallet.models import WemaFaceSession
from wallet.services import attach_existing_bank_account, get_or_create_wallet
from wallet.wema_callbacks import _record_face_account_outcome
