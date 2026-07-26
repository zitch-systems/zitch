import hmac
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone

from common.http import api, fail, mask_pii, ok, require_user, resolve_token
from common.ratelimit import (
    clear_login_failures,
    client_ip,
    login_locked,
    note_login_failure,
    ratelimit,
)

log = logging.getLogger("zitch.security")
from utility import wema
from utility.providers import (
    kyc_verify_address, kyc_verify_face, kyc_verify_id_document, kyc_verify_nin_document,
    send_email, send_sms, verify_bvn, verify_nin,
)
from wallet.models import Wallet
from wallet.services import get_or_create_wallet, wema_account_reference

from .models import OTP, AccessToken, User

# Brand mark hosted on the marketing site (Cloudflare), so email clients can load it.
_LOGO_URL = "https://zitch.ng/assets/brand/zitch-icon.png"


def _branded_email(title: str, intro: str, *, code: str = "", note: str = "") -> str:
    """An email-client-safe branded HTML body: the Zitch logo + wordmark on the brand
    header, a heading, an intro line, an OPTIONAL prominent one-time-code box, an
    optional fine-print note, and the licensing footer. Inline styles only (no
    <style>/external CSS) so it renders consistently in Gmail/Outlook/Apple Mail.

    `code` is optional — omit it for message-only emails (e.g. a sign-in reminder);
    the code box only renders when a code is supplied."""
    code_box = (
        '<div style="font-size:32px;font-weight:bold;letter-spacing:10px;color:#0FA295;'
        f'background:#EAF3F1;border-radius:12px;padding:18px 10px;margin:0 0 22px;">{code}</div>'
    ) if code else ""
    note_p = (
        f'<p style="font-size:12.5px;color:#737B83;line-height:1.55;margin:0;">{note}</p>'
    ) if note else ""
    return (
        '<div style="background:#EFF7F5;padding:32px 16px;font-family:Arial,Helvetica,sans-serif;">'
        '<div style="max-width:460px;margin:0 auto;background:#ffffff;border-radius:18px;overflow:hidden;border:1px solid #E2EEEB;">'
        '<div style="background:#0C3A39;padding:24px 0;text-align:center;">'
        f'<img src="{_LOGO_URL}" width="54" height="54" alt="Zitch" style="border-radius:14px;display:inline-block;vertical-align:middle;" />'
        '<span style="color:#ffffff;font-size:22px;font-weight:bold;letter-spacing:0.5px;vertical-align:middle;margin-left:10px;">Zitch</span>'
        '</div>'
        '<div style="padding:32px 28px;text-align:center;">'
        f'<h1 style="font-size:20px;color:#0A0A0B;margin:0 0 8px;">{title}</h1>'
        f'<p style="font-size:14px;color:#737B83;line-height:1.55;margin:0 0 22px;">{intro}</p>'
        f'{code_box}{note_p}'
        '</div>'
        '<div style="background:#F4F9F8;border-top:1px solid #E2EEEB;padding:16px;text-align:center;font-size:11px;color:#9AAEB0;">'
        'Zitch &middot; Licensed by the CBN &middot; Deposits insured by the NDIC'
        '</div></div></div>'
    )


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

    if login_locked("user", ident, settings.USER_LOGIN_MAX_FAILS):
        log.warning("signin_locked ident=%r ip=%s", mask_pii(ident), client_ip(request))
        return fail("Too many failed attempts. Please wait before trying again.", status=429)

    user = User.objects.filter(Q(email__iexact=ident) | Q(phone=ident) | Q(username=ident)).first()
    if user is None or not user.is_active or not user.check_password(password):
        # Security event: surfaces credential-stuffing / targeted brute force in
        # the logs (the per-IP rate limiter caps it; this makes it observable).
        note_login_failure("user", ident, settings.USER_LOGIN_LOCKOUT_SECONDS)
        log.warning("signin_failed ident=%r ip=%s", mask_pii(ident), client_ip(request))
        return fail("Incorrect details", status=401)

    clear_login_failures("user", ident)
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
            send_email(existing.email or "", "Zitch sign-in reminder", reminder,
                       html=_branded_email("You already have a Zitch account", reminder,
                                           note="If this wasn't you, you can safely ignore this email."))
        else:
            code = _otp_code()
            OTP.issue(phone=phone, code=code, email=email)
            # SMS ONLY: the signup OTP proves control of the PHONE (the identity
            # being registered). Emailing it to the caller-supplied, unverified
            # `email` would let an attacker receive the code for someone else's
            # number and squat/deny that account (and misdirect P2P sends keyed on
            # phone). The email is still stored for the account, just never sent the
            # code. Account-recovery flows, by contrast, email the address on file.
            send_sms(phone, f"Your Zitch verification code is {code}")
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
    if not otp.verify_code(code):
        otp.attempts += 1
        otp.save(update_fields=["attempts"])
        return fail("Invalid OTP", status=400)

    otp.used = True
    otp.save(update_fields=["used"])

    # The customer's legal name is captured at register (before this OTP round-trip)
    # and sent here so the account is created WITH a name. It matters beyond display:
    # the dedicated funding account (Wema NUBAN) is later opened in this name, and an
    # account with no holder name can't be safely funded by transfer — the payer has
    # no name to confirm against. Trim + length-cap to the User field width.
    first_name = (request.data.get("first_name") or "").strip()[:150]
    last_name = (request.data.get("last_name") or "").strip()[:150]
    user, created = User.objects.get_or_create(
        phone=phone,
        defaults={"username": phone, "email": otp.email or "",
                  "first_name": first_name, "last_name": last_name},
    )
    # Defense in depth: a SIGNUP OTP must never sign anyone into an already
    # established account. A genuine new signup has no usable password at this
    # point (set-password runs AFTER verify), so an existing user that already has
    # a password is a pre-existing account — refuse rather than issue its session.
    if not created and user.has_usable_password():
        log.warning("signup_otp_for_existing_account phone=%r ip=%s", phone, client_ip(request))
        return fail("Invalid OTP", status=400)
    # Backfill the name onto a mid-signup account (created on a prior attempt, or
    # before name capture existed) that has no name yet — so re-verifying still
    # lands a named account. Never overwrite a name already on file.
    if not created and (first_name or last_name) and not (user.first_name or user.last_name):
        user.first_name = first_name or user.first_name
        user.last_name = last_name or user.last_name
        user.save(update_fields=["first_name", "last_name"])
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
    # A SIGNUP OTP authenticates into the matching account (verify_otp resolves the
    # phone to the existing user), so minting one here and DELIVERING it to a
    # client-supplied email would be a full, password-less account takeover from
    # just a phone number. Guard exactly like phone_verification:
    #   * an established account (has a usable password) is never sent a fresh
    #     signup OTP — reply with the same generic message, no enumeration;
    #   * a mid-signup account (created but no password yet) may still resend, but
    #     only to its OWN email on file — never a client-supplied address.
    existing = User.objects.filter(phone=phone).first()
    if existing is not None and existing.has_usable_password():
        return ok(message="OTP resent")
    if existing is not None:
        email = existing.email or ""
    else:
        email = (request.data.get("email") or "").strip()
        if not email:
            prior = OTP.objects.filter(phone=phone).order_by("-created").first()
            email = prior.email if prior else ""
    code = _otp_code()
    OTP.issue(phone=phone, code=code, email=email)
    # SMS ONLY, same reason as phone_verification: a signup OTP authenticates into
    # the matching account, so it must reach only the PHONE being registered —
    # never a client-supplied email. The email is stored for the account record.
    send_sms(phone, f"Your Zitch verification code is {code}")
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
        OTP.issue(phone=user.phone, code=code, email=user.email or "", purpose=OTP.RESET)
        message = f"Your Zitch password reset code is {code}"
        send_sms(user.phone, message)
        send_email(user.email or "", "Your Zitch password reset code", message,
                   html=_branded_email("Reset your password",
                                       "Use this code to reset your Zitch password.",
                                       code=code,
                                       note="If you didn't request a reset, ignore this email — your password is unchanged."))
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
    if not otp.verify_code(code):
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
    # Changing an EXISTING password requires the current one, so a stolen session
    # token alone can't overwrite it. First-time onboarding runs set-password
    # before any real password exists — a freshly-created user's password hash is
    # the empty string (has_usable_password() is True for ""), so gate on a
    # NON-EMPTY usable hash to exempt the first set while covering every change.
    if user.password and user.has_usable_password():
        current = request.data.get("current_password") or ""
        if not (current and user.check_password(current)):
            return fail("Enter your current password to change it",
                        status=403, code="current_password_required")
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
@ratelimit("set_pin", limit=10, window=300)
@require_user
def set_transaction_pin(request):
    """POST /api/set-transaction-pin/ {access_token, pin, old_pin?, password?}

    First-time set (onboarding) needs only the session token. CHANGING an
    already-set PIN additionally requires proof the caller knows the current PIN
    — either the ``old_pin`` itself, or (as a fallback for a forgotten PIN) the
    account ``password`` — so a stolen session token alone can't overwrite the
    PIN that gates money movement.
    """
    from common.http import evaluate_transaction_pin

    user = request.user_obj
    pin = (request.data.get("pin") or "").strip()
    if len(pin) < 4:
        return fail("PIN must be at least 4 digits")
    if user.transaction_pin:
        old_pin = (request.data.get("old_pin") or "").strip()
        password = request.data.get("password") or ""
        # old_pin MUST go through the brute-force-protected checker (lockout after
        # PIN_MAX_ATTEMPTS) — a raw check_transaction_pin here let a stolen token
        # guess the 4-digit PIN unlimited times and overwrite it. The password
        # fallback keeps forgot-PIN recovery working.
        ok_pwd = bool(password) and user.check_password(password)
        ok_old = False
        if not ok_pwd and old_pin:
            ok_old, code, message = evaluate_transaction_pin(user, old_pin)
            if code == "pin_locked":
                return fail(message, status=403, code="pin_locked")
        if not (ok_old or ok_pwd):
            return fail("Enter your current PIN to change it",
                        status=403, code="current_pin_required")
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
# Nigeria identity (BVN/NIN): ALAT has NO standalone lookup — verification happens in
# the NUBAN account-creation OTP flow (/api/wallet/wema/*), which name-matches the
# holder record ALAT returns before lifting the tier. So in PRODUCTION the verify_bvn/
# verify_nin calls below return an "otp_required" redirect (route the user to account
# setup); dev/tests keep the provider mock so these endpoints still exercise offline.
# The image/biometric steps (face/address/ID) stay on Prembly and are unaffected.
def _reserve_wallet_account(user, bvn: str = "", nin: str = "") -> None:
    """Best-effort: mint the user's dedicated funding account once KYC supplies a
    BVN/NIN. Never lets a provider hiccup fail the KYC response — it's retried on
    the next KYC action (and lazily via /api/wallet/account/)."""
    from wallet.services import ensure_reserved_account

    try:
        ensure_reserved_account(user, bvn=bvn, nin=nin)
    except Exception:  # noqa: BLE001 — reservation must never break verification
        log.warning("reserve_account_failed user=%s", user.id, exc_info=True)


def _sync_wema_tier3(user, address: dict) -> None:
    """Best-effort: lift the user's Wema NUBAN to Tier 3 at the bank (address
    verification) so its bank-side limits track the user's verified KYC. Needs only
    the address + account number (NOT BVN/NIN, which we don't retain — so the Tier-2
    face upgrade, which does require them, is intentionally not auto-synced). Never
    breaks the KYC response; a hiccup is logged and can be re-synced later."""
    acct = getattr(getattr(user, "wallet", None), "account_number", "") or ""
    if not acct:
        return
    try:
        from utility import wema
        res = wema.upgrade_tier3(acct, address)
        if not res.get("success"):
            log.info("wema_tier3_sync_soft_fail user=%s msg=%s", user.id, res.get("message", ""))
    except Exception:  # noqa: BLE001 — a provider hiccup must never break KYC
        log.warning("wema_tier3_sync_error user=%s", user.id, exc_info=True)


_TIER_NAMES = {0: "Unverified", 1: "Verified", 2: "Enhanced", 3: "Premium"}


def _kyc_state(user) -> dict:
    return {
        "tier": user.tier,
        "tier_name": _TIER_NAMES.get(user.tier, "Unverified"),
        "transaction_limit": str(user.transaction_limit),
        "daily_transfer_limit": str(user.daily_transfer_limit),
        "daily_bill_limit": str(user.daily_bill_limit),
        "bvn_verified": user.bvn_verified,
        "nin_verified": user.nin_verified,
        "face_verified": user.face_verified,
        "address_verified": user.address_verified,
        "id_document_verified": user.id_document_verified,
        "large_txn_threshold": str(User.LARGE_TXN_THRESHOLD),
    }


@api
@require_user
def kyc_status(request):
    """POST /api/kyc/status/ {access_token} -> tier + verification flags"""
    return ok(success=True, **_kyc_state(request.user_obj))


def _simulate_provision_account(user) -> str:
    """Provision a mock NUBAN for a test user the way the OTP flow does in simulation:
    get_account_details returns a demo account when WEMA_SIMULATION is on. Idempotent —
    returns the existing number untouched, or the freshly minted one ('' on a
    miss/conflict)."""
    wallet = get_or_create_wallet(user)
    if wallet.account_number:
        return wallet.account_number
    acct = wema.get_account_details(user.phone or "", bvn=True)
    num = (acct.get("account_number") or "").strip()
    if not num or Wallet.objects.filter(account_number=num).exclude(pk=wallet.pk).exists():
        return wallet.account_number
    wallet.account_number = num
    wallet.account_name = acct.get("account_name") or (user.get_full_name() or "").strip() or "ZITCH USER"
    wallet.bank_name = acct.get("bank_name") or "Wema Bank"
    wallet.account_reference = wema_account_reference(user)
    try:
        wallet.save(update_fields=["account_number", "account_name", "bank_name",
                                   "account_reference", "updated"])
    except IntegrityError:
        return wallet.account_number
    wema.lift_debit_restriction(num, bvn=True)  # mock-lifts the PND hold in simulation
    return num


@api
@ratelimit("simulate_kyc", limit=30, window=60)
def simulate_kyc(request):
    """POST /api/dev/simulate-kyc/ {token, phone, tier?} -> {success, ...kyc, account_number}

    TEST-ONLY. Marks a test user KYC-verified to the requested tier (default 3) and
    provisions a mock NUBAN, so the app's KYC-gated features — tiers, virtual account,
    higher limits — can be walked without real identity data or deliverable ownership
    OTPs. Same three-gate lockdown as /api/dev/simulate-deposit/ (and the SAME token),
    ALL required:

      1. WEMA_SIMULATION on (else 404 — cannot exist on a live-money deploy).
      2. SIMULATE_DEPOSIT_TOKEN set and matching (constant-time compare).
      3. tier in 1..3 for an existing user.

    Every use is logged loudly; wema_preflight HARD-FAILS while the token is set.
    Remove SIMULATE_DEPOSIT_TOKEN before go-live.
    """
    # Gate 1 — simulation only. 404 so a live deploy gives no hint the route exists.
    if not wema.wema_simulation():
        return fail("Not found", status=404)
    # Gate 2 — shared-secret token (the same one that gates simulate-deposit).
    configured = settings.SIMULATE_DEPOSIT_TOKEN
    supplied = (request.data.get("token") or "").strip()
    if not configured or not hmac.compare_digest(supplied, configured):
        return fail("Forbidden", status=403)
    # Gate 3 — target user + tier.
    phone = (request.data.get("phone") or "").strip()
    if not phone:
        return fail("phone is required")
    try:
        tier = int(request.data.get("tier", 3))
    except (TypeError, ValueError):
        return fail("tier must be 1, 2, or 3")
    if tier not in (1, 2, 3):
        return fail("tier must be 1, 2, or 3")
    user = User.objects.filter(phone=phone).first()
    if user is None:
        return fail("No user with that phone", status=404)

    # Set the flags recompute_tier() derives the tier from (see User.recompute_tier):
    # tier 1 = BVN+NIN, tier 2 += face+address, tier 3 += ID document. Dummy BVN/NIN
    # values just populate the hash/last4 the way a real verification would.
    user.set_bvn("22222222222")
    user.set_nin("11111111111")
    user.bvn_verified = True
    user.nin_verified = True
    user.face_verified = tier >= 2
    user.address_verified = tier >= 2
    user.id_document_verified = tier >= 3
    user.recompute_tier()
    user.save(update_fields=["bvn_hash", "bvn_last4", "nin_hash", "nin_last4",
                             "bvn_verified", "nin_verified", "face_verified",
                             "address_verified", "id_document_verified", "tier"])

    account_number = _simulate_provision_account(user)
    log.warning("simulate_kyc_used phone=%s tier=%s account=%s — SIMULATE_DEPOSIT_TOKEN is "
                "set (WEMA_SIMULATION on); REMOVE before go-live", phone, user.tier, account_number)
    return ok(success=True, account_number=account_number,
              message="Simulated KYC applied", **_kyc_state(user))


_KYC_BVN_TTL = 600  # seconds an unconfirmed BVN code stays valid
_KYC_BVN_MAX_ATTEMPTS = 5  # wrong OTP guesses before the pending code is burned


@api
@ratelimit("kyc_bvn_start", limit=5, window=300)
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
    result = verify_bvn(bvn, name=user.get_full_name() or "", mobile=user.phone or "")
    if not result.get("success"):
        return fail(result.get("message", "BVN verification failed"), status=400)
    code = _otp_code()
    cache.set(f"kyc_bvn:{user.id}", {"code": code, "bvn": bvn}, _KYC_BVN_TTL)
    msg = f"Your Zitch BVN verification code is {code}"
    send_sms(user.phone or "", msg)
    if user.email:
        send_email(user.email, "Your Zitch BVN code", msg,
                   html=_branded_email("Verify your BVN",
                                       "Enter this code in the app to confirm your BVN.",
                                       code=code,
                                       note="If you didn't start BVN verification, ignore this email."))
    return ok(success=True, otp_required=True,
              message="We sent a verification code to your phone and email.")


@api
@ratelimit("kyc_bvn_confirm", limit=20, window=300)
@require_user
def kyc_bvn_confirm(request):
    """POST /api/kyc/bvn/confirm {access_token, otp} — confirm the BVN code and
    mark the BVN verified."""
    user = request.user_obj
    otp = (request.data.get("otp") or "").strip()
    cache_key = f"kyc_bvn:{user.id}"
    pending = cache.get(cache_key)
    if not pending:
        return fail("Your code expired — start BVN verification again", status=400)
    if otp != pending["code"]:
        # Cap wrong guesses: the OTP is only 6 digits, so without a counter a
        # stolen session could brute-force it inside the 10-minute window. After
        # _KYC_BVN_MAX_ATTEMPTS the pending code is burned and the user restarts.
        attempts = int(pending.get("attempts", 0)) + 1
        if attempts >= _KYC_BVN_MAX_ATTEMPTS:
            cache.delete(cache_key)
            return fail("Too many incorrect attempts — start BVN verification again",
                        status=429, code="too_many_attempts")
        pending["attempts"] = attempts
        cache.set(cache_key, pending, _KYC_BVN_TTL)
        left = _KYC_BVN_MAX_ATTEMPTS - attempts
        return fail(f"Incorrect code. {left} attempt(s) left.", status=400)
    cache.delete(cache_key)
    user.set_bvn(pending["bvn"])
    user.bvn_verified = True
    user.recompute_tier()
    user.save(update_fields=["bvn_hash", "bvn_last4", "bvn_verified", "tier"])
    _reserve_wallet_account(user, bvn=pending["bvn"])
    return ok(success=True, message="BVN verified", **_kyc_state(user))


@api
@ratelimit("kyc_bvn", limit=5, window=300)
@require_user
def kyc_bvn(request):
    """POST /api/kyc/bvn/ {access_token, bvn} -> verifies BVN, recomputes tier"""
    user = request.user_obj
    bvn = (request.data.get("bvn") or "").strip()
    result = verify_bvn(bvn, name=user.get_full_name() or "", mobile=user.phone or "")
    if not result.get("success"):
        return fail(result.get("message", "BVN verification failed"), status=400)
    user.set_bvn(bvn)
    user.bvn_verified = True
    user.recompute_tier()
    user.save(update_fields=["bvn_hash", "bvn_last4", "bvn_verified", "tier"])
    _reserve_wallet_account(user, bvn=bvn)
    return ok(success=True, message="BVN verified", **_kyc_state(user))


@api
@ratelimit("kyc_nin", limit=5, window=300)
@require_user
def kyc_nin(request):
    """POST /api/kyc/nin/ {access_token, nin} -> verifies NIN, recomputes tier"""
    user = request.user_obj
    nin = (request.data.get("nin") or "").strip()
    # Pass the account name so a NIN that demonstrably belongs to someone else is
    # rejected (mirrors kyc_bvn) — otherwise any valid NIN would lift the tier.
    result = verify_nin(nin, name=user.get_full_name() or "")
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
    _reserve_wallet_account(user, nin=nin)
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
    user.recompute_tier()  # face is a Tier 2 requirement
    user.save(update_fields=["face_verified", "tier"])
    return ok(success=True, message="Face verification recorded", **_kyc_state(user))


@api
@require_user
def kyc_address(request):
    """POST /api/kyc/address/ {access_token, address, city?, state?, document?}

    Verifies a residential address (Tier 2, together with face). Accepts an
    address (optionally city/state and a proof-of-address document); marks the
    address verified on success and recomputes the tier.
    """
    user = request.user_obj
    address = (request.data.get("address") or "").strip()
    if len(address) < 6:
        return fail("Enter your full residential address")
    city = (request.data.get("city") or "").strip()
    state = (request.data.get("state") or "").strip()
    full = ", ".join(p for p in [address, city, state] if p)
    result = kyc_verify_address(full, document=request.data.get("document") or "")
    if not result.get("success"):
        return fail(result.get("message", "Couldn't verify your address"), status=400)
    user.set_address(full)
    user.address_verified = True
    user.recompute_tier()
    user.save(update_fields=["address", "address_verified", "tier"])
    # Sync the bank-side NUBAN tier (best-effort; needs only the address + NUBAN).
    _sync_wema_tier3(user, {"fullAddress": full, "city": city, "state": state})
    return ok(success=True, message="Address verified", **_kyc_state(user))


@api
@require_user
def kyc_id_document(request):
    """POST /api/kyc/id/ {access_token, image, doc_type?}

    Verifies a government-issued ID document (Tier 3): passport / driver's
    licence / voter's card / NIN slip. Only the verified flag and the document
    type are retained — never the raw image.
    """
    user = request.user_obj
    image = request.data.get("image") or request.data.get("document") or ""
    doc_type = (request.data.get("doc_type") or "").strip()[:32]
    if not image:
        return fail("Upload a clear photo of your government ID")
    result = kyc_verify_id_document(image, doc_type=doc_type)
    if not result.get("success"):
        return fail(result.get("message", "Couldn't verify your ID document"), status=400)
    user.id_document_type = doc_type or "generic"
    user.id_document_verified = True
    user.recompute_tier()
    user.save(update_fields=["id_document_type", "id_document_verified", "tier"])
    return ok(success=True, message="ID document verified", **_kyc_state(user))

