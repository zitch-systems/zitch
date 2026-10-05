"""Fail-closed configuration for the separate bank-to-Zitch VAS contract."""
import re

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connection


def config():
    return getattr(settings, "WEMA_VAS", {})


def validate_configuration():
    value = config()
    mode, prefix = value.get("MODE", "validation"), value.get("PREFIX", "711")
    if mode not in ("validation", "live") or not re.fullmatch(r"[0-9]{3}", prefix):
        raise ImproperlyConfigured("VAS mode or prefix is not configured")
    if (mode == "validation" and prefix != "711") or (mode == "live" and prefix == "711"):
        raise ImproperlyConfigured("VAS validation and production prefixes must be separate")
    token = value.get("TOKEN", "")
    if not isinstance(token, str) or len(token) < 48 or any(c.isspace() for c in token):
        raise ImproperlyConfigured("VAS authentication is not configured")
    if not getattr(settings, "TESTING", False) and connection.vendor != "postgresql":
        raise ImproperlyConfigured("VAS requires PostgreSQL")
    # Validate keys before opening any of the bank endpoints, including notify.
    from .identity import cipher
    cipher()
    return value
