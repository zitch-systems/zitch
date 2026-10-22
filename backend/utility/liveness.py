"""Server-created Prembly SDK sessions; callbacks and photographs are not proof.

Initiation: https://js.prembly.com/v1/inline/widget-v3.js
Authenticated readback: https://docs.prembly.com/docs/api-webhooks
The bank upgrade has a separate image contract; this adapter never changes
Wallet.bank_tier or exposes biometric images to the legacy upgrade endpoint.
"""
import hashlib
import hmac
import re
import uuid
from datetime import timedelta
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from accounts.models import IdentityProof, PremblyFaceSession, User, hash_identifier

TIER2_LIVENESS_UNAVAILABLE = (
    "Live face verification is temporarily unavailable. Your verified identity "
    "is saved; you do not need to verify NIN after BVN. Please try again later."
)


def tier2_face_available() -> bool:
    config = settings.PREMBLY
    return bool(config.get("FACE_SDK_ENABLED") is True and config.get("API_KEY")
                and config.get("FACE_WIDGET_ID") and config.get("FACE_WIDGET_KEY"))


def _unavailable():
    return {"success": False, "unavailable": True, "code": "tier2_liveness_unavailable",
            "message": TIER2_LIVENESS_UNAVAILABLE}


def _stamp(user):
    return hmac.new(settings.SECRET_KEY.encode(),
                    f"{user.pk}:{user.phone}:{user.password}:{user.transaction_pin}".encode(),
                    hashlib.sha256).hexdigest()


def _identity(user):
    for kind in ("bvn", "nin"):
        digest = getattr(user, f"{kind}_hash")
        if getattr(user, f"{kind}_verified") and digest and IdentityProof.objects.filter(
                user=user, identity_type=kind, identity_hash=digest).exists():
            return kind, digest
    return None


def _bound(session, user):
    return (session.expires_at > timezone.now()
            and user.email_verified and user.phone_verified
            and hmac.compare_digest(session.credential_stamp, _stamp(user))
            and getattr(user, f"{session.identity_type}_verified", False)
            and getattr(user, f"{session.identity_type}_hash", "") == session.identity_hash
            and IdentityProof.objects.filter(user=user, identity_type=session.identity_type,
                                            identity_hash=session.identity_hash).exists())


def _public(session):
    return {"success": True, "session": str(session.state), "status": session.status,
            "url": "https://sdk-live.prembly.com/?" + urlencode({
                "session": session.provider_session_id, "has_callback": "false"})}


def start_tier2_liveness(user):
    if not tier2_face_available():
        return _unavailable()
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user.pk)
        identity = _identity(user)
        if not identity or not (user.email_verified and user.phone_verified):
            return {"success": False, "message": "Verify your contact details and either BVN or NIN first."}
        if identity[0] != "bvn":
            return {"success": False, "message": "The live face workflow currently supports verified BVN. "
                    "Your NIN verification is saved; contact support for the NIN face option."}
        current = PremblyFaceSession.objects.filter(
            user=user, status__in=["starting", "pending"], expires_at__gt=timezone.now()
        ).order_by("-created").first()
        if current and _bound(current, user):
            if current.provider_session_id and current.status == "pending":
                return _public(current)
            return {"success": False, "pending": True,
                    "message": "Your face session is starting. Please check again shortly."}
        session = PremblyFaceSession.objects.create(
            user=user, widget_id=settings.PREMBLY["FACE_WIDGET_ID"],
            identity_type=identity[0], identity_hash=identity[1], credential_stamp=_stamp(user),
            expires_at=timezone.now() + timedelta(minutes=20))
    try:
        response = requests.post(
            "https://backend.prembly.com/api/v1/checker-widget/sdk/sessions/initiate/",
            json={"first_name": user.first_name, "last_name": user.last_name,
                  "email": user.email, "phone": user.phone, "widget_id": session.widget_id,
                  "widget_key": settings.PREMBLY["FACE_WIDGET_KEY"],
                  "metadata": {"zitch_action": str(session.state), "purpose": "tier2_liveness"}},
            timeout=(5, 25), allow_redirects=False)
        body = response.json()
        data = body.get("data") if isinstance(body, dict) else None
        provider_session = data.get("session", data) if isinstance(data, dict) else {}
        provider_id = provider_session.get("session_id") if isinstance(provider_session, dict) else None
        if (response.status_code not in (200, 201) or body.get("status") is not True
                or not isinstance(provider_id, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{8,160}", provider_id)):
            raise ValueError("Session initiation was not confirmed")
        with transaction.atomic():
            user = User.objects.select_for_update().get(pk=user.pk)
            session = PremblyFaceSession.objects.select_for_update().get(pk=session.pk)
            if not _bound(session, user):
                raise ValueError("Session authorization changed")
            session.provider_session_id = provider_id
            session.status = "pending"
            session.save(update_fields=["provider_session_id", "status"])
        return _public(session)
    except (requests.RequestException, ValueError, TypeError, KeyError):
        # Keep uncertain initiations claimed until expiry; do not duplicate charges.
        # Never log credentials, provider payloads or biometrics.
        return {"success": False, "pending": True,
                "message": "We could not confirm your face session. Please check again shortly."}


def _session_result(body, session):
    if not isinstance(body, dict) or body.get("status") is not True:
        return None
    data = body.get("data")
    if not isinstance(data, dict):
        return None
    candidates = data.get("sessions", [data.get("session", data)])
    if not isinstance(candidates, list):
        return None
    matches = [item for item in candidates if isinstance(item, dict)
               and item.get("session_id") == session.provider_session_id]
    return matches[0] if len(matches) == 1 else None


def _verified_identity_matches(data, session):
    """Compare only SDK verification data, never user metadata or profile names.

    Different BVN endpoint versions nest their result in data/response/result.
    Any contradictory identifier rejects the proof. No raw BVN is retained.
    """
    identifiers = []
    def visit(value):
        if not isinstance(value, dict):
            return
        for key, item in value.items():
            if str(key).lower() in (session.identity_type, "number", "identity_number"):
                if isinstance(item, str) and re.fullmatch(r"[0-9]{11}", item):
                    identifiers.append(item)
            elif key in ("data", "response", "result", "verification_response"):
                visit(item)
    visit(data)
    return bool(identifiers and all(hmac.compare_digest(hash_identifier(value), session.identity_hash)
                                   for value in identifiers))


def _face_comparison_passed(data):
    # Published SDK biometric comparison response. Unknown shapes stay pending
    # for review; presence of a selfie or a confidence score alone is not proof.
    if not isinstance(data, dict):
        return False
    comparisons = data.get("comparison_result")
    if not isinstance(comparisons, list) or not comparisons:
        return False
    for comparison in comparisons:
        result = comparison.get("result") if isinstance(comparison, dict) else None
        if not isinstance(result, dict):
            return False
        confidence = result.get("confidence")
        if (result.get("status") is not True or result.get("response_code") != "00"
                or type(confidence) not in (int, float) or not 80 <= confidence <= 100):
            return False
    return True


def verify_tier2_liveness(user, data, *, authorize=None) -> dict:
    if not tier2_face_available():
        return _unavailable()
    if any(key in data for key in ("bvn", "nin", "live_image", "liveImageOfFace", "selfie", "image")):
        return {"success": False, "unavailable": True, "code": "bank_image_contract_unavailable",
                "message": "Use the Prembly live face session. Uploaded photos cannot authorize a bank upgrade."}
    try:
        state = uuid.UUID(str(data.get("session", "")))
    except (ValueError, TypeError, AttributeError):
        return {"success": False, "message": "Start a live Prembly face session to continue."}
    session = PremblyFaceSession.objects.filter(user=user, state=state).first()
    if not session:
        return {"success": False, "message": "Unknown face verification session."}
    if not _bound(session, user):
        return {"success": False, "status": "expired", "message": "Start a new face verification session."}
    if session.status == "verified":
        return {"success": True, "status": "verified"}
    if session.status != "pending" or not session.provider_session_id:
        return {"success": False, "status": session.status, "pending": session.status == "starting",
                "message": "Face verification is not complete."}
    headers = {"x-api-key": settings.PREMBLY["API_KEY"]}
    if settings.PREMBLY.get("ORGANISATION_ID"):
        headers["x-organisation-id"] = settings.PREMBLY["ORGANISATION_ID"]
    try:
        response = requests.get(
            "https://api.prembly.com/api/v1/checker-widget/sdk/sessions/"
            + session.provider_session_id + "/", headers=headers,
            params={"include_payload": "true"}, timeout=(5, 20), allow_redirects=False)
        result = _session_result(response.json(), session) if response.status_code == 200 else None
    except (requests.RequestException, ValueError, TypeError):
        result = None
    if result is None:
        return {"success": False, "pending": True, "status": "pending",
                "message": "Prembly has not confirmed your face check yet. Check again shortly."}
    # These fields are returned by the authenticated SDK session-detail API.
    # The configured workflow requires a live gesture challenge and comparison
    # against its BVN result. Never substitute the still-image liveness API.
    config = result.get("widget_config") or {}
    live = result.get("liveness") or {}
    metadata = result.get("metadata") or {}
    passed = (result.get("status") == "completed" and result.get("overall_result") == "passed"
              and not result.get("failure_reason") and bool(result.get("completed_at"))
              and isinstance(config, dict) and config.get("id") == session.widget_id
              and config.get("enable_face_scan") is True and config.get("use_gesture_liveness") is True
              and isinstance(config.get("metadata"), dict)
              and config["metadata"].get("image_comparison") is True
              and isinstance(live, dict) and live.get("enabled") is True
              and live.get("status") == "completed" and live.get("is_live") is True
              and bool(live.get("process_id")) and not live.get("error")
              and isinstance(metadata, dict) and metadata.get("zitch_action") == str(session.state)
              and metadata.get("purpose") == "tier2_liveness"
              and _verified_identity_matches(result.get("verification_data"), session)
              and _face_comparison_passed(result.get("face_scan_result")))
    if not passed:
        rejected = result.get("status") in ("failed", "cancelled", "expired")
        if rejected:
            PremblyFaceSession.objects.filter(pk=session.pk, status="pending").update(status="failed")
        return {"success": False, "pending": not rejected, "status": "failed" if rejected else "pending",
                "message": "Face check was not confirmed. Retry the check." if rejected else
                           "Your face check is awaiting verified results from Prembly. Check again shortly."}
    with transaction.atomic():
        current_user = User.objects.select_for_update().get(pk=user.pk)
        current = PremblyFaceSession.objects.select_for_update().get(pk=session.pk)
        if (not _bound(current, current_user) or current.status not in ("pending", "verified")
                or (authorize is not None and not authorize())):
            return {"success": False, "status": "expired", "message": "Start a new face verification session."}
        current.status = "verified"
        current.verified_at = timezone.now()
        current.save(update_fields=["status", "verified_at"])
        current_user.face_verified = True
        current_user.recompute_tier()
        current_user.save(update_fields=["face_verified", "tier"])
    user.refresh_from_db()
    return {"success": True, "status": "verified", "message": "Tier 2 face verification complete."}


def refresh_tier2_liveness(user):
    """Explicit app/chat status checks can finish an already authorized session."""
    if user.face_verified or not tier2_face_available():
        return
    pending = PremblyFaceSession.objects.filter(user=user, status="pending",
        expires_at__gt=timezone.now()).order_by("-created").first()
    if pending:
        verify_tier2_liveness(user, {"session": str(pending.state)})
