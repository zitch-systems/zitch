import random
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from common.http import api, fail, ok, require_user
from common.ratelimit import ratelimit
from utility.providers import kyc_verify_bvn, kyc_verify_face, kyc_verify_nin, send_sms
from wallet.services import get_or_create_wallet

from .models import OTP, AccessToken, User


def _otp_on_cooldown(phone: str) -> bool:
    """True if a code was issued for this phone within the resend cooldown,
    to stop OTP-flooding / rapid brute-force of a victim's number."""
    cutoff = timezone.now() - timedelta(seconds=OTP.RESEND_COOLDOWN_SECONDS)
    return OTP.objects.filter(phone=phone, created__gte=cutoff).exists()


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
        return fail("Incorrect details", status=401)

    get_or_create_wallet(user)
    token = AccessToken.issue(user)
    return ok(access_token=token.key, message="Signed in")


@ratelimit("otp_send", limit=5, window=60)
@api
def phone_verification(request):
    """POST /api/phone_verification/ {email, phone} -> sends OTP"""
    phone = (request.data.get("phone") or "").strip()
    email = (request.data.get("email") or "").strip()
    if not phone:
        return fail("Phone is required")
    if User.objects.filter(phone=phone).exists():
        return fail("An account with this phone already exists")
    if _otp_on_cooldown(phone):
        return fail("Please wait a moment before requesting another code", status=429)

    code = f"{random.randint(0, 999999):06d}"
    OTP.objects.create(phone=phone, email=email, code=code)
    send_sms(phone, f"Your Zitch verification code is {code}")
    return ok(message="OTP sent")


@ratelimit("otp_verify", limit=20, window=60)
@api
def verify_otp(request):
    """POST /api/verify_otp/ {otp, phone} -> creates user + {access_token}"""
    phone = (request.data.get("phone") or "").strip()
    code = (request.data.get("otp") or "").strip()
    if not phone or not code:
        return fail("Phone and OTP are required")

    otp = OTP.objects.filter(phone=phone, used=False).order_by("-created").first()
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
    code = f"{random.randint(0, 999999):06d}"
    OTP.objects.create(phone=phone, email=email, code=code)
    send_sms(phone, f"Your Zitch verification code is {code}")
    return ok(message="OTP resent")


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
    if len(password) < 8:
        return fail("Password must be at least 8 characters")
    user.set_password(password)
    user.save(update_fields=["password"])
    return ok(message="Password set")


@api
@require_user
def set_transaction_pin(request):
    """POST /api/set-transaction-pin/ {access_token, pin}

    Authenticated: sets the PIN on the token's user (see set_password note)."""
    user = request.user_obj
    pin = (request.data.get("pin") or "").strip()
    if len(pin) < 4:
        return fail("PIN must be at least 4 digits")
    user.set_transaction_pin(pin)
    user.save(update_fields=["transaction_pin"])
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


@api
@require_user
def kyc_bvn(request):
    """POST /api/kyc/bvn/ {access_token, bvn} -> verifies BVN, recomputes tier"""
    user = request.user_obj
    bvn = (request.data.get("bvn") or "").strip()
    result = kyc_verify_bvn(bvn)
    if not result.get("success"):
        return fail(result.get("message", "BVN verification failed"), status=400)
    user.bvn = bvn
    user.bvn_verified = True
    user.recompute_tier()
    user.save(update_fields=["bvn", "bvn_verified", "tier"])
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
    user.nin = nin
    user.nin_verified = True
    user.recompute_tier()
    user.save(update_fields=["nin", "nin_verified", "tier"])
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
