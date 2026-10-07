"""Short-lived, authenticated-encrypted identity input for one-entry VAS setup.

Only ciphertext enters the shared cache. The database retains an opaque handle;
the identifier is recovered only inside its original, still-authorized session.
Cache expiry bounds retention even when a customer abandons the form.
"""
import hmac
import json
import logging
import math
import re
import secrets

from django.core.cache import cache
from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from accounts.models import hash_identifier
from common.ratelimit import opaque_cache_identifier
from wema_vas.identity import cipher

from .models import PendingAction

FIELD = "identity_capsule"
MAX_TTL = 15 * 60
PURPOSE = "whatsapp.vas.identity.v1"
log = logging.getLogger("zitch.security")


class CapsuleUnavailable(Exception):
    """A private entry cannot safely be recovered; never include input details."""


def _key(reference):
    if not isinstance(reference, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32}", reference):
        raise CapsuleUnavailable()
    return "wa-vas-identity:" + opaque_cache_identifier("wa-vas-identity", reference)


def _binding(pa):
    payload = pa.payload
    return {
        "purpose": PURPOSE, "action": pa.pk, "user": pa.user_id,
        "expires": pa.expires_at.isoformat(),
        **{key: payload.get(key) for key in (
            "nonce", "credentials", "link_id", "flow_id", "contract", "mode",
            "consent_version", "consent_at", "id_kind", "identity_hash",
        )},
    }


@sensitive_variables()
def store(pa, number):
    """Arm encrypted recovery before any paid lookup or code delivery."""
    ttl = min(MAX_TTL, math.floor((pa.expires_at - timezone.now()).total_seconds()))
    if (ttl < 1 or pa.payload.get("consent") is not True
            or pa.payload.get("id_kind") not in {"bvn", "nin"}
            or not isinstance(number, str) or not re.fullmatch(r"[0-9]{11}", number)
            or not hmac.compare_digest(hash_identifier(number), pa.payload.get("identity_hash", ""))):
        raise CapsuleUnavailable()
    reference = secrets.token_urlsafe(24)
    try:
        encrypted = cipher().encrypt(json.dumps({"binding": _binding(pa), "number": number},
            separators=(",", ":")).encode()).decode("ascii")
        if not cache.add(_key(reference), encrypted, timeout=ttl):
            raise CapsuleUnavailable()
    except Exception:
        raise CapsuleUnavailable() from None
    pa.payload[FIELD] = reference


@sensitive_variables()
def recover(pa):
    """Authenticate the envelope and its entire session before returning input."""
    if pa.expired or pa.payload.get("consent") is not True:
        raise CapsuleUnavailable()
    try:
        encrypted = cache.get(_key(pa.payload.get(FIELD)))
        if not isinstance(encrypted, str) or len(encrypted) > 8192:
            raise CapsuleUnavailable()
        value = json.loads(cipher().decrypt_at_time(encrypted.encode("ascii"), ttl=MAX_TTL,
            current_time=int(timezone.now().timestamp())))
        number = value["number"]
        if (value["binding"] != _binding(pa) or pa.payload.get("id_kind") not in {"bvn", "nin"}
                or not isinstance(number, str) or not re.fullmatch(r"[0-9]{11}", number)
                or not hmac.compare_digest(hash_identifier(number), pa.payload.get("identity_hash", ""))):
            raise CapsuleUnavailable()
    except Exception:
        raise CapsuleUnavailable() from None
    return number


def discard(pa):
    """Delete after commit; cache expiry remains the fallback during an outage."""
    try:
        key = _key(pa.payload.get(FIELD))
    except CapsuleUnavailable:
        return

    def cleanup():
        try:
            cache.delete(key)
        except Exception:
            log.warning("wa_vas_identity_cleanup_failed category=cache_unavailable")

    transaction.on_commit(cleanup)


@receiver(post_delete, sender=PendingAction, dispatch_uid="whatsapp.vas_capsule.cleanup")
def _pending_action_deleted(sender, instance, **kwargs):
    if instance.action_type == "vas_enroll":
        discard(instance)
