"""Small helpers for JSON POST views without pulling in DRF.

The Expo app sends JSON bodies. Authenticated calls pass the token either as an
`Authorization: Bearer <token>` header (preferred) or, for older builds, an
`access_token` field in the body — `require_user` accepts both. These helpers
parse the body, resolve the user, and standardise error shapes so views stay
tiny.
"""
import functools
import json
from datetime import timedelta

from django.http import JsonResponse
from django.utils import timezone
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


def idempotent_replay(prior):
    """Standard response for a duplicate idempotent spend, or None when there's
    no prior row for the key. Replays the original outcome so a retried or raced
    request never debits / charges twice."""
    if prior is None:
        return None
    from wallet.models import Transaction
    if prior.transaction_status == Transaction.FAILED:
        return fail("This request already failed — please start a new one", status=409, code="duplicate")
    return ok(success=True, reference=prior.reference, message="Already processed", duplicate=True)


def verify_transaction_pin(user, raw_pin):
    """Verify a user's transaction PIN with brute-force protection.

    The PIN is the second factor gating every money-movement endpoint, so a
    stolen session token must not be enough to guess it. Returns an error
    JsonResponse when no PIN is set, when the PIN is temporarily locked after too
    many wrong tries, or when the PIN is wrong; otherwise None.

    The failure count and lockout live on the user row and are updated under a
    row lock, so concurrent guesses can't slip past the cap.
    """
    from django.db import transaction as db_transaction

    from accounts.models import User

    if not user.transaction_pin:
        return fail("No transaction PIN set on this account", status=403)

    with db_transaction.atomic():
        u = User.objects.select_for_update().get(pk=user.pk)
        if u.pin_locked:
            mins = max(1, int((u.pin_locked_until - timezone.now()).total_seconds() // 60) + 1)
            return fail(
                f"Transaction PIN locked after too many wrong attempts. Try again in {mins} minute(s).",
                status=429, code="pin_locked",
            )
        if u.check_transaction_pin((raw_pin or "").strip()):
            # Correct PIN: clear any accumulated failures / stale lock.
            if u.pin_failed_attempts or u.pin_locked_until:
                u.pin_failed_attempts = 0
                u.pin_locked_until = None
                u.save(update_fields=["pin_failed_attempts", "pin_locked_until"])
            return None
        u.pin_failed_attempts += 1
        if u.pin_failed_attempts >= User.PIN_MAX_ATTEMPTS:
            u.pin_failed_attempts = 0  # reset the counter; the lock is the gate now
            u.pin_locked_until = timezone.now() + timedelta(minutes=User.PIN_LOCKOUT_MINUTES)
            u.save(update_fields=["pin_failed_attempts", "pin_locked_until"])
            return fail(
                f"Transaction PIN locked for {User.PIN_LOCKOUT_MINUTES} minutes "
                "after too many wrong attempts.",
                status=429, code="pin_locked",
            )
        u.save(update_fields=["pin_failed_attempts"])
        left = User.PIN_MAX_ATTEMPTS - u.pin_failed_attempts
        return fail(
            f"Incorrect transaction PIN. {left} attempt(s) left before lock.",
            status=403, code="pin_incorrect",
        )


def provider_purchase_response(status, txn, result, *, success_message, **success_extra):
    """Map a wallet.services.run_provider_purchase outcome to a JSON response.

    success -> 200 with the success message (plus any extra fields, e.g. a meter
               token or exam PINs); pending -> 200 with pending=True and a
               'processing' note (the money is held while reconciliation confirms
               or refunds it later); failed -> 502 with the provider's message.
    """
    if status == "pending":
        return ok(pending=True, reference=txn.reference,
                  message="Your purchase is processing and will be confirmed shortly.")
    if status != "success":
        return fail(result.get("message", "Transaction failed"), status=502)
    return ok(success=True, message=success_message, reference=txn.reference, **success_extra)


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
