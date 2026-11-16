"""PIN-gated, action-scoped mobile web verification; never an API session.

Router contract: ``start_verification(user, msisdn, 2 | 3) -> https URL``.
An inactive/mismatched link raises ValueError. A new link retires earlier web
verification actions for that phone. Other WhatsApp actions are untouched.

Tier 3 calls accounts.views.verify_kyc_address, the service shared with the
kyc_address API, retaining its validation/provider decisions and shared rate limit.
The API's JSON/bearer
transport is replaced ONLY here by a bounded form and action-scoped PIN proof.
No AccessToken is issued, and no caller can choose an endpoint or account.

Tier 2 uses its own link (face_page): a Zitch camera page whose selfie goes
to the bank's Tier 2 upgrade with this account. The bank's read-back, not the
submission, raises the tier. No third-party liveness vendor is involved.
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

from .liveness import liveness_context, secure_camera_headers
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


def _verification_allowed(user, tier):
    """Keep KYC identity proof independent of unresolved banking balances.

    Tier 2 needs a verified Tier 1 identity and a bank account to upgrade; the
    upgrade service itself re-checks the bank's eligibility. This only opens
    the verification page; it NEVER lifts the separate bank funding and spend
    holds. Tier 3 still follows the bank's eligibility/upgrade policy,
    including its global lifecycle switch.
    """
    if tier == 2:
        # Tier 2 upgrades a bank account; with no account there is nothing to
        # upgrade, and the customer would finish liveness for no result.
        from wallet.models import Wallet
        has_account = Wallet.objects.filter(user_id=user.pk).exclude(account_number="").exists()
        return bool(user.is_active and user.tier1_identity_verified and has_account)
    return partnership_new_business_allowed(user)


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
        if not _verification_allowed(current, tier):
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


def _resolve(claims, *, lock=False, status_only=False):
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
            or (pa.state not in (PIN, READY) and not readable_pending) or pa.payload.get("tier") not in (2, 3)
            or pa.payload.get("link_id") != link.pk or pa.msisdn != link.wa_msisdn
            or pa.payload.get("link_stamp") != _link_stamp(link)
            or not hmac.compare_digest(pa.payload.get("credentials", ""), _credential_stamp(user))
            or not hmac.compare_digest(claims["b"], _binding(pa))):
        return None, None
    # Recheck existing, unsubmitted grants after a lifecycle switch or an
    # individual migration. Retained credentials must not keep the old address
    # form open. Submitted operations resolve outside this capability reader;
    # their processing/review/completion records remain untouched.
    if not _verification_allowed(user, pa.payload.get("tier")):
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
        if pa.state == REVIEW:
            return _page(request, status=403)
        return _page(request, "pin", status=status, message=message)
    if pa.payload["tier"] == 2:
        if user.face_verified and user.tier >= 2:
            return _page(request, "face_complete")
        # Older PIN-gated Tier 2 links now hand over to the camera page.
        token = request.path.rstrip("/").rsplit("/", 1)[-1]
        return _page(request, "tier2", status=status, message=message,
                     face_url=reverse("whatsapp_face", kwargs={"token": token}))
    from accounts.views import _kyc_state

    if refresh_status and user.address_verification_pending:
        from wallet.address_verification import refresh_address_verification

        refresh_address_verification(user)
    address_status = (_kyc_state(user) if refresh_status else {
        "address_verified": user.address_verified,
        "address_verification_pending": user.address_verification_pending})
    if address_status.get("address_verified"):
        return _page(request, "complete")
    if address_status.get("address_verification_pending"):
        return _pending_page(request)
    if pa.state == REVIEW:
        return _page(request, "review", status=503)
    from wallet.address_verification import tier3_address_capability

    capability = tier3_address_capability()
    if capability["tier3_address_available"] is False:
        return _page(request, "unavailable", status=503,
                     message=capability["tier3_address_unavailable_reason"])
    if not (user.tier1_identity_verified and user.face_verified
            and user.email_verified and user.phone_verified and user.tier >= 2):
        return _page(request, "unavailable", status=409,
                     message="Complete Tier 2 identity verification before verifying your address. "
                             "Return to WhatsApp to continue.")
    if _bank_address_unavailable():
        return _page(request, "unavailable", status=503,
                     message="Address verification is temporarily unavailable. Your address has not been submitted. "
                             "Please try again later or return to WhatsApp.")
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
    action = request.POST.get("action", "")
    if action in ("tier2", "face_status"):
        # Tier 2 now runs on the camera page (face_page), not on this form.
        return _page(request, "unavailable", status=410,
                     message="Open the Tier 2 link from WhatsApp to take your selfie.")
    if action == "status":
        # Readback may make a bank GET and lock Wallet -> User. Authenticate in
        # a short transaction, then release User/Link/Action before that call.
        with transaction.atomic():
            pa, user = _resolve(claims, lock=True, status_only=True)
            if pa is None:
                return _page(request, status=410)
            if (pa.payload["tier"] != 3
                    or set(request.POST) - {"action", "csrfmiddlewaretoken"} or request.FILES
                    or any(len(request.POST.getlist(k)) != 1 for k in request.POST)):
                return _page(request, "error", status=400,
                             message="Use the status button on this page.")
            if not _has_proof(request, pa, user):
                return _page(request, "pin", status=403,
                             message="Enter your transaction PIN to continue.")
        response = _form(request, pa, user)
        current, current_user = _resolve(claims, status_only=True)
        if current is None or not _has_proof(request, current, current_user):
            return _page(request, status=410)
        return response
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
            return _page(request, "pin", status=403,
                         message="Enter your transaction PIN to continue.")
        if action != "address" or pa.payload["tier"] != 3:
            return _page(request, "unavailable", status=400,
                         message="This action is not available from this verification link.")
        if not (user.tier1_identity_verified and user.face_verified
                and user.email_verified and user.phone_verified and user.tier >= 2):
            return _form(request, pa, user, refresh_status=False)
        if user.address_verified:
            return _page(request, "complete")
        if user.address_verification_pending:
            return _pending_page(request)
        from wallet.address_verification import tier3_address_capability

        if tier3_address_capability()["tier3_address_available"] is False:
            return _form(request, pa, user, refresh_status=False)
        if _bank_address_unavailable():
            return _form(request, pa, user, refresh_status=False)
        data, values, error = _address_data(request)
        if error:
            return _form(request, pa, user, status=400, message=error, values=values, refresh_status=False)
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
        pa.payload["address_pending"] = True
        PendingAction.objects.filter(pk=pa.pk, state=PROCESSING).update(state=REVIEW, payload=pa.payload)
        return _pending_page(request)
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


# --------------------------------------------------------------------------- #
# Tier 2 face link: WhatsApp -> Zitch camera page -> bank. No PIN, no vendor.
# --------------------------------------------------------------------------- #
#: Link-preview fetchers open the URL before the customer taps it. A preview
#: gets the closed page and never reaches the camera or the bank.
_PREVIEW_AGENTS = ("facebookexternalhit", "facebookcatalog", "whatsapp/", "meta-externalagent",
                   "twitterbot", "slackbot", "telegrambot", "discordbot", "bot", "crawler", "spider")
FACE_PROCESSING, FACE_PENDING, FACE_FAILED = "web_face_processing", "web_face_pending", "web_face_failed"
#: A JPEG selfie from the page is ~100-300 KB; base64 adds a third.
MAX_FACE_REQUEST_BYTES = 3_000_000


def _is_preview_fetch(request) -> bool:
    agent = (request.headers.get("User-Agent") or "").lower()
    return not agent or any(marker in agent for marker in _PREVIEW_AGENTS)


def _android_in_app(request) -> bool:
    """WhatsApp's in-app browser on Android is a WebView ("; wv)"), where the
    camera often never opens."""
    agent = request.headers.get("User-Agent") or ""
    return "Android" in agent and "; wv)" in agent


def _chrome_handoff(request, url: str):
    """Reopen this page in Chrome, where the camera works. If the WebView cannot
    follow an intent link, stay here (``?stay=1``) and use the photo picker."""
    from django.http import HttpResponse
    from django.utils.html import escape

    parts = urlsplit(url)
    intent = (f"intent://{parts.netloc}{parts.path}"
              "#Intent;scheme=https;package=com.android.chrome;end")
    fallback = url + "?stay=1"
    nonce = request.verification_nonce
    body = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>Opening camera</title><style nonce='{nonce}'>body{{font-family:sans-serif;"
        "padding:32px;color:#14532d}a{color:#166534}</style></head><body>"
        "<p>Opening the camera…</p>"
        f"<p><a href='{escape(fallback)}'>Tap here if it does not open</a></p>"
        f"<script nonce='{nonce}'>"
        f"location.href={json.dumps(intent)};"
        f"setTimeout(function(){{location.replace({json.dumps(fallback)});}},1500);"
        "</script></body></html>")
    return HttpResponse(body)


def _face_resolve(claims, *, lock=False):
    """Like _resolve, but also reads a face check already submitted from this link."""
    if claims is None:
        return None, None
    actions = PendingAction.objects.select_for_update() if lock else PendingAction.objects
    pa = actions.filter(pk=claims["p"], action_type=ACTION_TYPE).first()
    if pa is None or pa.state not in (FACE_PROCESSING, FACE_PENDING, FACE_FAILED, COMPLETE):
        return _resolve(claims, lock=lock)
    if (not isinstance(pa.payload, dict) or pa.payload.get("tier") != 2
            or not hmac.compare_digest(claims["b"], _binding(pa))):
        return None, None
    user = get_user_model().objects.filter(pk=pa.user_id, is_active=True).first()
    return (pa, user) if user else (None, None)


def _face_get(request, token):
    from wallet import selfie
    from wallet.views import tier2_face_available

    pa, user = _face_resolve(_decode(token))
    if pa is None or pa.payload.get("tier") != 2:
        return _page(request, status=410)
    if (user.face_verified and user.tier >= 2) or pa.state == COMPLETE:
        return _page(request, "face_complete")
    if pa.state in (FACE_PROCESSING, FACE_PENDING):
        return _page(request, "face_pending", status=202)
    if pa.state == FACE_FAILED or pa.expired:
        return _page(request, status=410)
    if not tier2_face_available() or not selfie.face_checks_available():
        return _page(request, "unavailable", status=503, message=FACE_UNAVAILABLE)
    if _is_preview_fetch(request):
        return _page(request, "closed", status=200)
    if _android_in_app(request) and request.GET.get("stay") != "1":
        return _chrome_handoff(request, _public_origin() + request.path)
    return render(request, "whatsapp/face_camera.html", {
        **liveness_context(request.verification_nonce),
        "picker_only": request.GET.get("stay") == "1",
    })


def _json(http_status, **body):
    from django.http import JsonResponse

    return JsonResponse(body, status=http_status)


def _face_link_closed(pa, user):
    """The answer for a link that can no longer take a photo, or None."""
    if pa is None or pa.payload.get("tier") != 2 or pa.state == FACE_FAILED:
        return _json(410, status="closed", message="This link has closed. Reply 8 in WhatsApp for a new one.")
    if pa.state in (FACE_PROCESSING, FACE_PENDING):
        return _json(202, status="pending", message=FACE_PENDING_TEXT)
    if pa.state == COMPLETE or (user.face_verified and user.tier >= 2):
        return _json(200, status="verified", message="Tier 2 is already complete.")
    return None


@sensitive_variables("image", "turns", "bvn", "nin")
def _face_post(request, token):
    """Check the live face here (the straight photo and the head-turn frames),
    then send BVN, NIN and the photo to the bank once (Wema Tier 2). A photo
    the bank could not use, or a turn that did not happen, is refused before
    the link is claimed, so the customer simply tries again. Retry after a
    bank answer only on an explicit refusal. BVN/NIN are never stored or
    logged raw."""
    from wallet import face_check
    from wallet.views import upgrade_wema_identity

    if (request.content_type not in ("application/x-www-form-urlencoded", "multipart/form-data")
            or set(request.POST) - {"image", "turns", "bvn", "nin", "csrfmiddlewaretoken"}
            or request.FILES or any(len(request.POST.getlist(k)) != 1 for k in request.POST)):
        return _json(400, status="retry", field="face", message="Take the photo using this page.")
    # Base64 never contains a comma: "image" is 1-3 straight photos (the
    # server keeps the clearest) and "turns" the head-turn frames.
    image = [frame for frame in request.POST.get("image", "").split(",") if frame]
    turns = [frame for frame in request.POST.get("turns", "").split(",") if frame]
    bvn, nin = request.POST.get("bvn", "").strip(), request.POST.get("nin", "").strip()
    if not (re.fullmatch(r"[0-9]{11}", bvn) and re.fullmatch(r"[0-9]{11}", nin)):
        return _json(400, status="retry", field="ids", message="Enter your 11-digit BVN and NIN.")
    if not turns:
        # A page loaded before the head-turn step existed.
        return _json(400, status="retry", field="face", code="liveness_missing",
                     message=FACE_REFRESH_TEXT)
    if len(image) > face_check.MAX_FRONTAL_FRAMES or len(turns) > face_check.MAX_TURN_FRAMES:
        return _json(400, status="retry", field="face", message="Take the photo using this page.")
    pa, user = _face_resolve(_decode(token))
    closed = _face_link_closed(pa, user)
    if closed:
        return closed
    checked = face_check.check_live_face(image, turns)
    face_check.log_outcome("whatsapp", user, checked)
    if checked.code == "face_check_unavailable":
        return _json(503, status="retry", field="face", code=checked.code, message=checked.message)
    if not checked.ok:
        return _json(422, status="retry", field="face", code=checked.code, message=checked.message)
    with transaction.atomic():
        pa, user = _face_resolve(_decode(token), lock=True)
        closed = _face_link_closed(pa, user)
        if closed:
            return closed
        # Claim before the bank call: a timeout or a double tap must not send
        # the photo twice.
        if not PendingAction.objects.filter(pk=pa.pk, state__in=(PIN, READY)).update(state=FACE_PROCESSING):
            return _json(409, status="pending", message=FACE_PENDING_TEXT)
    try:
        response = upgrade_wema_identity(user, {"bvn": bvn, "nin": nin}, checked=checked.frontal)
        body = json.loads(response.content)
    except Exception:  # Never log the image, identities or provider payloads.
        PendingAction.objects.filter(pk=pa.pk, state=FACE_PROCESSING).update(state=FACE_PENDING)
        return _json(202, status="pending", message=FACE_PENDING_TEXT)
    if response.status_code == 200 and body.get("success") is True:
        _finish_face(pa, "verified")
        return _json(200, status="verified", message="Tier 2 verification complete.")
    if response.status_code == 202:
        PendingAction.objects.filter(pk=pa.pk, state=FACE_PROCESSING).update(state=FACE_PENDING)
        return _json(202, status="pending", message=FACE_PENDING_TEXT)
    # An explicit refusal: allow another try from the same link. Only our own
    # validation messages are shown; bank text may carry account details. A
    # BVN/NIN problem keeps the checked photo; anything else needs a new one.
    PendingAction.objects.filter(pk=pa.pk, state=FACE_PROCESSING).update(state=pa.state)
    own = response.status_code in (400, 409, 429)
    message = (body.get("message") if own else "") or FACE_RETRY_TEXT
    field = "ids" if own and re.search(r"\b(BVN|NIN)\b", message) else "face"
    return _json(response.status_code if own else 422, status="retry", field=field, message=message)


def _finish_face(pa, outcome):
    """Record the outcome on the link and tell the customer in WhatsApp, once."""
    from .router import reply

    with transaction.atomic():
        current = PendingAction.objects.select_for_update().filter(pk=pa.pk).first()
        if current is None or "face_notified" in (current.payload or {}):
            return False
        current.payload["face_notified"] = outcome
        current.state = COMPLETE if outcome == "verified" else FACE_FAILED
        current.save(update_fields=["payload", "state"])
    reply(pa.msisdn, FACE_DONE_TEXT if outcome == "verified" else FACE_FAILED_TEXT)
    return True


@csrf_protect
@ensure_csrf_cookie
@ratelimit("wa_verification_face", limit=30, window=300)
def _face_protected(request, token):
    if request.method == "POST":
        return _face_post(request, token)
    return _face_get(request, token)


@sensitive_post_parameters()
@csrf_exempt
def face_page(request, token):
    """The Tier 2 link WhatsApp sends: a Zitch camera page, then the bank.

    No PIN here, by design. The link is a 15-minute signed capability bound to
    the linked WhatsApp number, the account's current login/PIN stamp and this
    action, and Tier 2 only completes when the bank matches the live photo to
    the BVN/NIN record behind this account and its read-back shows Tier 2.
    Someone holding a forwarded link cannot pass that with their own face.
    This is NOT a CSRF exemption: the inner csrf_protect checks every POST.
    """
    request.verification_nonce = secrets.token_urlsafe(24)
    if request.method not in ("GET", "POST"):
        response = _page(request, status=405)
        response["Allow"] = "GET, POST"
    elif not request.is_secure() or request.build_absolute_uri("/").rstrip("/") != _public_origin():
        response = _page(request, status=403)
    elif request.method == "POST" and (
        request.headers.get("Origin") != _public_origin()
        or request.headers.get("Sec-Fetch-Site") == "cross-site"
    ):
        response = _json(403, status="retry", message="Open this link from WhatsApp.")
    elif request.method == "POST" and int(request.META.get("CONTENT_LENGTH") or 0) > MAX_FACE_REQUEST_BYTES:
        response = _json(413, status="retry", message="That photo is too large. Please take it again.")
    else:
        try:
            response = _face_protected(request, token)
        except (RequestDataTooBig, SuspiciousOperation, ValueError):
            response = _json(400, status="retry", message="We could not read that photo. Please take it again.")
    # Same nonce-only CSP as before, plus 'wasm-unsafe-eval' for the on-device
    # liveness model (self-hosted; see whatsapp/liveness.py).
    return secure_camera_headers(response, request.verification_nonce)


FACE_SWEEP_LIMIT = 20
FACE_UNAVAILABLE = ("Tier 2 face verification is temporarily unavailable. Your verified "
                    "identity is saved. Please try again later.")
FACE_PENDING_TEXT = ("Your bank is checking your photo. We'll message you in WhatsApp "
                     "with the result - you can close this page.")
FACE_RETRY_TEXT = ("Your bank could not confirm your face. Take the photo again in good "
                   "light, facing the camera, with nothing covering your face.")
FACE_REFRESH_TEXT = ("This page has been updated. Refresh it, then take the face check again. "
                     "Your link still works.")
FACE_DONE_TEXT = ("✅ *Tier 2 verification complete.*\n\n"
                  "Your face check passed and your account limits have been upgraded.")
FACE_FAILED_TEXT = ("❌ *Your face check wasn't confirmed.*\n\n"
                    "Nothing changed on your account. Reply *8* to try again in good light.")


def notify_face_results(limit: int = FACE_SWEEP_LIMIT) -> int:
    """Finish face checks the bank was still processing, and tell the customer.

    Only reads back the bank's tier (refresh_tier2_face); the photo is never
    sent again. Returns how many customers were notified."""
    from wallet.views import refresh_tier2_face, tier2_face_pending

    since = timezone.now() - timedelta(hours=2)
    actions = (PendingAction.objects
               .filter(action_type=ACTION_TYPE, created__gte=since, state=FACE_PENDING)
               .exclude(payload__has_key="face_notified")
               .select_related("user")
               .order_by("created")[:limit])
    notified = 0
    for pa in actions:
        user = pa.user
        if refresh_tier2_face(user):
            notified += _finish_face(pa, "verified")
        elif not tier2_face_pending(user):
            # The bank refused it, or it is no longer being tracked.
            notified += _finish_face(pa, "failed")
    return notified


def face_link(user, msisdn) -> str:
    """The Tier 2 link WhatsApp sends: one tap to the Zitch camera page."""
    url = start_verification(user, msisdn, 2)
    token = url.rstrip("/").rsplit("/", 1)[-1]
    return _public_origin() + reverse("whatsapp_face", kwargs={"token": token})
