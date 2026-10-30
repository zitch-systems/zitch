"""Domain-separated encryption for short-lived bank requests and private reports."""
import base64
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _keys():
    materials = getattr(settings, "BANKING_ENCRYPTION_KEYS", []) or [settings.SECRET_KEY]
    return [Fernet(base64.urlsafe_b64encode(hashlib.sha256(
        b"zitch.banking.v1\0" + str(value).encode()).digest())) for value in materials]


def seal(value):
    return _keys()[0].encrypt(json.dumps(value, separators=(",", ":")).encode()).decode()


def unseal(value):
    for key in _keys():
        try:
            return json.loads(key.decrypt(value.encode()))
        except InvalidToken:
            continue
    raise InvalidToken("Banking request cannot be decrypted")
