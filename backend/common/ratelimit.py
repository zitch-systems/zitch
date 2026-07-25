"""Lightweight per-IP rate limiting for sensitive endpoints.

Uses the Django cache. The default LocMemCache is per-process, so behind
multiple gunicorn workers the effective limit is multiplied — fine as a
baseline. For accurate, shared limits in production point CACHES at Redis, or
(better) enforce rate limits at the edge / CDN. Controlled by the
RATELIMIT_ENABLE setting, which is off under tests so it can't pollute
unrelated cases (a dedicated test re-enables it).
"""
import functools
import ipaddress

from django.conf import settings
from django.core.cache import cache

from .http import fail


def client_ip(request) -> str:
    """Best-effort client IP from the configured trusted proxy boundary.

    Untrusted clients can prepend arbitrary values to X-Forwarded-For. Selecting
    from the right using the known proxy-hop count prevents that spoof from
    creating a fresh rate-limit bucket for every guess.
    """
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    hops = int(getattr(settings, "RATELIMIT_TRUSTED_PROXY_HOPS", 0) or 0)
    if xff and hops > 0:
        forwarded = [part.strip() for part in xff.split(",") if part.strip()]
        if len(forwarded) >= hops:
            candidate = forwarded[-hops]
            try:
                return str(ipaddress.ip_address(candidate))
            except ValueError:
                pass
    remote = (request.META.get("REMOTE_ADDR", "") or "").strip()
    try:
        return str(ipaddress.ip_address(remote))
    except ValueError:
        return "unknown"


def ratelimit(scope: str, limit: int, window: int):
    """Allow at most `limit` requests per `window` seconds per IP for `scope`.

    Fixed-window counter: the first request seeds a counter with a `window` TTL;
    once the count exceeds `limit` within that window, further requests get 429
    until the window rolls over.
    """
    def decorator(view):
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            if getattr(settings, "RATELIMIT_ENABLE", True):
                key = f"rl:{scope}:{client_ip(request)}"
                cache.add(key, 0, window)  # seed only if absent (sets the TTL)
                try:
                    count = cache.incr(key)
                except ValueError:
                    # Window expired between add and incr; start a fresh one.
                    cache.set(key, 1, window)
                    count = 1
                if count > limit:
                    return fail("Too many requests. Please slow down and try again shortly.", status=429)
            return view(request, *args, **kwargs)
        return wrapper
    return decorator


# --------------------------------------------------------------------------- #
# Per-account login lockout
# --------------------------------------------------------------------------- #
# The per-IP `ratelimit` above bounds a single source; this bounds guesses
# against a single *account* regardless of source IP, so a distributed /
# rotating-IP brute force against one operator login is still capped. Failures
# are counted in the cache under a per-identifier key that expires after the
# lockout window, so the lock auto-clears — no unlock step needed.
def _lockout_key(scope: str, identifier: str) -> str:
    return f"login-lock:{scope}:{(identifier or '').strip().lower()}"


def login_locked(scope: str, identifier: str, max_fails: int | None = None) -> bool:
    """True if `identifier` has reached the failed-login cap for `scope`.
    `max_fails` defaults to settings.ADMIN_LOGIN_MAX_FAILS. Disabled (always
    False) when RATELIMIT_ENABLE is off, like the rest of the rate limiting, so
    tests aren't polluted by a shared cache."""
    if not getattr(settings, "RATELIMIT_ENABLE", True):
        return False
    if max_fails is None:
        max_fails = getattr(settings, "ADMIN_LOGIN_MAX_FAILS", 5)
    return (cache.get(_lockout_key(scope, identifier)) or 0) >= max_fails


def note_login_failure(scope: str, identifier: str, window: int | None = None) -> int:
    """Record one failed login for `identifier`; the counter (and thus the lock)
    expires `window` seconds after the FIRST failure in the window (defaults to
    settings.ADMIN_LOGIN_LOCKOUT_SECONDS). Returns the running count."""
    if not getattr(settings, "RATELIMIT_ENABLE", True):
        return 0
    if window is None:
        window = getattr(settings, "ADMIN_LOGIN_LOCKOUT_SECONDS", 900)
    key = _lockout_key(scope, identifier)
    cache.add(key, 0, window)
    try:
        return cache.incr(key)
    except ValueError:
        cache.set(key, 1, window)
        return 1


def clear_login_failures(scope: str, identifier: str) -> None:
    """Reset the failed-login counter for `identifier` after a success."""
    if getattr(settings, "RATELIMIT_ENABLE", True):
        cache.delete(_lockout_key(scope, identifier))

