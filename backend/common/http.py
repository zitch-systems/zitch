"""Small helpers for JSON POST views without pulling in DRF.

The Expo app sends JSON bodies and (for authenticated calls) an `access_token`
field inside the body. These helpers parse the body, resolve the user from that
token, and standardise error shapes so views stay tiny.
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


def require_user(view):
    """Decorator: resolves the access_token in the body to a User.

    Apply *below* @api so request.data is available. Injects `request.user_obj`.
    """

    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        from accounts.models import AccessToken

        token = (request.data or {}).get("access_token", "")
        user = AccessToken.resolve(token)
        if user is None:
            return fail("Invalid or expired session", status=401)
        request.user_obj = user
        return view(request, *args, **kwargs)

    return wrapper
