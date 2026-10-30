"""PIN-gated, action-scoped bank verification for a linked WhatsApp account.

9PSB owns BVN, NIN and face decisions. Prembly is reserved for Tier 3 address
verification. Grants are short-lived, bound to the linked customer and revoked
when credentials change. They never grant a general app/API session.
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
from utility.providers import partnership_new_business_allowed

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
from utility.liveness import TIER2_LIVENESS_UNAVAILABLE as UNAVAILABLE



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
    if isinstance(tier, bool) or str(tier) not in ("1", "2", "3"):
        raise ValueError("Verification tier must be 1, 2 or 3")
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
        if not partnership_new_business_allowed(current):
            raise ValueError("This verification is unavailable for your account")
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


def _resolve(claims, *, lock=False, status_only=False, completing=False):
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
    readable_pending = bool(pa and status_only and pa.state == REVIEW
                            and pa.payload.get("address_pending") is True)
    if (not pa or not user or not link or pa.user_id != user.pk or pa.expired
            or (pa.state not in (PIN, READY) and not readable_pending
                and not (completing and pa.state == PROCESSING)) or pa.payload.get("tier") not in (1, 2, 3)
            or pa.payload.get("link_id") != link.pk or pa.msisdn != link.wa_msisdn
            or pa.payload.get("link_stamp") != _link_stamp(link)
            or not hmac.compare_digest(pa.payload.get("credentials", ""), _credential_stamp(user))
            or not hmac.compare_digest(claims["b"], _binding(pa))):
        return None, None
    # Recheck existing, unsubmitted grants after a lifecycle switch or an
    # individual migration. Retained credentials must not keep the old address
    # form open. Submitted operations resolve outside this capability reader;
    # their processing/review/completion records remain untouched.
    if not partnership_new_business_allowed(user):
        return None, None
    return pa, user


def _cookie_name(pa):
    return f"__Secure-wa_verify_{pa.pk}"


def _has_proof(request, pa, user):
    proof = request.COOKIES.get(_cookie_name(pa), "")
    expected = pa.payload.get("browser_proof", "")
    readable_pending = pa.state == REVIEW and pa.payload.get("address_pending") is True
    return bool((pa.state == READY or readable_pending) and expected and len(proof) == 43
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
    from accounts.views import kyc_provider

    return kyc_provider() != "wema"


def _bank_address_unavailable():
    from accounts.views import kyc_provider, wema

    return kyc_provider() == "wema" and not wema.address_verify_live()


def _pending_page(request):
    from wallet.address_verification import tier3_address_capability

    return _page(request, "pending", status=202, can_refresh=True,
                 needs_review=tier3_address_capability()["tier3_address_available"] is False)


def _form(request, pa, user, *, message="", status=200, values=None, refresh_status=True):
    if not _has_proof(request, pa, user):
        return _page(request, "pin", status=status, message=message)
    from banking.web_verification import context
    return render(request, "whatsapp/bank_verification.html",
        {"nonce": request.verification_nonce, "message": message, **context(user, pa.payload)}, status=status)


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
    action = request.POST.get("action", "")
    with transaction.atomic():
        pa, user = _resolve(claims, lock=True)
        if pa is None:
            return _page(request, status=410)
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
            return _page(request, "pin", status=403, message="Enter your transaction PIN to continue.")
        if (any(len(request.POST.getlist(k)) != 1 for k in request.POST)
                or any(len(request.FILES.getlist(k)) != 1 for k in request.FILES)):
            return _page(request, "error", status=400, message="Use one value for each field.")
        from banking.web_verification import validate_fields, OPERATION_LIMITS
        try:
            validate_fields(action, request.POST, request.FILES)
        except ValueError as exc:
            return _form(request, pa, user, message=str(exc), status=400)
        if action in OPERATION_LIMITS:
            scope, limit, window = OPERATION_LIMITS[action]
            if ratelimit(scope, limit, window)(lambda _request: None)(request) is not None:
                return _form(request, pa, user, status=429,
                             message="Too many requests. Please wait before trying again.")
        # Claim the browser action before leaving the transaction for bank I/O.
        # Chat cancellation and a second POST cannot re-enter this operation.
        pa.state = PROCESSING
        pa.save(update_fields=["state"])
    from banking.web_verification import submit
    from banking.client import ProviderError
    status, uncertain = 200, False
    try:
        result = submit(user, action, request.POST, request.FILES, pa.payload)
        message = result.get("message") or ("Verification completed." if result.get("success") else "Your bank verification is processing.")
    except ValueError as exc:
        result, message = {}, str(exc)
        status = 400
    except ProviderError:
        result, message = {}, "The service could not confirm the result. Check status before submitting again."
        status = 503
    except Exception:
        result, message = {}, "We could not confirm this verification. Please contact support before trying again."
        status, uncertain = 503, True
    with transaction.atomic():
        current, owner = _resolve(claims, lock=True, completing=True)
        if not current:
            return _page(request, status=410)
        current.state = READY
        if not _has_proof(request, current, owner):
            return _page(request, status=410)
        for key in ("identity_reference", "address_reference"):
            if result.get(key):
                current.payload[key] = result[key]
        current.state = REVIEW if uncertain else READY
        current.save(update_fields=["payload", "state"])
    if uncertain:
        return _page(request, "error", status=status, message=message)
    return _form(request, current, owner, message=message, status=status)


@csrf_protect
@ensure_csrf_cookie
@ratelimit("wa_verification_web", limit=60, window=300)
def _protected(request, token):
    claims = _decode(token)
    if request.method == "POST":
        return _submit(request, claims)
    pa, user = _resolve(claims, status_only=True)
    if pa is None:
        return _page(request, status=410)
    authenticated = _has_proof(request, pa, user)
    response = _form(request, pa, user)
    if authenticated:
        current, current_user = _resolve(claims, status_only=True)
        if current is None or not _has_proof(request, current, current_user):
            return _page(request, status=410)
    return response


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
