"""Private Prembly ownership verification, independent of VAS allocation.

Only the ownership-code consumer can claim an identity or record proof. Lookup
keeps a keyed candidate hash on the pending action, never raw identity/contact
values, and does not depend on a validation-account invitation.
"""
import hmac
import json
import logging
import re
from contextlib import contextmanager
from time import monotonic

from django.db import transaction
from django.utils.crypto import salted_hmac
from django.views.decorators.debug import sensitive_variables
from urllib3.util import Timeout

from accounts.models import User, hash_identifier

from .models import PendingAction, WhatsAppLink


log = logging.getLogger("zitch.security")
MAX_ATTEMPTS = 3
UNAVAILABLE = "Identity verification could not finish. Return to the chat and try again later."
CHANGED = "This verification changed. Return to the chat and start again."


def identity_deadline():
    # Keep room below Meta's ten-second exchange limit. Requests timeouts bound
    # connection/read waits, not every possible response-body or scheduler delay.
    return monotonic() + 8.5


def identity_timeout(deadline, maximum, *, read=None):
    # Leave at least a second for proof hashing, database work and encryption.
    remaining = deadline - monotonic() - 1.0
    if remaining <= 0.25:
        return None
    total = min(maximum, remaining)
    return Timeout(total=total, connect=min(1, total),
                   read=min(read if read is not None else total, total))


def lookup_failure(result):
    """Only fixed labels may cross the identity-provider logging boundary."""
    if not isinstance(result, dict):
        return "lookup_schema"
    if result.get("mock"):
        return "lookup_mock"
    if result.get("success") is not True:
        return "lookup_rejected" if result.get("invalid") is True else "lookup_unavailable"
    parts = [result.get(key, "") for key in ("first_name", "middle_name", "last_name")]
    if not all(isinstance(part, str) for part in parts) or not " ".join(parts).strip():
        return "lookup_name_missing"
    if not isinstance(result.get("phone"), str) or not result["phone"]:
        return "lookup_phone_missing"
    return ""


def credentials(user):
    value = json.dumps([user.pk, user.phone, user.email, user.password,
                        user.transaction_pin, user.phone_verified, user.email_verified])
    return salted_hmac("whatsapp.vas.identity.credentials.v1", value,
                       algorithm="sha256").hexdigest()


def _credential_link_matches(pa, user):
    payload = pa.payload if isinstance(pa.payload, dict) else {}
    stamp = payload.get("vas_identity_credentials")
    return bool(user.is_active and pa.user_id == user.pk
                and isinstance(stamp, str) and re.fullmatch(r"[a-f0-9]{64}", stamp)
                and hmac.compare_digest(stamp, credentials(user))
                and WhatsAppLink.objects.filter(
                    pk=payload.get("vas_identity_link_id"), user_id=user.pk,
                    wa_msisdn=pa.msisdn, status=WhatsAppLink.ACTIVE).exists())


def contact_bound(pa, user):
    """Contact forms are bound before either contact has been verified."""
    payload = pa.payload if isinstance(pa.payload, dict) else {}
    return bool(pa.action_type == "kyc" and not pa.expired
                and payload.get("vas_contacts") is True
                and _credential_link_matches(pa, user))


def _binding_matches(pa, user):
    payload = pa.payload if isinstance(pa.payload, dict) else {}
    return bool(user.phone_verified and user.email_verified and user.email
                and payload.get("vas_identity") is True
                and _credential_link_matches(pa, user))


def bound(pa, user):
    """Recheck at token resolution, OTP delivery and ownership consumption."""
    from .flows import FLOW_ID_STATE

    return bool(pa.action_type == "kyc" and pa.state == FLOW_ID_STATE
                and not pa.expired and _binding_matches(pa, user))


def _sync(target, current, user):
    target.payload, target.state, target.expires_at = current.payload, current.state, current.expires_at
    target.user = user


def arm_contacts(pa, user, msisdn):
    """Bind a new contact flow; never refresh a previously issued capability."""
    with transaction.atomic():
        current = User.objects.select_for_update().filter(pk=user.pk).first()
        locked = PendingAction.objects.select_for_update().filter(
            pk=pa.pk, user_id=user.pk, msisdn=msisdn, action_type="kyc").first()
        if (current is None or locked is None or locked.expired
                or not current.is_active or not isinstance(locked.payload, dict)):
            return False
        if ("vas_identity_credentials" in locked.payload
                or "vas_identity_link_id" in locked.payload):
            valid = contact_bound(locked, current)
            if valid:
                _sync(pa, locked, current)
            return valid
        if locked.state != "idle":
            return False
        link = WhatsAppLink.objects.filter(
            user=current, wa_msisdn=msisdn, status=WhatsAppLink.ACTIVE).first()
        if link is None:
            return False
        locked.payload.update(vas_contacts=True, vas_identity_credentials=credentials(current),
                              vas_identity_link_id=link.pk)
        locked.save(update_fields=["payload"])
        _sync(pa, locked, current)
    return True


def arm(pa, user, msisdn):
    """Bind before the encrypted form is issued; never adopt a stale challenge."""
    from .flows import FLOW_ID_STATE

    with transaction.atomic():
        current = User.objects.select_for_update().filter(pk=user.pk).first()
        locked = PendingAction.objects.select_for_update().filter(
            pk=pa.pk, user_id=user.pk, msisdn=msisdn, action_type="kyc").first()
        if (current is None or locked is None or locked.expired
                or not current.is_active or not current.phone_verified
                or not current.email_verified or not current.email
                or not isinstance(locked.payload, dict)
                or locked.payload.get("id_kind") not in ("bvn", "nin")):
            return False
        if locked.payload.get("vas_contacts") is True:
            if not contact_bound(locked, current):
                return False
        elif locked.state not in ("idle", FLOW_ID_STATE):
            return False
        if locked.payload.get("vas_identity") is True:
            valid = _binding_matches(locked, current)
            if valid:
                _sync(pa, locked, current)
            return valid
        if locked.payload.get("id_otp_hash") or locked.payload.get("identity_hash"):
            return False
        link = WhatsAppLink.objects.filter(
            user=current, wa_msisdn=msisdn, status=WhatsAppLink.ACTIVE).first()
        if link is None:
            return False
        locked.payload.update(vas_identity=True, vas_identity_credentials=credentials(current),
                              vas_identity_link_id=link.pk, vas_step="identity")
        locked.save(update_fields=["payload"])
        _sync(pa, locked, current)
    return True


@contextmanager
def _locked(pa, user, msisdn):
    from .flows import FLOW_ID_STATE

    with transaction.atomic():
        current = User.objects.select_for_update().filter(pk=user.pk).first()
        locked = PendingAction.objects.select_for_update().filter(
            pk=pa.pk, user_id=user.pk, msisdn=msisdn,
            action_type="kyc", state=FLOW_ID_STATE).first()
        if current is None or locked is None or not bound(locked, current):
            yield None, None
        else:
            yield locked, current


def _outcome(pa, locked, user, digest, outcome, message=""):
    """Remember each paid lookup's verdict so transport retries cannot repeat it."""
    locked.payload.setdefault("vas_identity_results", {})[digest] = outcome
    locked.payload["vas_identity_error"] = message
    locked.payload["vas_step"] = "code" if outcome == "otp" else "identity" if outcome == "invalid" else "stopped"
    locked.save(update_fields=["payload"])
    _sync(pa, locked, user)
    return outcome


def _candidate_unchanged(locked, user, kind, digest):
    return bool(locked.payload.get("id_kind") == kind
                and locked.payload.get("vas_step") == "processing"
                and locked.payload.get("identity_hash") == digest
                and not getattr(user, f"{kind}_verified")
                and hmac.compare_digest(str(locked.payload.get("identity_previous_hash", "")),
                                        getattr(user, f"{kind}_hash", "")))


@sensitive_variables()
def submit(pa, user, msisdn, kind, digits):
    """Lookup once, then arm the shared private OTP; never allocate an account."""
    deadline = identity_deadline()
    if kind not in ("bvn", "nin") or not isinstance(digits, str) or not re.fullmatch(r"[0-9]{11}", digits):
        return "invalid"
    digest = hash_identifier(digits)
    with _locked(pa, user, msisdn) as (locked, current):
        if locked is None or locked.payload.get("id_kind") != kind:
            return "stop"
        if getattr(current, f"{kind}_verified"):
            return "stop"
        if locked.payload.get("vas_step") in ("processing", "code"):
            if locked.payload.get("identity_hash") != digest:
                return "stop"
            _sync(pa, locked, current)
            if locked.payload.get("vas_step") == "processing":
                return "processing"
            return "otp" if locked.payload.get("id_otp_hash") else "stop"
        previous = locked.payload.get("vas_identity_results", {}).get(digest)
        if previous:
            _sync(pa, locked, current)
            return previous if previous in ("fail", "invalid", "stop") else "stop"
        if (int(locked.payload.get("id_bad_attempts") or 0) >= MAX_ATTEMPTS
                or len(locked.payload.get("vas_identity_results", {})) >= MAX_ATTEMPTS):
            return "stop"
        if User.objects.exclude(pk=current.pk).filter(**{f"{kind}_hash": digest}).exists():
            return _outcome(pa, locked, current, digest, "stop", CHANGED)
        locked.payload.update(identity_hash=digest, identity_last4=digits[-4:],
                              identity_previous_hash=getattr(current, f"{kind}_hash", ""),
                              vas_step="processing", vas_identity_error="")
        locked.save(update_fields=["payload"])
        _sync(pa, locked, current)
        user = current

    from utility.providers import _prembly_identity_live, prembly_verify_bvn, prembly_verify_nin

    result = {}
    failure = "lookup_unconfigured"
    try:
        if _prembly_identity_live():
            budget = identity_timeout(deadline, 6, read=5.5)
            if budget is None:
                failure = "lookup_budget_exhausted"
            else:
                lookup = prembly_verify_bvn if kind == "bvn" else prembly_verify_nin
                result = lookup(digits, name=user.get_full_name() or "", timeout=budget)
                failure = lookup_failure(result)
    except Exception as exc:
        failure = "lookup_exception"
        log.warning("wa_identity_lookup_failed error_type=%s", type(exc).__name__)
    if failure:
        log.warning("wa_identity_lookup_failed category=%s", failure)
    if not isinstance(result, dict):
        result = {}
    name_parts = [result.get(key, "") for key in ("first_name", "middle_name", "last_name")]
    name = " ".join(" ".join(name_parts).split()) if all(isinstance(part, str) for part in name_parts) else ""
    phone, email = result.get("phone", ""), result.get("email", "")
    with _locked(pa, user, msisdn) as (locked, current):
        if locked is None or not _candidate_unchanged(locked, current, kind, digest):
            return "stop"
        if User.objects.exclude(pk=current.pk).filter(**{f"{kind}_hash": digest}).exists():
            return _outcome(pa, locked, current, digest, "stop", CHANGED)
        if result.get("invalid") is True and not result.get("mock"):
            attempts = int(locked.payload.get("id_bad_attempts") or 0) + 1
            locked.payload["id_bad_attempts"] = attempts
            return _outcome(pa, locked, current, digest,
                            "stop" if attempts >= MAX_ATTEMPTS else "invalid",
                            "Those identity details could not be confirmed.")
        if (result.get("success") is not True or result.get("mock") or not name
                or not isinstance(phone, str) or not phone):
            return _outcome(pa, locked, current, digest, "fail", UNAVAILABLE)
        _sync(pa, locked, current)
        user = current

    from .router import _kyc_send_identity_otp

    try:
        error = _kyc_send_identity_otp(pa, user, kind, phone, verified_name=name,
            email=email if isinstance(email, str) else "", delivery_deadline=deadline)
    except Exception as exc:
        log.warning("wa_identity_delivery_failed error_type=%s", type(exc).__name__)
        error = UNAVAILABLE
    with _locked(pa, user, msisdn) as (locked, current):
        if locked is None or not _candidate_unchanged(locked, current, kind, digest):
            return "stop"
        if error is None and locked.payload.get("id_otp_hash"):
            return _outcome(pa, locked, current, digest, "otp")
        return _outcome(pa, locked, current, digest, "fail", UNAVAILABLE)
