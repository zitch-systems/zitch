"""Session ownership and evidence gates; all provider traffic is mocked."""
from copy import deepcopy
from datetime import timedelta
from unittest.mock import Mock, patch

import requests
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import AccessToken, IdentityProof, PremblyFaceSession, User, record_identity_proof
from utility.liveness import start_tier2_liveness, verify_tier2_liveness
from wallet.services import get_or_create_wallet

CONFIG = {"API_KEY": "secret-test", "FACE_WIDGET_KEY": "public-test", "FACE_WIDGET_ID": "face-only",
          "FACE_SDK_ENABLED": True, "ORGANISATION_ID": "org-test"}
