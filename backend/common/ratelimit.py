"""Lightweight per-IP rate limiting for sensitive endpoints.

Uses the Django cache. The default LocMemCache is per-process, so behind
multiple gunicorn workers the effective limit is multiplied — fine as a
baseline. For accurate, shared limits in production point CACHES at Redis, or
(better) enforce rate limits at the edge / CDN. Controlled by the
RATELIMIT_ENABLE setting, which is off under tests so it can't pollute
unrelated cases (a dedicated test re-enables it).
"""
import functools

from django.conf import settings
from django.core.cache import cache

from .http import fail


def client_ip(request) -> str:
    """Best-effort client IP, honouring the proxy header Render/CDNs set."""
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "") or "unknown"


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
