"""Small helpers for JSON POST views without pulling in DRF.

The Expo app sends JSON bodies and authenticated calls pass the token in the
`Authorization: Bearer` header. A body-token compatibility switch exists only
for local/test builds; production keeps credentials out of request bodies and
the logs/analytics systems that commonly capture them.
"""
import functools
import json
import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
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
    # A large spend from a device this account has never used needs the customer, not
    # just their credentials. Checked before the bank's cap because it is local and
    # free, and because it catches a takeover rather than a limit.
    from common.risk import new_device_step_up_error
    step_up = new_device_step_up_error(user, amount)
    if step_up:
        return step_up
    # The partner bank enforces its OWN per-tier caps on the NUBAN, on a different
    # ladder from ours — so an amount our tier allows can still be refused at the
    # gateway. Refusing here turns that into a clear message instead of a failed
    # payout that has to be reversed. Silent until the account's bank tier is known.
    from wallet.services import bank_spend_error
    return bank_spend_error(user, amount)


def velocity_exceeded(user) -> bool:
    """True if the user has made too many outbound money movements in a short
    window — the signature of a compromised account/PIN being drained. Counts OUT
    ledger rows in the last 10 minutes against VELOCITY_MAX_OUT_10MIN (settings;
    default 20 — far above any legitimate human pace). Disabled when the cap is 0.

    The channel-agnostic core, so HTTP (check_velocity) and non-HTTP callers (the
    WhatsApp router) share ONE fraud brake — a compromised WhatsApp session can't
    move funds faster than the app allows."""
    from django.conf import settings as dj_settings

    cap = int(getattr(dj_settings, "VELOCITY_MAX_OUT_10MIN", 20) or 0)
    if cap <= 0:
        return False
    from wallet.models import Transaction

    recent = Transaction.objects.filter(
        user=user, direction=Transaction.OUT,
        created__gte=timezone.now() - timedelta(minutes=10),
    ).count()
    if recent >= cap:
        log.warning("velocity_blocked user=%s recent_out=%s cap=%s", user.id, recent, cap)
        return True
    return False


def check_velocity(user):
    """HTTP wrapper around `velocity_exceeded`: a 429 JsonResponse to surface, or
    None when within the limit."""
    if velocity_exceeded(user):
        return fail("Too many transactions in a short time. Please wait a few minutes and try again.",
                    status=429, code="velocity")
    return None


def check_send_limits(user, amount):
    """HTTP wrapper around `send_limit_error`: returns an error JsonResponse if
    `amount` breaks a limit, otherwise None. Also applies the fraud velocity
    guard (check_velocity) — every money-send path funnels through here."""
    velocity = check_velocity(user)
    if velocity is not None:
        return velocity
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
# NGN→foreign FX conversions ("Convert NGN→…") move value out of the regulated
# NGN ledger just like a transfer, and the FX OUT row is written currency="NGN",
# so it's counted here (the non-NGN-source Convert rows carry a foreign currency
# and are excluded by _daily_spent's currency="NGN" filter).
_TRANSFER_PREFIXES = ("Transfer to", "Convert NGN")
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


# Per-transaction minimums. One definition for both surfaces: these were
# duplicated as literals in the app views and the WhatsApp router and had
# drifted (WhatsApp accepted ₦10 transfers the app refused at ₦50, and ₦100
# electricity the app refused at ₦500), so the same account behaved differently
# depending on where the customer stood.
MIN_TRANSFER = Decimal("50")
MIN_AIRTIME = Decimal("50")
MIN_ELECTRICITY = Decimal("500")


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


def daily_kind_for(service: str) -> str:
    """Which daily bucket a ledger label counts toward: 'transfer', 'bill', or ''.

    Single source of truth for the mapping, so the cap CHECKED before a spend and
    the bucket the row later COUNTS toward can't drift apart — they are now derived
    from the same label by the same function.
    """
    label = str(service or "")
    if label.startswith(_TRANSFER_PREFIXES):
        return "transfer"
    if label.startswith(_BILL_PREFIXES):
        return "bill"
    return ""


#: The checks that must pass before ANY money may leave an account, in the order
#: a customer completes them. Spelled out rather than expressed as a tier so the
#: refusal can name the specific step that is missing, not a tier number.
#:
#: NIN is deliberately NOT here. It is still collected, still hashed, still
#: reviewed, and still required for Tier 1 by `recompute_tier` — so it governs
#: how MUCH a customer may move. It just no longer decides whether they may move
#: anything at all. Until the Prembly NIN lookup is confirmed against a live
#: dashboard (see utility.providers.prembly_verify_nin), the only route to
#: nin_verified is an operator clearing the review queue by hand, and gating
#: every first spend on that means one unattended day strands every new customer
#: with a funded wallet they cannot spend from.
#:
#: BVN stays: the bank name-matches it while opening the account, so it is
#: verified in the ordinary course of signup with nobody in the loop.
_FIRST_SPEND_CHECKS = (
    ("email_verified", "email address"),
    ("phone_verified", "phone number"),
    ("bvn_verified", "BVN"),
)


def stale_pin_error(user) -> "str | None":
    """Refusal for an account still holding a pre-6-digit PIN, or None.

    Applied to money LEAVING only, like the identity checks: the account keeps
    working, keeps receiving, and keeps signing in — it just cannot authorise a
    payment with a PIN we have decided is too short. There is no way to measure
    an existing hash, so this rests on the flag the migration set.
    """
    if not getattr(user, "pin_reset_required", False):
        return None
    return ("For your security, transaction PINs are now 6 digits. Set a new one before "
            "sending money — on WhatsApp reply *reset pin*; in the Zitch app, Me → Security.")


def unverified_error(user) -> "str | None":
    """Refusal naming the identity checks still outstanding, or None if the
    account may spend.

    Applied to money LEAVING an account only. Funding is deliberately still
    allowed while unverified: money arriving is how a customer gets to the point
    of caring, and blocking a deposit would strand funds in a NUBAN the owner
    cannot then use.

    This is a floor beneath the tier ceilings, not a replacement for them. The
    ceilings answer "how much"; this answers "at all".
    """
    stale = stale_pin_error(user)
    if stale:
        return stale
    missing = [label for field, label in _FIRST_SPEND_CHECKS
               if not getattr(user, field, False)]
    if not missing:
        return None
    if len(missing) > 1:
        listed = ", ".join(missing[:-1]) + " and " + missing[-1]
    else:
        listed = missing[0]
    return (f"Before you can send money we need to verify your {listed}. "
            "On WhatsApp reply *8*; in the Zitch app, open Me → Verify identity.")


def spend_limit_error(user, amount, service: str) -> "str | None":
    """EVERY cap that applies to an outbound debit of `amount` labelled `service`,
    as one user-facing message — or None when the spend is allowed.

    This is the authoritative gate. `wallet.services.debit` calls it a second time
    while holding the wallet row lock, which is what makes the daily caps and the
    velocity brake actually hold: checked outside a lock they are advisory, because
    two concurrent requests both read the same "spent today" and both pass.

    The views still call the check_* wrappers first, because a 403 with the right
    code and a helpful message is a better experience than an exception — but that
    call is now an optimisation, not the guarantee.
    """
    # Identity first: an account that has not proved who owns it should be
    # refused before we start discussing amounts, and this is the gate `debit`
    # re-runs under the row lock — so it holds for every surface at once.
    unverified = unverified_error(user)
    if unverified:
        return unverified
    if velocity_exceeded(user):
        return "Too many transactions in a short time. Please wait a few minutes and try again."
    msg = send_limit_error(user, amount)
    if msg:
        return msg
    kind = daily_kind_for(service)
    if kind:
        return daily_limit_error(user, amount, kind)
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
    The lock state is mirrored back onto the CALLER's `user` instance before
    returning — see `_sync_lock_state`.
    """
    from django.db import transaction as db_transaction

    from accounts.models import User

    if not user.transaction_pin:
        return False, "no_pin", "No transaction PIN set on this account"

    with db_transaction.atomic():
        u = User.objects.select_for_update().get(pk=user.pk)
        if u.pin_locked:
            _sync_lock_state(user, u)
            return False, "pin_locked", _pin_lock_message(u)
        if u.check_transaction_pin((raw_pin or "").strip()):
            # Correct PIN: clear any accumulated failures / stale lock, AND the
            # escalation strikes. Proving you know the PIN is what separates a
            # customer who forgot it from someone working through the keyspace,
            # so it is what resets the ladder back to the one-hour tier.
            if u.pin_failed_attempts or u.pin_locked_until or u.pin_lockout_strikes:
                u.pin_failed_attempts = 0
                u.pin_locked_until = None
                u.pin_lockout_strikes = 0
                u.save(update_fields=["pin_failed_attempts", "pin_locked_until",
                                      "pin_lockout_strikes"])
            _sync_lock_state(user, u)
            return True, None, None
        u.pin_failed_attempts += 1
        if u.pin_failed_attempts >= User.PIN_MAX_ATTEMPTS:
            u.pin_failed_attempts = 0  # reset the counter; the lock is the gate now
            # Escalate: five wrong guesses is a forgotten PIN, sitting out the
            # first lock and burning five more is not. Strikes only advance while
            # no correct PIN has been entered in between (see above).
            u.pin_lockout_strikes += 1
            minutes = (User.PIN_LOCKOUT_MINUTES if u.pin_lockout_strikes < 2
                       else User.PIN_LOCKOUT_ESCALATED_MINUTES)
            u.pin_locked_until = timezone.now() + timedelta(minutes=minutes)
            u.save(update_fields=["pin_failed_attempts", "pin_locked_until",
                                  "pin_lockout_strikes"])
            # Security event: a locked PIN means repeated wrong guesses against the
            # second factor that gates money movement — worth surfacing in logs.
            log.warning("transaction_pin_locked user=%s strike=%s minutes=%s",
                        u.pk, u.pin_lockout_strikes, minutes)
            _sync_lock_state(user, u)
            return False, "pin_locked", _pin_lock_message(u)
        u.save(update_fields=["pin_failed_attempts"])
        _sync_lock_state(user, u)
        left = User.PIN_MAX_ATTEMPTS - u.pin_failed_attempts
        return False, "pin_incorrect", f"Incorrect transaction PIN. {left} attempt(s) left before lock."


def _sync_lock_state(user, u) -> None:
    """Mirror the lock fields from the row-locked copy back onto the caller's
    instance.

    Every write above lands on `u`, which `select_for_update()` fetched fresh —
    a SEPARATE object from the `user` the caller handed in. Without this the
    caller still holds pre-call values, so any code running after this call that
    reads `user.pin_locked`, `.pin_locked_until` or `.pin_lockout_strikes` would
    see stale state on the very request that just changed it.

    In-memory only. It deliberately does not save — `user` may be carrying the
    caller's own unsaved edits, and this function's business is the three fields
    it just read back.
    """
    if user is u:
        return
    user.pin_failed_attempts = u.pin_failed_attempts
    user.pin_locked_until = u.pin_locked_until
    user.pin_lockout_strikes = u.pin_lockout_strikes


def _pin_lock_message(u) -> str:
    """One wording for a locked PIN, whether the lock was just applied or is
    being re-hit later, so the customer is never told two different things about
    the same lock.

    Always states the remaining time rather than the tier's nominal length: a
    customer coming back mid-lock needs to know when they can act, not what the
    policy is. And always names the way out — a reset clears ANY lock outright
    (see User.set_transaction_pin) and is reachable regardless of tier, so
    withholding the offer on the first lock only made a customer who forgot
    their PIN sit out an hour before learning they never had to. The wording used
    to reserve this for the 24-hour escalated tier, on the theory that a first
    lock is more likely an honest slip than a forgotten PIN — but "you can reset
    it" costs nothing to say even then, and the customer is the one who knows
    which is true.
    """
    remaining = (u.pin_locked_until or timezone.now()) - timezone.now()
    minutes = max(1, int(remaining.total_seconds() // 60) + 1)
    if minutes >= 120:
        when = f"about {round(minutes / 60)} hours"
    elif minutes >= 60:
        when = "about an hour"
    else:
        when = f"{minutes} minute(s)"
    return (f"Transaction PIN locked after too many wrong attempts. Try again in {when}. "
            "You can reset your PIN instead — we'll confirm it's you first, and a new "
            "PIN unlocks payments straight away.")


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
                  message="Your purchase is processing and will be confirmed shortly.",
                  **success_extra)
    if status != "success":
        return fail(result.get("message", "Transaction failed"), status=502)
    return ok(success=True, message=success_message, reference=txn.reference, **success_extra)


def mask_pii(value: str) -> str:
    """Redact a login identifier for logs and audit rows: keep just enough to
    correlate repeated attempts, hide the rest. A failed-login trail must not
    become a plaintext list of real emails/phones that anyone with audit read
    can harvest. `jane@zitch.ng` -> `j***@zitch.ng`; `08012345678` ->
    `0801***78`; short values collapse to `***`."""
    v = (value or "").strip()
    if not v:
        return ""
    if "@" in v:
        local, _, domain = v.partition("@")
        return f"{(local[:1] or '')}***@{domain}"
    if len(v) <= 4:
        return "***"
    return f"{v[:4]}***{v[-2:]}"


def fail(message, status=400, **extra):
    return JsonResponse({"message": message, **extra}, status=status)


def parse_json_object(request, *, limit: int | None = None):
    """Return ``(dict, None)`` or ``(None, error_response)`` for JSON input.

    The customer and operator APIs share this parser so the privileged back
    office cannot drift from the public API's size and content-type boundary.
    """
    limit = int(limit or getattr(settings, "API_MAX_BODY_BYTES", 4 * 1024 * 1024))
    try:
        declared = int(request.META.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError):
        declared = 0
    if declared > limit:
        return None, fail("Payload too large", status=413)
    try:
        body = request.body or b""
    except RequestDataTooBig:
        return None, fail("Payload too large", status=413)
    if len(body) > limit:
        return None, fail("Payload too large", status=413)
    content_type = (request.content_type or "").lower()
    if body and not (content_type == "application/json" or content_type.endswith("+json")):
        return None, fail("Content-Type must be application/json", status=415)
    try:
        data = json.loads(body or b"{}")
    except (ValueError, TypeError, UnicodeDecodeError):
        return None, fail("Invalid JSON body", status=400)
    if not isinstance(data, dict):
        return None, fail("Invalid request body", status=400)
    return data, None


def api(view):
    """POST-only, size-bounded JSON parsed into ``request.data``."""

    @csrf_exempt
    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        if request.method != "POST":
            return fail("Method not allowed", status=405)
        request.data, error = parse_json_object(request)
        if error is not None:
            return error
        return view(request, *args, **kwargs)

    return wrapper


def resolve_token(request) -> str:
    """Resolve a bearer token; body fallback is non-production compatibility."""
    auth = request.headers.get("Authorization", "")
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip()
    if getattr(settings, "ALLOW_BODY_ACCESS_TOKEN", False):
        return (getattr(request, "data", None) or {}).get("access_token", "")
    return ""


def require_user(view):
    """Decorator: resolves the request's access token (Bearer header or body)
    to a User.

    Apply *below* @api so request.data is available for non-production legacy
    compatibility. Injects `request.user_obj`.
    """

    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        from accounts.models import AccessToken

        # App tokens issued by current clients are bound to the install identifier
        # that performed authentication.  Passing an empty value is deliberate: a
        # caller cannot bypass the binding by stripping the header.
        device_id = (request.headers.get("X-Zitch-Device") or "").strip()[:64]
        user = AccessToken.resolve(resolve_token(request), device_id=device_id)
        if user is None:
            return fail("Invalid or expired session", status=401)
        request.user_obj = user
        # Hang the calling device on the user so money code can see it without every
        # signature growing a `request` parameter. This is the single place every
        # authenticated request passes through with both objects in hand. Parsing only
        # — no database write, because this runs on every API call.
        from common.risk import attach_device
        attach_device(request, user)
        return view(request, *args, **kwargs)

    return wrapper
