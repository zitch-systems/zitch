"""Small helpers for JSON POST views without pulling in DRF.

The Expo app sends JSON bodies. Authenticated calls pass the token either as an
`Authorization: Bearer <token>` header (preferred) or, for older builds, an
`access_token` field in the body — `require_user` accepts both. These helpers
parse the body, resolve the user, and standardise error shapes so views stay
tiny.
"""
import functools
import json
import logging
from datetime import timedelta
from decimal import Decimal

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

log = logging.getLogger("zitch.security")


def ok(data=None, **extra):
    payload = {}
    if data:
        payload.update(data)
    payload.update(extra)
    return JsonResponse(payload, status=200)


def send_limit_error(user, amount) -> str | None:
    """User-facing reason `amount` can't be sent — tier cap or (at/above the
    large-txn threshold) missing face verification — or None if it's allowed.

    The single source of truth for the limit rules + copy, shared by the HTTP
    `check_send_limits` and non-HTTP callers (e.g. the WhatsApp router).

    Large transfers require durable, server-side face verification
    (`user.face_verified`, set via the provider-backed KYC face step) — not a
    per-request flag, which a caller hitting the API directly could just assert.
    """
    if amount > user.transaction_limit:
        return (f"This exceeds your Tier {user.tier} limit of ₦{user.transaction_limit:,.0f}. "
                "Upgrade your KYC to send more.")
    from accounts.models import User
    if amount >= User.LARGE_TXN_THRESHOLD and not user.face_verified:
        return "Face verification required for this amount."
    return None


def check_send_limits(user, amount):
    """HTTP wrapper around `send_limit_error`: returns an error JsonResponse if
    `amount` breaks a limit, otherwise None."""
    if amount > user.transaction_limit:
        return fail(
            send_limit_error(user, amount),
            status=403, code="limit_exceeded", tier=user.tier,
            transaction_limit=str(user.transaction_limit),
        )
    from accounts.models import User
    if amount >= User.LARGE_TXN_THRESHOLD and not user.face_verified:
        return fail(
            send_limit_error(user, amount),
            status=403, code="face_required",
            large_txn_threshold=str(User.LARGE_TXN_THRESHOLD),
        )
    return None


# --- Daily aggregate limits (per KYC tier) -----------------------------------
# Caps the *total* a user can move per category per day, on top of the
# per-transaction `check_send_limits`. WhatsApp onboarding (BVN -> Tier 2) caps
# at ₦1,000,000 transfers / ₦100,000 bills a day; full app KYC (Tier 3) raises
# them. The caps live on the user (per tier), so they apply identically whether
# the user transacts in the app or on WhatsApp.
_TRANSFER_PREFIXES = ("Transfer to",)
# The "bill" bucket is really "non-transfer spend": airtime/data/cable/electricity
# plus the other spendable-cash outflows (betting funding, exam PINs, card funding).
# All of them already enforce the per-txn tier ceiling + face gate; folding them
# into one daily aggregate closes the gap where they escaped the daily cap entirely.
# The ledger labels these flows write must start with one of these prefixes.
_BILL_PREFIXES = ("Airtime", "Data", "Cable", "Electricity", "Betting", "Exam", "Card funding")


def _daily_spent(user, prefixes) -> Decimal:
    """Sum of today's non-failed outbound NGN spend whose service label starts
    with any of `prefixes`, within the user's local day."""
    from django.db.models import Q, Sum
    from wallet.models import Transaction
    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    label_q = Q()
    for p in prefixes:
        label_q |= Q(service__startswith=p)
    agg = (Transaction.objects
           .filter(label_q, user=user, direction=Transaction.OUT,
                   currency="NGN", created__gte=start)
           .exclude(transaction_status=Transaction.FAILED)
           .aggregate(s=Sum("amount")))
    return agg["s"] or Decimal("0")


def daily_limit_error(user, amount, kind) -> "str | None":
    """User-facing reason `amount` breaks the per-day cap for `kind`
    ('transfer' or 'bill'), or None. Non-HTTP, so the WhatsApp router shares it."""
    if kind == "transfer":
        prefixes, cap, label = _TRANSFER_PREFIXES, user.daily_transfer_limit, "transfer"
    else:
        prefixes, cap, label = _BILL_PREFIXES, user.daily_bill_limit, "bill payment"
    spent = _daily_spent(user, prefixes)
    if spent + amount > cap:
        remaining = cap - spent
        if remaining < 0:
            remaining = Decimal("0")
        return (f"This would pass your daily {label} limit of ₦{cap:,.0f} "
                f"(₦{remaining:,.0f} left today). Upgrade your KYC in the app to raise it.")
    return None


def check_daily_limit(user, amount, kind):
    """HTTP wrapper around `daily_limit_error`: a 403 JsonResponse if the daily
    cap would be exceeded, else None."""
    msg = daily_limit_error(user, amount, kind)
    if msg:
        cap = user.daily_transfer_limit if kind == "transfer" else user.daily_bill_limit
        return fail(msg, status=403, code="daily_limit_exceeded", daily_limit=str(cap))
    return None


def parse_amount(value):
    """Coerce a request amount to a safe money Decimal, or None if invalid.

    Rejects non-finite values (``Infinity``/``NaN``/``1e500`` all parse as
    Decimals and would otherwise crash money math with InvalidOperation/500) and
    non-positive amounts, and quantizes to 2 places (rounding down) so the value
    can never carry sub-kobo precision the ledger column would silently round —
    which would let the stored row, the provider call, and the debit disagree.
    Callers keep their own min/max copy; this only guarantees a clean number.
    """
    from decimal import ROUND_DOWN, Decimal, InvalidOperation

    try:
        d = Decimal(str(value))
        if not d.is_finite() or d <= 0:
            return None
        # quantize can still raise InvalidOperation for a finite-but-absurd value
        # (e.g. "1e500" exceeds the default context precision), so keep it inside
        # the guard — a clean None (-> 400) beats a 500.
        return d.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    except (InvalidOperation, TypeError, ValueError):
        return None


def spend_key(client_key, user, *parts, window_seconds: int = 30):
    """The idempotency key to use for a spend.

    Prefers the client-supplied key (stable across that client's own retries —
    the Expo app sends one per authorization). When absent (older or third-party
    clients), derives a deterministic fallback from the user + spend details
    within a short time window, so an accidental double-submit is still deduped
    by the ledger's unique (user, idempotency_key) constraint instead of
    debiting twice. The real client always sends a unique key, so it never hits
    the fallback.
    """
    import hashlib
    import time

    key = (client_key or "").strip()
    if key:
        return key
    bucket = int(time.time() // max(1, window_seconds))
    raw = "|".join([str(getattr(user, "id", user)), *[str(p) for p in parts], str(bucket)])
    return "auto-" + hashlib.sha256(raw.encode()).hexdigest()[:24]


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


def evaluate_transaction_pin(user, raw_pin):
    """Brute-force-protected PIN check shared by HTTP views and the WhatsApp
    router. Returns ``(ok, code, message)``: ok=True on a correct PIN; otherwise
    code is one of ``no_pin`` / ``pin_locked`` / ``pin_incorrect`` with a
    user-facing message.

    The failure count and lockout live on the user row and are updated under a
    row lock, so concurrent guesses (across channels) can't slip past the cap.
    """
    from django.db import transaction as db_transaction

    from accounts.models import User

    if not user.transaction_pin:
        return False, "no_pin", "No transaction PIN set on this account"

    with db_transaction.atomic():
        u = User.objects.select_for_update().get(pk=user.pk)
        if u.pin_locked:
            mins = max(1, int((u.pin_locked_until - timezone.now()).total_seconds() // 60) + 1)
            return (False, "pin_locked",
                    f"Transaction PIN locked after too many wrong attempts. Try again in {mins} minute(s).")
        if u.check_transaction_pin((raw_pin or "").strip()):
            # Correct PIN: clear any accumulated failures / stale lock.
            if u.pin_failed_attempts or u.pin_locked_until:
                u.pin_failed_attempts = 0
                u.pin_locked_until = None
                u.save(update_fields=["pin_failed_attempts", "pin_locked_until"])
            return True, None, None
        u.pin_failed_attempts += 1
        if u.pin_failed_attempts >= User.PIN_MAX_ATTEMPTS:
            u.pin_failed_attempts = 0  # reset the counter; the lock is the gate now
            u.pin_locked_until = timezone.now() + timedelta(minutes=User.PIN_LOCKOUT_MINUTES)
            u.save(update_fields=["pin_failed_attempts", "pin_locked_until"])
            # Security event: a locked PIN means repeated wrong guesses against the
            # second factor that gates money movement — worth surfacing in logs.
            log.warning("transaction_pin_locked user=%s", u.pk)
            return (False, "pin_locked",
                    f"Transaction PIN locked for {User.PIN_LOCKOUT_MINUTES} minutes after too many wrong attempts.")
        u.save(update_fields=["pin_failed_attempts"])
        left = User.PIN_MAX_ATTEMPTS - u.pin_failed_attempts
        return False, "pin_incorrect", f"Incorrect transaction PIN. {left} attempt(s) left before lock."


def verify_transaction_pin(user, raw_pin):
    """HTTP wrapper around `evaluate_transaction_pin`: returns an error
    JsonResponse on a bad/locked/missing PIN, otherwise None. The PIN is the
    second factor gating every money-movement endpoint, so a stolen session
    token isn't enough to guess it."""
    ok, code, message = evaluate_transaction_pin(user, raw_pin)
    if ok:
        return None
    if code == "no_pin":
        return fail(message, status=403)
    status = 429 if code == "pin_locked" else 403
    return fail(message, status=status, code=code)


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
