"""The bank face check, in chat.

The chat can only ever hand over a link. The outcome arrives on our own callback,
so nothing in this module may mark anybody verified — a customer who opens the
bank's page and closes it is exactly as unverified as one who never tapped.

The other property under test is that the identity number never lands in the
thread: it is collected in the encrypted Flow, and where there is no Flow the step
is skipped rather than asked for in clear.
"""
from banking.test_fixtures import bank_channel_case
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from wallet.models import WemaFaceSession
from wallet.services import get_or_create_wallet

from . import router
from .models import PendingAction, WhatsAppLink

User = get_user_model()
MSISDN = "2348011113333"

FACE_ON = {"KEYS": {"wallet": "k"}, "CHANNEL_ID": "c", "SIMULATION": False,
           "FACE_VERIFY_URL": "https://face.example/", "CALLBACK_TOKEN": "tok",
           "CALLBACK_TOKEN_PREV": "", "CALLBACK_ENFORCE_IPS": False, "CALLBACK_IPS": []}
# The face rail needs the channel id (x_tk) and a base URL — see utility.wema._face_key.
FACE_OFF = {**FACE_ON, "CHANNEL_ID": "", "FACE_VERIFY_URL": ""}


#: The identity this test account has actually PROVEN. The face session must bind
#: to it — see accounts.views.face_identity_error.
VERIFIED_BVN = "22222222222"


def _user(**flags):
    from accounts.models import hash_identifier
    u = User.objects.create(username="f1", phone="08010000009", email="f@z.ng",
                            first_name="Ada", last_name="Eze", tier=1,
                            email_verified=True, phone_verified=True,
                            bvn_verified=True, nin_verified=True,
                            bvn_hash=hash_identifier(VERIFIED_BVN), **flags)
    u.save()
    get_or_create_wallet(u)
    WhatsAppLink.objects.create(user=u, wa_msisdn=MSISDN, status=WhatsAppLink.ACTIVE)
    return u
