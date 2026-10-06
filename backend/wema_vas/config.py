"""Fail-closed configuration for the separate bank-to-Zitch VAS contract."""
import re

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connection


def config():
    return getattr(settings, "WEMA_VAS", {})


def approval_reference_present(value):
    """Require an evidence reference, without treating a setting as verification."""
    return (isinstance(value, str) and bool(value.strip()) and len(value) <= 256
            and not any(ord(char) < 32 for char in value)
            and value.strip().casefold() not in {"pending", "todo", "tbd", "none", "false", "true"})


def enrollment_release_policy(values=None):
    """Validate customer release policy independently of bank callback readiness.

    Closing enrollment, emptying the pilot, or a malformed allowlist must never
    disable authenticated notifications for accounts the bank already accepted.
    Missing phase defaults closed, including deployments with the old enable flag.
    """
    values = config() if values is None else values
    phase = values.get("RELEASE_PHASE", "closed")
    errors = []
    if phase not in ("closed", "pilot", "general"):
        errors.append("VAS release phase must be closed, pilot or general.")
    raw_ids = values.get("PILOT_USER_IDS", [])
    if isinstance(raw_ids, str):
        raw_ids = raw_ids.split(",") if raw_ids.strip() else []
    ids = set()
    if not isinstance(raw_ids, (list, tuple, set, frozenset)):
        errors.append("VAS pilot user IDs must be a list of positive integer IDs.")
    else:
        for value in raw_ids:
            if (isinstance(value, bool) or not isinstance(value, (int, str))
                    or not re.fullmatch(r"[1-9][0-9]{0,18}", str(value).strip())
                    or int(str(value).strip()) > 9223372036854775807):
                errors.append("VAS pilot user IDs must contain only positive integer IDs.")
                break
            ids.add(int(str(value).strip()))
    if phase == "general" and not approval_reference_present(values.get("GENERAL_APPROVAL_REFERENCE")):
        errors.append("General VAS enrollment requires separate launch approval evidence.")
    return {"phase": phase, "pilot_user_ids": frozenset(ids), "errors": tuple(errors)}


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
