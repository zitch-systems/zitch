"""Lightweight per-IP rate limiting for sensitive endpoints.

Uses the Django cache. The default LocMemCache is per-process, so behind
multiple gunicorn workers the effective limit is multiplied — fine as a
baseline. For accurate, shared limits in production point CACHES at Redis, or
(better) enforce rate limits at the edge / CDN. Controlled by the
RATELIMIT_ENABLE setting, which is off under tests so it can't pollute
unrelated cases (a dedicated test re-enables it).
"""
import functools
import hashlib
import hmac
import ipaddress

from django.conf import settings
from django.core.cache import cache

from .http import fail


def opaque_cache_identifier(scope: str, value: str) -> str:
    """Keyed, non-reversible cache component for an IP/email/phone.

    Redis keys are visible to operators, monitoring agents and backups. They must
    not become a second plaintext customer directory. HMAC (rather than a plain
    hash) also prevents enumerating Nigeria's small phone-number space offline.
    """
    key = str(getattr(settings, "SECRET_KEY", "") or "zitch-cache-key").encode()
    message = f"{scope}\0{(value or '').strip().lower()}".encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()[:32]


# Cloudflare's published proxy ranges. CF-Connecting-IP is honoured only when the
# adjacent public hop is one of these; trusting the header from anyone else would
# let a direct caller name their own address.
CLOUDFLARE_PROXY_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
    "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
    "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
    "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32",
    "2405:8100::/32", "2a06:98c0::/29", "2c0f:f248::/32",
))

# Addresses that can only be one of the host platform's own proxies.
_PLATFORM_INTERNAL_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10",
    "127.0.0.0/8", "169.254.0.0/16", "::1/128", "fc00::/7", "fe80::/10",
))


def _in_networks(address, networks) -> bool:
    return any(address.version == network.version and address in network
               for network in networks)


def rightmost_public_ip(request) -> str:
    """The caller's own address: the right-most PUBLIC entry in X-Forwarded-For,
    falling back to REMOTE_ADDR.

    A caller can only PREPEND to X-Forwarded-For; every trusted hop APPENDS. So the
    right-most public entry is the address that connected to the edge, whatever a
    client puts in the header. When that hop is Cloudflare, the client is the one
    Cloudflare names in CF-Connecting-IP.
    """
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "") or ""
    public_chain = []
    for part in [p.strip() for p in xff.split(",") if p.strip()]:
        try:
            addr = ipaddress.ip_address(part)
        except ValueError:
            continue
        if addr.is_global:
            public_chain.append(addr)

    if public_chain:
        adjacent_proxy = public_chain[-1]
        if _in_networks(adjacent_proxy, CLOUDFLARE_PROXY_NETWORKS):
            connecting = (request.META.get("HTTP_CF_CONNECTING_IP", "") or "").strip()
            try:
                caller = ipaddress.ip_address(connecting)
            except ValueError:
                caller = None
            if caller is not None and caller.is_global:
                return str(caller)
        return str(adjacent_proxy)
    remote = (request.META.get("REMOTE_ADDR", "") or "").strip()
    try:
        return str(ipaddress.ip_address(remote))
    except ValueError:
        return "unknown"


def client_ip(request) -> str:
    """Best-effort client IP from the configured trusted proxy boundary.

    Untrusted clients can prepend arbitrary values to X-Forwarded-For. Selecting
    from the right using the known proxy-hop count prevents that spoof from
    creating a fresh rate-limit bucket for every guess.

    The hop count is only right for one proxy topology. On this platform the entry
    it selects has been one of the host's own internal proxies (10.x), or
    Cloudflare's edge on the custom domain - an address EVERY customer shares, so
    every per-IP limit became one global bucket: a dozen requests a minute from
    anyone returned 429 to everyone. When the selected hop is a proxy rather than a
    client, resolve the caller from the right-most public hop instead.
    """
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    hops = int(getattr(settings, "RATELIMIT_TRUSTED_PROXY_HOPS", 0) or 0)
    if xff and hops > 0:
        forwarded = [part.strip() for part in xff.split(",") if part.strip()]
        if len(forwarded) >= hops:
            candidate = forwarded[-hops]
            try:
                address = ipaddress.ip_address(candidate)
            except ValueError:
                address = None
            if address is not None:
                if (_in_networks(address, _PLATFORM_INTERNAL_NETWORKS)
                        or _in_networks(address, CLOUDFLARE_PROXY_NETWORKS)):
                    return rightmost_public_ip(request)
                return str(address)
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
                key = f"rl:{scope}:{opaque_cache_identifier(scope, client_ip(request))}"
                cache.add(key, 0, window)  # seed only if absent (sets the TTL)
                try:
                    count = cache.incr(key)
                except ValueError:
                    # Window expired between add and incr; start a fresh one.
                    cache.set(key, 1, window)
                    count = 1
                if count > limit:
                    # This rejection happens before the wrapped handler executes,
                    # so money clients can safely distinguish it from an unknown
                    # provider/gateway 429 and discard an unused attempt key.
                    return fail(
                        "Too many requests. Please slow down and try again shortly.",
                        status=429,
                        code="rate_limited",
                    )
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
    return f"login-lock:{scope}:{opaque_cache_identifier('login-' + scope, identifier)}"


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

