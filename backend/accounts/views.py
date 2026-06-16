import logging
import secrets
from datetime import timedelta

from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from common.http import api, fail, ok, require_user, resolve_token
from common.ratelimit import client_ip, ratelimit

log = logging.getLogger("zitch.security")
from utility.providers import (
    kyc_verify_bvn, kyc_verify_face, kyc_verify_nin, kyc_verify_nin_document, send_email, send_sms,
)
from wallet.services import get_or_create_wallet

from .models import OTP, AccessToken, User


def _otp_on_cooldown(phone: str) -> bool:
    """True if a code was issued for this phone within the resend cooldown,
    to stop OTP-flooding / rapid brute-force of a victim's number."""
    cutoff = timezone.now() - timedelta(seconds=OTP.RESEND_COOLDOWN_SECONDS)
    return OTP.objects.filter(phone=phone, created__gte=cutoff).exists()


def _otp_code() -> str:
    """A 6-digit one-time code from a CSPRNG. Must never use the `random` module:
    its Mersenne-Twister stream is predictable from observed outputs, and a
    guessable code here would let an attacker verify a number they don't own or
    reset another user's password."""
    return f"{secrets.randbelow(1_000_000):06d}"


def _weak_password(password: str, user=None) -> str | None:
    """Run Django's configured password validators server-side; returns a
    user-facing error string if the password is too weak (too short / too common
    / all-numeric / too similar to the user's own details), else None. Enforced
    here because the client strength hints are advisory — a direct API call could
    otherwise set a trivially guessable password on a money account."""
    from django.contrib.auth.password_validation import validate_password
    from django.core.exceptions import ValidationError

    try:
        validate_password(password, user)
        return None
    except ValidationError as e:
        return " ".join(e.messages)


@ratelimit("signin", limit=10, window=300)
@api
def signin(request):
    """POST /api/sigin/  {email_or_phone, password} -> {access_token}"""
    ident = (request.data.get("email_or_phone") or "").strip()
    password = request.data.get("password") or ""
    if not ident or not password:
        return fail("Email/phone and password are required")

    user = User.objects.filter(Q(email__iexact=ident) | Q(phone=ident) | Q(username=ident)).first()
    if user is None or not user.check_password(password):
        # Security event: surfaces credential-stuffing / targeted brute force in
        # the logs (the per-IP rate limiter caps it; this makes it observable).
        log.warning("signin_failed ident=%r ip=%s", ident, client_ip(request))
        return fail("Incorrect details", status=401)

    get_or_create_wallet(user)
    token = AccessToken.issue(user)
    return ok(access_token=token.key, message="Signed in")


@ratelimit("otp_send", limit=5, window=60)
@api
def phone_verification(request):
    """POST /api/phone_verification/ {email, phone} -> sends a signup OTP.

    Does NOT disclose whether the number already has an account — that would let
    anyone enumerate Zitch customers (for targeted phishing / SIM-swap). The
    reply is identical whether the number is new, already registered, or on
    cooldown; an already-registered owner is told to sign in via SMS to the
    number they control, not in the API response. Mirrors password_forgot below.
    """
    phone = (request.data.get("phone") or "").strip()
    email = (request.data.get("email") or "").strip()
    if not phone:
        return fail("Phone is required")
    if not _otp_on_cooldown(phone):
        existing = User.objects.filter(phone=phone).first()
        if existing is not None:
            # Owner-only channels: tell the real number / their email on file,
            # never the API caller.
            reminder = "You already have a Zitch account. Open the app to sign in, or use 'Forgot password' to reset."
            send_sms(phone, reminder)
            send_email(existing.email or "", "Zitch sign-in reminder", reminder)
        else:
            code = _otp_code()
            OTP.objects.create(phone=phone, email=email, code=code)
            message = f"Your Zitch verification code is {code}"
            send_sms(phone, message)
            send_email(email, "Your Zitch verification code", message)
    return ok(message="If this number can be registered, a verification code has been sent.")


@ratelimit("otp_verify", limit=20, window=60)
@api
def verify_otp(request):
    """POST /api/verify_otp/ {otp, phone} -> creates user + {access_token}"""
    phone = (request.data.get("phone") or "").strip()
    code = (request.data.get("otp") or "").strip()
    if not phone or not code:
        return fail("Phone and OTP are required")

    otp = OTP.objects.filter(phone=phone, used=False, purpose=OTP.SIGNUP).order_by("-created").first()
    if otp is None:
        return fail("Invalid OTP", status=400)
    if otp.is_expired:
        return fail("OTP has expired", status=400)
    if otp.too_many_attempts:
        # Cap reached: refuse further guesses on this code until a new one is
        # requested, bounding an attacker to MAX_ATTEMPTS tries per code.
        return fail("Too many incorrect attempts. Request a new code.", status=429)
    if otp.code != code:
        otp.attempts += 1
        otp.save(update_fields=["attempts"])
        return fail("Invalid OTP", status=400)

    otp.used = True
    otp.save(update_fields=["used"])

    user, _ = User.objects.get_or_create(
        phone=phone,
        defaults={"username": phone, "email": otp.email or ""},
    )
    get_or_create_wallet(user)
    token = AccessToken.issue(user)
    return ok(access_token=token.key, message="Verified")


@ratelimit("otp_send", limit=5, window=60)
@api
def resend_verify_otp(request):
    """POST /api/resend_verify_otp/ {phone, email?}

    Carries the email from the original phone_verification forward so the
    verified user is created with the right email and set-password works.
    The client may also pass `email` explicitly to override.
    """
    phone = (request.data.get("phone") or "").strip()
    if not phone:
        return fail("Phone is required")
    if _otp_on_cooldown(phone):
        return fail("Please wait a moment before requesting another code", status=429)
    email = (request.data.get("email") or "").strip()
    if not email:
        prior = OTP.objects.filter(phone=phone).order_by("-created").first()
        email = prior.email if prior else ""
    code = _otp_code()
    OTP.objects.create(phone=phone, email=email, code=code)
    message = f"Your Zitch verification code is {code}"
    send_sms(phone, message)
    send_email(email, "Your Zitch verification code", message)
    return ok(message="OTP resent")


# ------------------------------ ACCOUNT RECOVERY ------------------------------
@ratelimit("otp_send", limit=5, window=60)
@api
def password_forgot(request):
    """POST /api/password/forgot/ {email_or_phone} — send a reset code to a
    registered account, looked up by phone OR email.

    Always returns the same success message whether or not the account exists,
    so the endpoint can't be used to enumerate accounts.
    """
    ident = (request.data.get("email_or_phone") or request.data.get("phone") or "").strip()
    if not ident:
        return fail("Phone or email is required")
    user = User.objects.filter(phone=ident).first() or User.objects.filter(email__iexact=ident).first()
    if user is not None and user.phone and not _otp_on_cooldown(user.phone):
        code = _otp_code()
        OTP.objects.create(phone=user.phone, email=user.email or "", code=code, purpose=OTP.RESET)
        message = f"Your Zitch password reset code is {code}"
        send_sms(user.phone, message)
        send_email(user.email or "", "Your Zitch password reset code", message)
    return ok(message="If that account exists, a reset code has been sent.")


@ratelimit("otp_verify", limit=20, window=60)
@api
def password_reset(request):
    """POST /api/password/reset/ {email_or_phone, otp, password} -> {access_token}

    Verifies a RESET code and sets a new password. The account is looked up by
    phone OR email. Revokes every existing session (a reset means the old
    credential is gone) and returns a fresh token so the resetting device is
    signed in.
    """
    ident = (request.data.get("email_or_phone") or request.data.get("phone") or "").strip()
    code = (request.data.get("otp") or "").strip()
    password = request.data.get("password") or ""
    if not ident or not code:
        return fail("Phone/email and reset code are required")
    weak = _weak_password(password)
    if weak:
        return fail(weak)

    # Resolve the account first; a generic 400 if unknown so the endpoint can't
    # be used to tell a registered identifier from an unregistered one.
    user = User.objects.filter(phone=ident).first() or User.objects.filter(email__iexact=ident).first()
    if user is None:
        return fail("Invalid reset code", status=400)

    otp = OTP.objects.filter(phone=user.phone, used=False, purpose=OTP.RESET).order_by("-created").first()
    if otp is None:
        return fail("Invalid reset code", status=400)
    if otp.is_expired:
        return fail("Reset code has expired", status=400)
    if otp.too_many_attempts:
        return fail("Too many incorrect attempts. Request a new code.", status=429)
    if otp.code != code:
        otp.attempts += 1
        otp.save(update_fields=["attempts"])
        return fail("Invalid reset code", status=400)

    otp.used = True
    otp.save(update_fields=["used"])
    user.set_password(password)
    user.save(update_fields=["password"])
    user.tokens.all().delete()  # a password reset invalidates every prior session
    token = AccessToken.issue(user)
    return ok(access_token=token.key, message="Password reset")


@api
@require_user
def logout(request):
    """POST /api/logout/ {access_token} — revokes the presented token.

    Server-side revocation so a signed-out (or otherwise leaked-then-cleared)
    token can't be replayed for the remainder of its TTL.
    """
    AccessToken.objects.filter(key=AccessToken._hash(resolve_token(request))).delete()
    return ok(message="Logged out")


@api
@require_user
def set_password(request):
    """POST /api/set-password/ {access_token, password}

    Authenticated: acts on the token's user. Previously this looked the user up
    by an email in the body with no auth, letting anyone overwrite any account's
    password (and an empty email matched an arbitrary blank-email account).
    """
    user = request.user_obj
    password = request.data.get("password") or ""
    weak = _weak_password(password, user)
    if weak:
        return fail(weak)
    user.set_password(password)
    user.save(update_fields=["password"])
    # Revoke other sessions on a credential change: any token issued before this
    # change is now invalid. Keep the caller's current token so the onboarding
    # flow (set-password -> set-pin) and a change-password screen don't 401.
    user.tokens.exclude(key=AccessToken._hash(resolve_token(request))).delete()
    return ok(message="Password set")


@api
@require_user
def set_transaction_pin(request):
    """POST /api/set-transaction-pin/ {access_token, pin, password?}

    First-time set (onboarding) needs only the session token. CHANGING an
    already-set PIN additionally requires the account password, so a stolen
    session token alone can't overwrite the PIN that gates money movement.
    """
    user = request.user_obj
    pin = (request.data.get("pin") or "").strip()
    if len(pin) < 4:
        return fail("PIN must be at least 4 digits")
    if user.transaction_pin and not user.check_password(request.data.get("password") or ""):
        return fail("Enter your account password to change your PIN",
                    status=403, code="password_required")
    user.set_transaction_pin(pin)
    # Clear any brute-force lockout so a legitimate (password-authenticated) PIN
    # change isn't blocked by a stale lock against the old PIN.
    user.pin_failed_attempts = 0
    user.pin_locked_until = None
    user.save(update_fields=["transaction_pin", "pin_failed_attempts", "pin_locked_until"])
    return ok(message="Transaction PIN set")


@api
@require_user
def update_info(request):
    """POST /api/update_info/ {first_name, last_name, email, phone, access_token}"""
    user = request.user_obj
    data = request.data
    new_email = (data.get("email") or "").strip()
    new_phone = (data.get("phone") or "").strip()
    # Only validate uniqueness when the value is actually changing, so a plain
    # name update never trips on the user's own (or a legacy duplicate) value.
    # phone is unique in the DB — the pre-check turns a clash into a clean error
    # instead of a 500; email isn't unique but a clash would make sign-in (which
    # matches by email) ambiguous, so we guard it too.
    changing_phone = new_phone and new_phone != (user.phone or "")
    changing_email = new_email and new_email.lower() != (user.email or "").lower()
    if changing_phone and User.objects.filter(phone=new_phone).exclude(pk=user.pk).exists():
        return fail("That phone number is already in use")
    if changing_email and User.objects.filter(email__iexact=new_email).exclude(pk=user.pk).exists():
        return fail("That email is already in use")
    if data.get("first_name"):
        user.first_name = data["first_name"]
    if data.get("last_name"):
        user.last_name = data["last_name"]
    if new_email:
        user.email = new_email
    if new_phone:
        user.phone = new_phone
    user.save()
    return ok(message="Account updated")


def avatar_url(request, user) -> str:
    """Absolute URL for a user's profile photo, or '' if none set."""
    from django.conf import settings

    return request.build_absolute_uri(settings.MEDIA_URL + user.avatar) if user.avatar else ""


@api
@require_user
def avatar_upload(request):
    """POST /api/profile/avatar/ {access_token, image}
    `image` is a base64 data URL (or bare base64). Stores the photo and returns
    its absolute URL. -> {success, message, avatar}
    """
    import base64
    import binascii
    import secrets

    from django.conf import settings
    from django.core.files.base import ContentFile
    from django.core.files.storage import default_storage

    user = request.user_obj
    raw = (request.data.get("image") or request.data.get("avatar") or "").strip()
    if not raw:
        return fail("No image provided")

    ext = "png"
    if raw.startswith("data:"):
        header, _, b64 = raw.partition(",")
        if "jpeg" in header or "jpg" in header:
            ext = "jpg"
        elif "webp" in header:
            ext = "webp"
    else:
        b64 = raw

    try:
        blob = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        return fail("Invalid image data")
    if not blob:
        return fail("Empty image")
    if len(blob) > 3 * 1024 * 1024:
        return fail("Image too large (max 3MB)")

    # Drop the previous photo so we don't orphan files on re-upload.
    if user.avatar and default_storage.exists(user.avatar):
        default_storage.delete(user.avatar)

    path = default_storage.save(f"avatars/{user.id}-{secrets.token_hex(4)}.{ext}", ContentFile(blob))
    user.avatar = path
    user.save(update_fields=["avatar"])
    return ok(success=True, message="Photo updated",
              avatar=request.build_absolute_uri(settings.MEDIA_URL + path))


# --------------------------------- KYC ---------------------------------
def _kyc_state(user) -> dict:
    return {
        "tier": user.tier,
        "transaction_limit": str(user.transaction_limit),
        "bvn_verified": user.bvn_verified,
        "nin_verified": user.nin_verified,
        "face_verified": user.face_verified,
        "large_txn_threshold": str(User.LARGE_TXN_THRESHOLD),
    }


@api
@require_user
def kyc_status(request):
    """POST /api/kyc/status/ {access_token} -> tier + verification flags"""
    return ok(success=True, **_kyc_state(request.user_obj))


_KYC_BVN_TTL = 600  # seconds an unconfirmed BVN code stays valid


@api
@require_user
def kyc_bvn_start(request):
    """POST /api/kyc/bvn/start {access_token, bvn}

    Data-matches the BVN, then sends a one-time code (SMS + email) the user must
    confirm to prove ownership before the BVN counts. When the Prembly plan
    exposes native BVN-OTP to the BVN-registered phone, swap it in here
    (verify-before-live).
    """
    user = request.user_obj
    bvn = (request.data.get("bvn") or "").strip()
    result = kyc_verify_bvn(bvn)
    if not result.get("success"):
        return fail(result.get("message", "BVN verification failed"), status=400)
    code = _otp_code()
    cache.set(f"kyc_bvn:{user.id}", {"code": code, "bvn": bvn}, _KYC_BVN_TTL)
    msg = f"Your Zitch BVN verification code is {code}"
    send_sms(user.phone or "", msg)
    if user.email:
        send_email(user.email, "Your Zitch BVN code", msg)
    return ok(success=True, otp_required=True,
              message="We sent a verification code to your phone and email.")


@api
@require_user
def kyc_bvn_confirm(request):
    """POST /api/kyc/bvn/confirm {access_token, otp} — confirm the BVN code and
    mark the BVN verified."""
    user = request.user_obj
    otp = (request.data.get("otp") or "").strip()
    pending = cache.get(f"kyc_bvn:{user.id}")
    if not pending:
        return fail("Your code expired — start BVN verification again", status=400)
    if otp != pending["code"]:
        return fail("Incorrect code", status=400)
    cache.delete(f"kyc_bvn:{user.id}")
    user.set_bvn(pending["bvn"])
    user.bvn_verified = True
    user.recompute_tier()
    user.save(update_fields=["bvn_hash", "bvn_last4", "bvn_verified", "tier"])
    return ok(success=True, message="BVN verified", **_kyc_state(user))


@api
@require_user
def kyc_bvn(request):
    """POST /api/kyc/bvn/ {access_token, bvn} -> verifies BVN, recomputes tier"""
    user = request.user_obj
    bvn = (request.data.get("bvn") or "").strip()
    result = kyc_verify_bvn(bvn)
    if not result.get("success"):
        return fail(result.get("message", "BVN verification failed"), status=400)
    user.set_bvn(bvn)
    user.bvn_verified = True
    user.recompute_tier()
    user.save(update_fields=["bvn_hash", "bvn_last4", "bvn_verified", "tier"])
    return ok(success=True, message="BVN verified", **_kyc_state(user))


@api
@require_user
def kyc_nin(request):
    """POST /api/kyc/nin/ {access_token, nin} -> verifies NIN, recomputes tier"""
    user = request.user_obj
    nin = (request.data.get("nin") or "").strip()
    result = kyc_verify_nin(nin)
    if not result.get("success"):
        return fail(result.get("message", "NIN verification failed"), status=400)
    # The redesigned flow also uploads the NIN slip/ID image; verify it when sent.
    image = request.data.get("nin_image") or ""
    if image:
        doc = kyc_verify_nin_document(image)
        if not doc.get("success"):
            return fail(doc.get("message", "Couldn't verify your NIN document"), status=400)
    user.set_nin(nin)
    user.nin_verified = True
    user.recompute_tier()
    user.save(update_fields=["nin_hash", "nin_last4", "nin_verified", "tier"])
    return ok(success=True, message="NIN verified", **_kyc_state(user))


@api
@require_user
def kyc_face(request):
    """POST /api/kyc/face/ {access_token, selfie?}

    Verifies liveness via the KYC provider (mock-accepts offline) and, only on
    success, marks the user face-verified. Large transfers gate on this
    server-side flag, so it must never be a bare client claim.
    """
    user = request.user_obj
    selfie = request.data.get("selfie") or request.data.get("image") or ""
    result = kyc_verify_face(selfie)
    if not result.get("success"):
        return fail(result.get("message", "Face verification failed"), status=400)
    user.face_verified = True
    user.save(update_fields=["face_verified"])
    return ok(success=True, message="Face verification recorded", **_kyc_state(user))
