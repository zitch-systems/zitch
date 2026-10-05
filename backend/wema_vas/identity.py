"""Encrypted identifiers only; callers must first prove identity and consent."""
import json
import re

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.core.exceptions import ImproperlyConfigured

from .config import config


def cipher():
    keys = config().get("IDENTITY_KEYS", [])
    if isinstance(keys, str):
        keys = keys.split(",")
    try:
        return MultiFernet([Fernet(key.strip().encode("ascii")) for key in keys if key.strip()])
    except (ValueError, TypeError, AttributeError, UnicodeError) as exc:
        raise ImproperlyConfigured("VAS identity encryption keys are not configured") from exc


def validate_identity(bvn, nin, phone):
    if not all(isinstance(value, str) for value in (bvn, nin, phone)):
        raise ValueError("Invalid identity fields")
    if not ((not bvn or re.fullmatch(r"[0-9]{11}", bvn))
            and (not nin or re.fullmatch(r"[0-9]{11}", nin)) and (bvn or nin)
            and re.fullmatch(r"[0-9]{10,15}", phone)):
        raise ValueError("A verified BVN or NIN and mobile number are required")


def encrypt_identity(*, bvn="", nin="", phone):
    validate_identity(bvn, nin, phone)
    return cipher().encrypt(json.dumps({"bvn": bvn, "nin": nin, "phone": phone}).encode()).decode("ascii")


def decrypt_identity(value):
    try:
        result = json.loads(cipher().decrypt(value.encode("ascii")))
        validate_identity(result["bvn"], result["nin"], result["phone"])
    except (InvalidToken, ValueError, TypeError, KeyError, AttributeError, UnicodeError) as exc:
        raise ImproperlyConfigured("VAS identity unavailable") from exc
    return result
