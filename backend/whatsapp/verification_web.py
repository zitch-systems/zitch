"""PIN-gated, action-scoped mobile web verification; never an API session.

Router contract: ``start_verification(user, msisdn, 2 | 3) -> https URL``.
An inactive/mismatched link raises ValueError. A new link retires earlier web
verification actions for that phone. Other WhatsApp actions are untouched.

Tier 3 calls accounts.views.verify_kyc_address, the service shared with the
kyc_address API, retaining its validation/provider decisions and shared rate limit.
The API's JSON/bearer
transport is replaced ONLY here by a bounded form and action-scoped PIN proof.
No AccessToken is issued, and no caller can choose an endpoint or account.

Tier 2 deliberately fails closed: FaceLivenessModal is native camera capture;
kyc_verify_face requires explicit provider liveness evidence. Neither supplies a
browser/provider liveness session attested to this action. Wema's hosted Tier-1
ownership flow is not a substitute. Do not call wema_wallet_upgrade_tier2 with a
browser-uploaded still, or expose BVN/NIN inputs, until that contract exists.
"""
import base64
import hashlib
import hmac
import io
import json
import re
import secrets
import warnings
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ImproperlyConfigured, RequestDataTooBig, SuspiciousOperation
from django.db import transaction
from django.http import HttpResponseRedirect
from django.middleware.csrf import rotate_token
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.views.decorators.csrf import csrf_exempt, csrf_protect, ensure_csrf_cookie
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from PIL import Image, UnidentifiedImageError

from common.http import evaluate_transaction_pin
from common.ratelimit import ratelimit

from .models import PendingAction, WhatsAppLink

TTL_SECONDS = 15 * 60
ACTION_TYPE = "verification_web"
PIN, READY = "web_pin", "web_ready"
PROCESSING, COMPLETE, REVIEW = "web_processing", "web_complete", "web_review"
SALT = "whatsapp.verification.web.v1"
MAX_IMAGE_BYTES = 2_000_000
MAX_REQUEST_BYTES = MAX_IMAGE_BYTES + 32_768
ADDRESS_FIELDS = {
    "buildingNumber": 24, "street": 100, "city": 60, "state": 60,
    "lga": 60, "landmark": 100, "postalCode": 12,
}
REQUIRED_ADDRESS_FIELDS = {"buildingNumber", "street", "city", "state", "lga"}
UNAVAILABLE = (
    "Tier 2 needs your BVN, NIN and a provider-verified live face check. "
    "That live check is not available in this browser yet. Your verification "
    "has not changed. Return to WhatsApp for help with the live verification step."
)


def _public_origin():
    value = (getattr(settings, "ZITCH_LINKS", {}).get("API_BASE") or "").rstrip("/")
    parts = urlsplit(value)
    if (parts.scheme != "https" or not parts.netloc or parts.username or parts.password
            or parts.path or parts.query or parts.fragment):
        raise ImproperlyConfigured("ZITCH_LINKS.API_BASE must be an HTTPS origin")
    return value


def _credential_stamp(user):
    # Revokes web grants after a phone, login password or transaction PIN change.
    value = json.dumps([user.pk, user.phone, user.password, user.transaction_pin])
    return salted_hmac(SALT + ".credentials", value, algorithm="sha256").hexdigest()


def _link_stamp(link):
    return [link.created.isoformat(), link.linked_at.isoformat() if link.linked_at else ""]


def _binding(pa):
    payload = pa.payload
    value = json.dumps([
        pa.pk, pa.user_id, pa.msisdn, pa.action_type, pa.expires_at.isoformat(),
        pa.created.isoformat(), payload.get("tier"), payload.get("link_id"),
        payload.get("link_stamp"), payload.get("credentials"), payload.get("nonce"),
    ], separators=(",", ":"))
    return salted_hmac(SALT + ".binding", value, algorithm="sha256").hexdigest()


def start_verification(user, msisdn, tier):
    """Create one 15-minute, PIN-gated capability for an already linked phone."""
    origin = _public_origin()
    if isinstance(tier, bool) or str(tier) not in ("2", "3"):
        raise ValueError("Verification tier must be 2 or 3")
    tier = int(tier)
    msisdn = str(msisdn or "").removeprefix("+")
    if not re.fullmatch(r"[1-9][0-9]{7,14}", msisdn):
        raise ValueError("An active WhatsApp link is required")
    with transaction.atomic():
        current = get_user_model().objects.select_for_update().filter(
            pk=user.pk, is_active=True).first()
        link = WhatsAppLink.objects.select_for_update().filter(
            user_id=user.pk, wa_msisdn=msisdn, status=WhatsAppLink.ACTIVE).first()
        if current is None or link is None:
            raise ValueError("An active WhatsApp link is required")
        PendingAction.objects.filter(
            user=current, msisdn=msisdn, action_type=ACTION_TYPE,
            state__in=(PIN, READY),
        ).update(state="web_retired", expires_at=timezone.now())
        pa = PendingAction.objects.create(
            user=current, msisdn=msisdn, action_type=ACTION_TYPE, state=PIN,
            expires_at=timezone.now() + timedelta(seconds=TTL_SECONDS),
            payload={"tier": tier, "link_id": link.pk, "link_stamp": _link_stamp(link),
                     "credentials": _credential_stamp(current),
                     "nonce": secrets.token_hex(32)},
        )
        # The URL carries an opaque action id and MAC, never a phone, identity,
        # credential or an app/API bearer token. Binding data remains server-side.
        token = signing.dumps({"p": pa.pk, "b": _binding(pa)}, salt=SALT)
    return origin + reverse("whatsapp_verification", kwargs={"token": token})


def _decode(token):
    if not isinstance(token, str) or len(token) > 512:
        return None
    try:
        value = signing.loads(token, salt=SALT, max_age=TTL_SECONDS)
        if (isinstance(value, dict) and type(value.get("p")) is int
                and 0 < value["p"] < 2**63 and isinstance(value.get("b"), str)
                and len(value["b"]) == 64):
            return value
    except (signing.BadSignature, ValueError, TypeError):
        pass
    return None


def _resolve(claims, *, lock=False):
    """Called inside an atomic block when mutating: user -> link -> action locks."""
    if claims is None:
        return None, None
    pa = PendingAction.objects.filter(pk=claims["p"], action_type=ACTION_TYPE).first()
    if pa is None or not isinstance(pa.payload, dict):
        return None, None
    users, links, actions = get_user_model().objects, WhatsAppLink.objects, PendingAction.objects
    if lock:
        users, links, actions = (users.select_for_update(), links.select_for_update(),
                                actions.select_for_update())
    user = users.filter(pk=pa.user_id, is_active=True).first()
    link = links.filter(pk=pa.payload.get("link_id"), user_id=pa.user_id,
                        wa_msisdn=pa.msisdn, status=WhatsAppLink.ACTIVE).first()
    if lock:
        pa = actions.filter(pk=claims["p"], action_type=ACTION_TYPE).first()
    if (not pa or not user or not link or pa.user_id != user.pk or pa.expired
            or pa.state not in (PIN, READY) or pa.payload.get("tier") not in (2, 3)
            or pa.payload.get("link_id") != link.pk or pa.msisdn != link.wa_msisdn
            or pa.payload.get("link_stamp") != _link_stamp(link)
            or not hmac.compare_digest(pa.payload.get("credentials", ""), _credential_stamp(user))
            or not hmac.compare_digest(claims["b"], _binding(pa))):
        return None, None
    return pa, user


def _cookie_name(pa):
    return f"__Secure-wa_verify_{pa.pk}"


def _has_proof(request, pa, user):
    proof = request.COOKIES.get(_cookie_name(pa), "")
    expected = pa.payload.get("browser_proof", "")
    return bool(pa.state == READY and expected and len(proof) == 43
                and not user.pin_locked and not user.pin_reset_required
                and hmac.compare_digest(hashlib.sha256(proof.encode()).hexdigest(), expected))


def _page(request, screen="closed", *, status=200, message="", **context):
    return render(request, "whatsapp/verification_web.html", {
        "screen": screen, "message": message, "nonce": request.verification_nonce,
        **context,
    }, status=status)


def _proof_required():
    # Keep this predicate identical to the shared address service. Re-evaluate
    # for every form display/submission; no client field decides the provider.
    from accounts.views import kyc_provider, wema

    return not (kyc_provider() == "wema" and wema.address_verify_live())


def _form(request, pa, user, *, message="", status=200, values=None):
    if not _has_proof(request, pa, user):
        return _page(request, "pin", status=status, message=message)
    if pa.payload["tier"] == 2:
        return _page(request, "unavailable", status=status, message=UNAVAILABLE)
    if not (user.bvn_verified and user.nin_verified and user.face_verified
            and user.email_verified and user.phone_verified and user.tier >= 2):
        return _page(request, "unavailable", status=409,
                     message="Complete Tier 2 identity verification before verifying your address. "
                             "Return to WhatsApp to continue.")
    return _page(request, "address", status=status, message=message,
                 values=values or {}, expires_at=pa.expires_at,
                 proof_required=_proof_required())


@sensitive_variables()
def _address_data(request):
    allowed = {"action", "csrfmiddlewaretoken", *ADDRESS_FIELDS}
    if (set(request.POST) - allowed or set(request.FILES) - {"document"}
            or any(len(request.POST.getlist(k)) != 1 for k in request.POST)
            or len(request.FILES.getlist("document")) > 1):
        return None, {}, "Submit the address form using the fields shown on this page."
    values = {name: request.POST.get(name, "").strip() for name in ADDRESS_FIELDS}
    for name, limit in ADDRESS_FIELDS.items():
        if ((name in REQUIRED_ADDRESS_FIELDS and not values[name])
                or len(values[name]) > limit
                or any(ord(char) < 32 for char in values[name])):
            return None, values, "Enter a building number, street, city, state and LGA within the field limits."
    address = f"{values['buildingNumber']} {values['street']}"
    if len(address) < 6 or len(", ".join([address, values["city"], values["state"]])) > 255:
        return None, values, "Enter a full residential address of at most 255 characters."
    data = {**values, "address": address, "country": "Nigeria"}
    if not _proof_required():
        # A stale document-rail page may still submit a file after configuration
        # changes. Do not read/forward/retain that unnecessary image on the bank rail.
        return data, values, ""
    upload = request.FILES.get("document")
    if upload is None:
        return None, values, "Upload a proof-of-address photo: a utility bill or bank statement."
    if upload.size > MAX_IMAGE_BYTES:
        return None, values, "Choose a JPEG or PNG photo under 2 MB."
    raw = upload.read(MAX_IMAGE_BYTES + 1)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as proof:
                if (proof.format not in ("JPEG", "PNG") or len(raw) > MAX_IMAGE_BYTES
                        or proof.width * proof.height > 20_000_000
                        or getattr(proof, "n_frames", 1) != 1):
                    raise ValueError("Invalid image")
                proof.verify()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError,
            Image.DecompressionBombWarning, Image.DecompressionBombError):
        return None, values, "Choose a readable JPEG or PNG photo under 2 MB."
    # Explicit fields only. Never forward account ids, client verification flags,
    # URLs, bearer credentials, or an arbitrary operation from the request.
    return {**data, "document": base64.b64encode(raw).decode("ascii")}, values, ""


@ratelimit("kyc_address", limit=10, window=600)
@sensitive_variables()
def _run_address_operation(request, user, data):
    """The sole supported backend operation; authorization already passed.

    Same rate-limit scope as accounts.kyc_address; the service retains all KYC
    prerequisites, identity proof and provider checks. No headers, cookies or
    client-supplied authentication are passed to it.
    """
    from accounts.views import verify_kyc_address

    return verify_kyc_address(user, data)


@sensitive_variables()
def _submit(request, claims):
    with transaction.atomic():
        pa, user = _resolve(claims, lock=True)
        if pa is None:
            return _page(request, status=410)
        action = request.POST.get("action", "")
        if action == "pin":
            if (set(request.POST) - {"action", "pin", "csrfmiddlewaretoken"}
                    or request.FILES or any(len(request.POST.getlist(k)) != 1 for k in request.POST)):
                return _page(request, "pin", status=400, message="Enter your transaction PIN.")
            if user.pin_reset_required:
                return _page(request, "pin", status=403,
                             message="Reset your transaction PIN in WhatsApp before continuing.")
            raw_pin = request.POST.get("pin", "")
            # Malformed guesses spend the same cross-channel attempt budget too.
            passed, code, message = evaluate_transaction_pin(user, raw_pin[:128])
            if not passed:
                return _page(request, "pin", status=429 if code == "pin_locked" else 403,
                             message=message)
            proof = secrets.token_urlsafe(32)
            pa.payload["browser_proof"] = hashlib.sha256(proof.encode()).hexdigest()
            pa.state = READY
            pa.save(update_fields=["payload", "state"])
            rotate_token(request)
            response = HttpResponseRedirect(request.path, status=303)
            response.set_cookie(_cookie_name(pa), proof,
                                max_age=max(1, int((pa.expires_at - timezone.now()).total_seconds())),
                                secure=True, httponly=True, samesite="Strict", path=request.path)
            return response
        if not _has_proof(request, pa, user):
            return _page(request, "pin", status=403,
                         message="Enter your transaction PIN to continue.")
        if action == "tier2" and pa.payload["tier"] == 2:
            return _page(request, "unavailable", status=409, message=UNAVAILABLE)
        if action != "address" or pa.payload["tier"] != 3:
            return _page(request, "unavailable", status=400,
                         message="This action is not available from this verification link.")
        if not (user.bvn_verified and user.nin_verified and user.face_verified
                and user.email_verified and user.phone_verified and user.tier >= 2):
            return _form(request, pa, user)
        data, values, error = _address_data(request)
        if error:
            return _form(request, pa, user, status=400, message=error, values=values)
        # Commit the claim BEFORE making a provider call. A timeout/crash must not
        # roll back to READY and allow a second external verification. Conditional
        # update also guards DBs where select_for_update is unavailable (SQLite).
        if not PendingAction.objects.filter(pk=pa.pk, state=READY).update(state=PROCESSING):
            return _page(request, status=409)

    try:
        result = _run_address_operation(request, user, data)
        body = json.loads(result.content)
        user.refresh_from_db()
        verified = (result.status_code == 200 and isinstance(body, dict)
                    and body.get("success") is True and user.address_verified)
    except Exception:  # Do not log exception text, images, identities or provider payloads.
        PendingAction.objects.filter(pk=pa.pk, state=PROCESSING).update(state=REVIEW)
        return _page(request, "review", status=503)
    if verified:
        PendingAction.objects.filter(pk=pa.pk, state=PROCESSING).update(state=COMPLETE)
        response = _page(request, "complete")
        response.delete_cookie(_cookie_name(pa), path=request.path, samesite="Strict")
        return response
    if result.status_code == 202 and isinstance(body, dict) and body.get("pending") is True:
        PendingAction.objects.filter(pk=pa.pk, state=PROCESSING).update(state=REVIEW)
        return _page(request, "pending", status=202)
    if result.status_code in (400, 403, 409, 413, 422, 429):
        # Only an explicit rejection permits correction/retry. Never echo raw
        # provider messages; they may contain identity numbers or uploaded data.
        PendingAction.objects.filter(pk=pa.pk, state=PROCESSING).update(state=READY)
        messages = {
            403: "Verification was refused. Check your account verification status in WhatsApp.",
            409: "Your account is not ready for this step. Check its status in WhatsApp.",
            429: "Too many verification requests. Wait a few minutes before trying again.",
        }
        # Re-resolve after dispatch: unlink/reset/expiry must take effect even on
        # an error page returned by a slow provider.
        current, current_user = _resolve(claims)
        if current is None:
            return _page(request, status=410)
        correction = "We could not verify this address. Check every address field."
        if _proof_required():
            correction += " Use a clear utility bill or bank statement showing your name and address."
        return _form(request, current, current_user, status=result.status_code, values=values,
                     message=messages.get(result.status_code, correction))
    PendingAction.objects.filter(pk=pa.pk, state=PROCESSING).update(state=REVIEW)
    return _page(request, "review", status=503)


@csrf_protect
@ensure_csrf_cookie
@ratelimit("wa_verification_web", limit=60, window=300)
def _protected(request, token):
    claims = _decode(token)
    if request.method == "POST":
        return _submit(request, claims)
    pa, user = _resolve(claims)
    if pa is None:
        return _page(request, status=410)
    return _form(request, pa, user)


@sensitive_post_parameters()
@csrf_exempt
def verification_page(request, token):
    """GET + POST on one resource. Inner csrf_protect is mandatory.

    Defer global middleware CSRF handling to _protected so even CSRF rejections
    get this outer security-header envelope. This is NOT a CSRF exemption: the
    inner standard Django middleware checks tokens/cookies and secure origins.
    """
    request.verification_nonce = secrets.token_urlsafe(24)
    try:
        if request.method not in ("GET", "POST"):
            response = _page(request, status=405)
            response["Allow"] = "GET, POST"
        elif not request.is_secure() or request.build_absolute_uri("/").rstrip("/") != _public_origin():
            response = _page(request, status=403)
        elif request.method == "POST" and (
            request.headers.get("Origin") != _public_origin()
            or request.headers.get("Sec-Fetch-Site") == "cross-site"
        ):
            response = _page(request, status=403)
        elif request.method == "POST" and int(request.META.get("CONTENT_LENGTH") or 0) > MAX_REQUEST_BYTES:
            response = _page(request, "error", status=413,
                             message="Choose a JPEG or PNG proof-of-address photo under 2 MB.")
        elif request.method == "POST" and request.content_type not in (
            "application/x-www-form-urlencoded", "multipart/form-data"
        ):
            response = _page(request, "error", status=415, message="Use the verification form to continue.")
        else:
            response = _protected(request, token)
    except (RequestDataTooBig, SuspiciousOperation, ValueError):
        response = _page(request, "error", status=400, message="We could not read this form. Please try again.")
    response["Cache-Control"] = "no-store, private, max-age=0"
    response["Pragma"] = "no-cache"
    response["Referrer-Policy"] = "no-referrer"
    response["X-Content-Type-Options"] = "nosniff"
    response["X-Frame-Options"] = "DENY"
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response["Content-Security-Policy"] = (
        "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'; "
        f"style-src 'nonce-{request.verification_nonce}'; "
        f"script-src 'nonce-{request.verification_nonce}'; connect-src 'self'; "
        "object-src 'none'"
    )
    return response
