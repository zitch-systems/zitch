"""Independent VAS encrypted KYC vault. Keys come from this service only."""
import json
import re

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def cipher():
    try:
        return MultiFernet([Fernet(key.strip().encode("ascii"))
                            for key in settings.VAS_IDENTITY_KEYS.split(",") if key.strip()])
    except (ValueError, TypeError) as exc:
        raise ImproperlyConfigured("VAS identity encryption keys are not configured") from exc


def validate_identity(bvn, nin, phone):
    # Wema's PDF example values differ in length. Actual identity verification
    # belongs to the approved external verification process, not this parser.
    if not (isinstance(bvn, str) and isinstance(nin, str) and isinstance(phone, str)):
        raise ValueError("Invalid identity fields")
    if not ((not bvn or re.fullmatch(r"[0-9]{10,14}", bvn))
            and (not nin or re.fullmatch(r"[0-9]{10,14}", nin))
            and (bvn or nin) and re.fullmatch(r"[0-9]{10,15}", phone)):
        raise ValueError("A verified BVN or NIN and mobile number are required")


def encrypt_identity(*, bvn, nin, phone):
    validate_identity(bvn, nin, phone)
    return cipher().encrypt(json.dumps({"bvn": bvn, "nin": nin, "phone": phone}).encode()).decode("ascii")


def decrypt_identity(value):
    try:
        result = json.loads(cipher().decrypt(value.encode("ascii")))
        validate_identity(result["bvn"], result["nin"], result["phone"])
    except (InvalidToken, ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise ImproperlyConfigured("VAS identity unavailable; do not return partial KYC") from exc
    return result
