"""Zitch face check: a short-lived link to a page that uses the live front camera.

The page asks the customer to look straight, then turn left and right, and
sends one frame from each pose. The server refuses frames that do not change
between poses (a still photo held to the camera), and keeps the straight-on
frame as the face photo for the bank upgrade. The bank then compares that photo
with its identity records; this check only proves a live, moving face was there.
"""
import base64
import binascii
import json
import secrets
from io import BytesIO

from django.core import signing
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from PIL import Image

from accounts.models import User

from . import upgrades

SALT = "banking.liveness.v1"
LINK_SECONDS = 15 * 60
FRAME_MAX_CHARS = 2 * 1024 * 1024  # base64 of one camera frame
POSES = ("straight", "left", "right")
# Mean change per pixel (0-255) between the straight frame and each turned frame,
# on a small greyscale copy. A still photo held to a camera stays well below it.
MIN_POSE_CHANGE = 6.0


def _nonce_key(user_id):
    return f"bank-liveness:{user_id}"


def _origin():
    from whatsapp.verification_web import _public_origin
    return _public_origin()


def create_link(user):
    """One active link per customer: a new link retires the previous one."""
    nonce = secrets.token_urlsafe(18)
    cache.set(_nonce_key(user.pk), nonce, LINK_SECONDS)
    token = signing.dumps({"u": user.pk, "n": nonce}, salt=SALT, compress=True)
    return {"success": True, "url": _origin() + reverse("bank_liveness", args=[token]), "expires_in": LINK_SECONDS}


def _user_for(token):
    try:
        data = signing.loads(token, salt=SALT, max_age=LINK_SECONDS)
    except signing.BadSignature:
        return None
    if not isinstance(data, dict) or cache.get(_nonce_key(data.get("u"))) != data.get("n"):
        return None
    return User.objects.filter(pk=data.get("u"), is_active=True).first()


def _frame(value):
    if not isinstance(value, str) or not value.startswith("data:image/jpeg;base64,"):
        raise ValueError("Camera frames must be JPEG images")
    value = value.partition(",")[2]
    if not value or len(value) > FRAME_MAX_CHARS:
        raise ValueError("A camera frame is missing or too large")
    try:
        raw = base64.b64decode(value, validate=True)
        with Image.open(BytesIO(raw)) as im:
            if im.format != "JPEG" or im.width < 160 or im.height < 160:
                raise ValueError("A camera frame is too small")
            small = im.convert("L").resize((48, 48))
            return value, list(small.getdata())
    except (binascii.Error, OSError, Image.DecompressionBombError):
        raise ValueError("A camera frame could not be read") from None


def _change(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def check_frames(frames):
    """Return the straight-on frame (base64 JPEG) if the poses show a moving face."""
    if not isinstance(frames, dict) or set(frames) != set(POSES):
        raise ValueError("Complete all three steps")
    parsed = {pose: _frame(frames[pose]) for pose in POSES}
    straight = parsed["straight"][1]
    if min(_change(straight, parsed[pose][1]) for pose in ("left", "right")) < MIN_POSE_CHANGE:
        raise ValueError("We couldn't see you turn your head. Please try again, turning a little more")
    return parsed["straight"][0]


def _headers(response, nonce):
    response["Cache-Control"] = "no-store, private, max-age=0"
    response["Referrer-Policy"] = "no-referrer"
    response["X-Content-Type-Options"] = "nosniff"
    response["X-Frame-Options"] = "DENY"
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response["Permissions-Policy"] = "camera=(self), microphone=(), geolocation=()"
    response["Content-Security-Policy"] = (
        "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'; "
        f"style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'; connect-src 'self'; "
        "img-src blob: data:; media-src blob: mediastream:; object-src 'none'")
    return response


@csrf_exempt  # The signed, single-use, 15-minute token is the credential; no cookies are used.
def liveness_page(request, token):
    nonce = secrets.token_urlsafe(18)
    user = _user_for(token)
    if request.method == "GET":
        response = render(request, "banking/liveness.html",
                          {"nonce": nonce, "valid": user is not None, "submit_url": request.path + "/submit"},
                          status=200 if user else 410)
        return _headers(response, nonce)
    return _headers(HttpResponse(status=405), nonce)


@csrf_exempt  # Same-origin fetch from the page above, authorised by the signed token.
def liveness_submit(request, token):
    nonce = secrets.token_urlsafe(18)
    if request.method != "POST":
        return _headers(HttpResponse(status=405), nonce)
    user = _user_for(token)
    if user is None:
        return _headers(JsonResponse({"success": False, "message": "This link has expired. Start again in the Zitch app."},
                                     status=410), nonce)
    if int(request.META.get("CONTENT_LENGTH") or 0) > 3 * FRAME_MAX_CHARS + 4096:
        return _headers(JsonResponse({"success": False, "message": "The photos are too large."}, status=413), nonce)
    try:
        frames = json.loads(request.body or b"{}").get("frames")
        photo = check_frames(frames)
        upgrades.store_document(user, "userPhoto", photo)
    except (ValueError, AttributeError) as exc:
        return _headers(JsonResponse({"success": False, "message": str(exc)}, status=400), nonce)
    cache.delete(_nonce_key(user.pk))  # Single use.
    return _headers(JsonResponse({"success": True}), nonce)
