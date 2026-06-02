"""Small helpers for JSON POST views without pulling in DRF.

The Expo app sends JSON bodies. Authenticated calls pass the token either as an
`Authorization: Bearer <token>` header (preferred) or, for older builds, an
`access_token` field in the body — `require_user` accepts both. These helpers
parse the body, resolve the user, and standardise error shapes so views stay
tiny.
"""
import functools
import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt


def ok(data=None, **extra):
    payload = {}
    if data:
        payload.update(data)
    payload.update(extra)
    return JsonResponse(payload, status=200)


def check_send_limits(user, amount):
    """Returns an error JsonResponse if `amount` breaks the user's tier limit
    or (at/above the large-txn threshold) the user isn't face-verified;
    otherwise None.

    Large transfers require durable, server-side face verification
    (`user.face_verified`, set via the provider-backed KYC face step) — not a
    per-request flag, which a caller hitting the API directly could just assert.
    """
    if amount > user.transaction_limit:
        return fail(
            f"This exceeds your Tier {user.tier} limit of ₦{user.transaction_limit:,.0f}. "
            "Upgrade your KYC to send more.",
            status=403, code="limit_exceeded", tier=user.tier,
            transaction_limit=str(user.transaction_limit),
        )
    from accounts.models import User
    if amount >= User.LARGE_TXN_THRESHOLD and not user.face_verified:
        return fail(
            "Face verification required for this amount.",
            status=403, code="face_required",
            large_txn_threshold=str(User.LARGE_TXN_THRESHOLD),
        )
    return None


def fail(message, status=400, **extra):
    return JsonResponse({"message": message, **extra}, status=status)


def api(view):
    """Decorator: POST-only, JSON body parsed into `request.data` (a dict)."""

    @csrf_exempt
    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        if request.method != "POST":
            return fail("Method not allowed", status=405)
        try:
            request.data = json.loads(request.body or b"{}")
        except (ValueError, TypeError):
            return fail("Invalid JSON body", status=400)
        if not isinstance(request.data, dict):
            return fail("Invalid request body", status=400)
        return view(request, *args, **kwargs)

    return wrapper


def resolve_token(request) -> str:
    """The access token from the `Authorization: Bearer` header, falling back to
    the body `access_token` (older app builds send it there)."""
    auth = request.headers.get("Authorization", "")
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip()
    return (getattr(request, "data", None) or {}).get("access_token", "")


def require_user(view):
    """Decorator: resolves the request's access token (Bearer header or body)
    to a User.

    Apply *below* @api so request.data is available. Injects `request.user_obj`.
    """

    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        from accounts.models import AccessToken

        user = AccessToken.resolve(resolve_token(request))
        if user is None:
            return fail("Invalid or expired session", status=401)
        request.user_obj = user
        return view(request, *args, **kwargs)

    return wrapper
