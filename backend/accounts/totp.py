"""RFC 6238 TOTP, implemented on the standard library.

`django-otp` is not a dependency and adding one for ~40 lines of HMAC would be a
poor trade: TOTP is a small, frozen, well-specified algorithm, and the security here
lives in the policy around it (replay refusal, drift bounds, lockout) rather than in
the arithmetic.

Compatible with Google Authenticator / Authy / 1Password: SHA-1, 6 digits, 30-second
step. Those three parameters are the interoperable defaults — SHA-256 is stronger on
paper and is silently mis-handled by several popular authenticator apps, and an
operator who cannot enrol will simply have MFA switched off for them.
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

DIGITS = 6
STEP_SECONDS = 30
# Accept the neighbouring steps: phone clocks drift, and a code that is refused
# because a handset is 20 seconds fast produces a support ticket and, eventually, a
# request to turn MFA off. One step either side is the conventional bound — it widens
# the window an intercepted code is usable in from 30 to 90 seconds, which the
# single-use rule below then closes anyway.
DRIFT_STEPS = 1


def new_secret() -> str:
    """A fresh base32 secret. 20 bytes = 160 bits, the RFC 4226 recommendation."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def current_step(at: float | None = None) -> int:
    return int((at if at is not None else time.time()) // STEP_SECONDS)


def code_for(secret: str, step: int) -> str:
    """The 6-digit code for `secret` at `step`."""
    padding = "=" * (-len(secret) % 8)
    key = base64.b32decode(secret + padding, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    truncated = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(truncated % (10 ** DIGITS)).zfill(DIGITS)


def verify(secret: str, code: str, *, after_step: int = 0, at: float | None = None) -> int | None:
    """The step `code` matched, or None.

    `after_step` refuses any step at or below one already used — the caller persists
    the returned value. Without it a code stays valid for its whole window, so an
    operator's code read over their shoulder (or captured from a phished form) can be
    replayed by the attacker within the same 30 seconds. Single-use is what makes the
    drift tolerance above safe.

    Constant-time comparison, so a wrong code cannot be narrowed digit by digit from
    response timing.
    """
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != DIGITS:
        return None
    now = current_step(at)
    for step in range(now - DRIFT_STEPS, now + DRIFT_STEPS + 1):
        if step <= after_step:
            continue  # already used, or a replay of an older code
        if hmac.compare_digest(code_for(secret, step), code):
            return step
    return None


def provisioning_uri(secret: str, *, account: str, issuer: str = "Zitch") -> str:
    """The `otpauth://` URI an authenticator app scans.

    Returned once, at enrolment, and never again: the secret is the credential, so
    an endpoint that re-displays it turns any authenticated session into a permanent
    bypass of the second factor.
    """
    label = quote(f"{issuer}:{account}", safe="")
    return (f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"
            f"&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}")
