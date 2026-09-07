"""Small application-layer encryption helpers for operator credentials.

Database encryption and Render's encrypted disks remain necessary, but they do not
protect a copied SQL dump or a read-only database credential.  TOTP seeds are
replayable credentials, so keep them encrypted with key material supplied outside the
database.  The first configured key writes; later keys are decrypt-only during a
rotation window.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

PREFIX = "enc:v1:"


def _fernet(material: str) -> Fernet:
    # Fernet needs 32 url-safe bytes.  Domain separation prevents the same Django
    # secret from producing the key used by any other encrypted field.
    digest = hashlib.sha256(b"zitch.operator-totp.v1\0" + material.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _key_materials() -> list[str]:
    configured = getattr(settings, "TOTP_ENCRYPTION_KEYS", None) or []
    if isinstance(configured, str):
        configured = [value.strip() for value in configured.split(",") if value.strip()]
    materials = [str(value) for value in configured if str(value)]
    # Backward-compatible default: Render already pins DJANGO_SECRET_KEY outside the
    # database.  A dedicated key is preferred and can be introduced without downtime
    # by placing it first and retaining the old material second during rotation.
    return materials or [settings.SECRET_KEY]


def is_encrypted(value: str) -> bool:
    return bool(value and value.startswith(PREFIX))


def encrypt_operator_totp(secret: str) -> str:
    if not secret or is_encrypted(secret):
        return secret or ""
    token = _fernet(_key_materials()[0]).encrypt(secret.encode()).decode()
    return PREFIX + token


def decrypt_operator_totp(value: str) -> str:
    if not value:
        return ""
    # Plaintext compatibility is intentionally temporary.  The migration encrypts
    # every current row; this path also lets a rolling deploy read a row written by
    # the old release until migrations complete.
    if not is_encrypted(value):
        return value
    token = value[len(PREFIX):].encode()
    for material in _key_materials():
        try:
            return _fernet(material).decrypt(token).decode()
        except (InvalidToken, UnicodeDecodeError):
            continue
    raise InvalidToken("operator TOTP seed could not be decrypted with any active key")
