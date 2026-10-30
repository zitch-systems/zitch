import logging
import re
import secrets
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import IntegrityError, transaction as db_transaction
from django.db.models import F, Q
from django.http import HttpResponse, JsonResponse
from django.utils import timezone

from common.http import (api, fail, mask_pii, ok, require_user, resolve_token,
                         verify_transaction_pin)
from common.ratelimit import (
    clear_login_failures,
    client_ip,
    login_locked,
    note_login_failure,
    ratelimit,
)

log = logging.getLogger("zitch.security")
from utility.liveness import verify_tier2_liveness
from utility.providers import (
    kyc_verify_id_document, mock_disabled_in_prod, send_email, send_sms, sms_live,
)
from wallet.services import get_or_create_wallet

from .models import (
    OTP, AccessToken, PushDevice, RefreshToken, User, password_rejection,
)

# The shared design system in common.emails is the only place email HTML lives.
# This alias keeps the name that accounts, whatsapp and admin already import.
from common.emails import branded_message as _branded_email  # noqa: E402


def _session_device_id(request) -> str:
    """Per-install identifier to bind a newly issued customer session."""
    return (request.headers.get("X-Zitch-Device") or "").strip()[:64]


def spend_account_namespace(user) -> str:
    """Stable, pseudonymous account identity for device-local spend attempts.

    A device can be shared by multiple Zitch customers. The mobile app needs a
    stable account namespace so an unresolved idempotency key from one customer
    is never replayed by another. This random value is persisted on the user: it
    survives access-token, signing-secret and region rotation and contains no PII.
    """
    namespace = getattr(user, "spend_namespace", None)
    if namespace:
        return str(namespace)
    if not getattr(user, "pk", None):
        raise ValueError("A persisted user is required for a spend namespace")

    # During an expand/contract deployment the previous release can create a
    # user without this newly-added field. Claim the NULL with one conditional
    # UPDATE: concurrent requests either write the candidate or observe the UUID
    # written by the winner; neither can overwrite the other. A savepoint keeps
    # the vanishingly unlikely UUID collision from poisoning an outer atomic
    # request so a fresh candidate can be tried safely.
    for _attempt in range(3):
        candidate = uuid.uuid4()
        try:
            with db_transaction.atomic():
                claimed = User.objects.filter(
                    pk=user.pk, spend_namespace__isnull=True,
                ).update(spend_namespace=candidate)
        except IntegrityError:
            continue
        if claimed:
            user.spend_namespace = candidate
            return str(candidate)

        namespace = (User.objects.filter(pk=user.pk)
                     .values_list("spend_namespace", flat=True).first())
        if namespace:
            user.spend_namespace = namespace
            return str(namespace)
        raise User.DoesNotExist("User disappeared while assigning spend namespace")

    raise RuntimeError("Unable to allocate a unique spend namespace")


def _session_payload(user, request) -> dict:
    """The fields every authenticating endpoint returns, so a session is issued
    the same way whether it came from a sign-in, a signup OTP or a password reset.

    `access_token` stays short-lived (TOKEN_TTL_HOURS) and `refresh_token` carries
    the session past it. `expires_in` is advertised so a client CAN renew ahead of
    the expiry; the app today does not, it refreshes on the 401 and retries the
    request transparently, which the customer never sees. The field is here so
    pre-emptive renewal doesn't need a second API change to become possible.
    """
    device_id = _session_device_id(request)
    refresh = RefreshToken.issue(user, device_id=device_id)
    access = AccessToken.issue(user, device_id=device_id, session_started_at=refresh.family_started)
    return {"access_token": access.key,
            "refresh_token": refresh.key,
            "account_namespace": spend_account_namespace(user),
            "expires_in": int(settings.TOKEN_TTL_HOURS) * 3600}


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


def _delivery_must_be_real() -> bool:
    """True on any deploy where a mocked "sent" would be a lie told to a real person.

    The provider wrappers return a MOCK SUCCESS when unconfigured (utility.providers):
    useful in local dev and in tests, dishonest anywhere a customer is waiting on a
    code. Deliberately NOT mock_disabled_in_prod(), which exempts simulation deploys —
    a simulation deploy fakes MONEY, not people: its codes still have to reach a real
    handset.
    """
    return not settings.DEBUG and not getattr(settings, "TESTING", False)


def _test_otp_phone(phone: str) -> bool:
    """True when `phone` is the configured TEST-ONLY bypass number (settings.TEST_OTP).

    That pair exists precisely so signup can be walked end to end while a real sender
    ID awaits carrier approval — i.e. exactly while the SMS rail cannot deliver — and
    OTP.verify_code accepts its fixed code with no SMS involved. So the guard below
    must not lock it out.
    """
    test = settings.TEST_OTP
    return bool(test["PHONE"] and test["CODE"] and phone == test["PHONE"])


def _otp_undeliverable(phone: str) -> bool:
    """True when a signup OTP for `phone` cannot actually reach anyone.

    The signup code is SMS-only (see phone_verification), and send_sms returns a mock
    success when TERMII_API_KEY is unset — so an unkeyed production deploy answers
    "a verification code has been sent" and sends nothing, with no error anywhere.
    That silent failure is the whole reason this check exists; the KYC identity flow
    below already refuses on the same grounds.

    Safe against enumeration, which is what the rest of this endpoint is built to
    resist: the answer is a property of the DEPLOY's configuration and is identical
    for every number (bar the test pair), so it cannot tell a registered number apart
    from an unregistered one.
    """
    if _test_otp_phone(phone):
        return False
    return _delivery_must_be_real() and not sms_live()


def _weak_password(password: str, user=None) -> str | None:
    """Returns a user-facing reason if the password is unacceptable, else None.

    Delegates to accounts.models.password_rejection, which is also what the
    WhatsApp signup Flow calls. The rule has to be one function: the strength
    tick-boxes on the sign-up screen ("a letter, a number, a special character")
    were CLIENT-side only, so a direct API call could set a password the app
    would have refused to let anyone type — and once WhatsApp became a second
    front door, two independent rules would have meant the weaker one is the one
    that gets used.
    """
    return password_rejection(password, user) or None


def _signup_contacts(data):
    """Validate untrusted contact input before creating a challenge or sending."""
    raw_phone = data.get("phone")
    raw_email = data.get("email", "")
    if raw_email is None:
        raw_email = ""
    if not isinstance(raw_phone, str) or not isinstance(raw_email, str):
        return "", "", fail("Enter a valid phone number and email address")
    phone, email = raw_phone.strip(), raw_email.strip()
    if not re.fullmatch(r"\+?[0-9]{8,15}", phone):
        return "", "", fail("Enter a valid phone number")
    if email:
        from django.core.exceptions import ValidationError
        from django.core.validators import validate_email

        try:
            validate_email(email)
        except ValidationError:
            return "", "", fail("Enter a valid email address")
        if len(email) > 254:
            return "", "", fail("Enter a valid email address")
    return phone, email, None


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
    # Score the device this sign-in came from and remember it. Deliberately does not
    # gate the sign-in: the password was correct, and refusing on a heuristic would
    # lock out customers who bought a new phone. What it buys is a durable trail from
    # the first minute of a takeover instead of a reconstruction from rotated logs —
    # and it is what makes the new-device step-up on a large spend meaningful.
    from common.risk import evaluate_login
    evaluate_login(request, user)
    return ok(**_session_payload(user, request), message="Signed in")


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
    phone, email, contact_error = _signup_contacts(request.data)
    if contact_error is not None:
        return contact_error
    if _otp_undeliverable(phone):
        return fail("SMS verification is temporarily unavailable. Please try again later.",
                    status=503)
    if not _otp_on_cooldown(phone):
        existing = User.objects.filter(phone=phone).first()
        if existing is not None:
            # Owner-only channels: tell the real number / their email on file,
            # never the API caller.
            reminder = "You already have a Zitch account. Open the app to sign in, or use 'Forgot password' to reset."
            delivery = send_sms(phone, reminder)
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
            delivery = send_sms(phone, f"Your Zitch verification code is {code}")
        if not delivery.get("success"):
            return fail("SMS verification is temporarily unavailable. Please try again later.", status=503)
    return ok(message="If this number can be registered, a verification code has been sent.")


@ratelimit("otp_verify", limit=20, window=60)
@api
def verify_otp(request):
    """POST /api/verify_otp/ {otp, phone} -> creates user + {access_token}"""
    raw_phone, raw_code = request.data.get("phone"), request.data.get("otp")
    if not isinstance(raw_phone, str) or not isinstance(raw_code, str):
        return fail("Phone and OTP are required")
    phone, code = raw_phone.strip(), raw_code.strip()
    if not phone or not code:
        return fail("Phone and OTP are required")
    # The customer's legal name is captured at register (before this OTP round-trip)
    # and sent here so the account is created WITH a name. It matters beyond display:
    # the dedicated funding account (9PSB NUBAN) is later opened in this name, and an
    # account with no holder name can't be safely funded by transfer — the payer has
    # no name to confirm against. Trim + length-cap to the User field width.
    names = [request.data.get(field) or "" for field in ("first_name", "last_name")]
    if not all(isinstance(value, str) for value in names):
        return fail("Enter your first and last name")
    first_name, last_name = (value.strip()[:150] for value in names)
    with db_transaction.atomic():
        # Existing accounts always lock before their challenge, matching contact
        # verification/reset. The challenge itself serializes first-time signup.
        user = User.objects.select_for_update().filter(phone=phone).first()
        otp = (OTP.objects.select_for_update().filter(phone=phone, purpose=OTP.SIGNUP)
               .order_by("-created", "-pk").first())
        # Select the newest challenge including consumed ones: an old unused
        # code must never reopen after a newer code has already been used.
        if otp is None or otp.used:
            return fail("Invalid OTP", status=400)
        if otp.is_expired:
            return fail("OTP has expired", status=400)
        if otp.too_many_attempts:
            return fail("Too many incorrect attempts. Request a new code.", status=429)
        if not otp.verify_code(code):
            OTP.objects.filter(pk=otp.pk, used=False, attempts__lt=OTP.MAX_ATTEMPTS).update(
                attempts=F("attempts") + 1)
            return fail("Invalid OTP", status=400)
        if user is not None and (not user.is_active or user.has_usable_password()
                                 or (user.email or "").strip().lower()
                                 != (otp.email or "").strip().lower()):
            log.warning("signup_otp_for_existing_account phone=%r ip=%s",
                        mask_pii(phone), client_ip(request))
            return fail("Invalid OTP", status=400)
        if user is None and User.objects.filter(username=phone).exists():
            # Older contact edits could leave a retired phone as another user's
            # unique username. SMS proof never authorizes merging those accounts.
            return fail("We need to review this phone number before registration. Contact Zitch Support.",
                        status=409, code="registration_review_required")
        if not OTP.objects.filter(pk=otp.pk, used=False,
                                  attempts__lt=OTP.MAX_ATTEMPTS).update(used=True):
            return fail("Invalid OTP", status=400)
        if user is None:
            try:
                with db_transaction.atomic():
                    user, created = User.objects.get_or_create(
                        phone=phone,
                        defaults={"username": phone, "email": otp.email or "",
                                  "first_name": first_name, "last_name": last_name,
                                  "password": make_password(None), "phone_verified": True},
                    )
            except IntegrityError:
                return fail("We need to review this phone number before registration. Contact Zitch Support.",
                            status=409, code="registration_review_required")
            # Another onboarding channel can create this phone while signup was
            # waiting. Require a fresh challenge against that current account,
            # rather than edit a user that we could not lock before the OTP.
            if not created:
                return fail("Invalid OTP", status=400)
        fields = ["phone_verified", "tier"]
        user.phone_verified = True
        if (first_name or last_name) and not (user.first_name or user.last_name):
            user.first_name, user.last_name = first_name, last_name
            fields.extend(["first_name", "last_name"])
        user.recompute_tier()
        user.save(update_fields=fields)
        get_or_create_wallet(user)
        payload = _session_payload(user, request)
    return ok(**payload, message="Verified")


@ratelimit("otp_send", limit=5, window=60)
@api
def resend_verify_otp(request):
    """POST /api/resend_verify_otp/ {phone, email?}

    Carries the email from the original phone_verification forward so the
    verified user is created with the right email and set-password works.
    The client may also pass `email` explicitly to override.
    """
    phone, supplied_email, contact_error = _signup_contacts(request.data)
    if contact_error is not None:
        return contact_error
    if _otp_undeliverable(phone):
        return fail("SMS verification is temporarily unavailable. Please try again later.",
                    status=503)
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
        email = supplied_email
        if not email:
            prior = (OTP.objects.filter(phone=phone, purpose=OTP.SIGNUP)
                     .order_by("-created", "-pk").first())
            email = prior.email if prior else ""
    code = _otp_code()
    OTP.issue(phone=phone, code=code, email=email)
    # SMS ONLY, same reason as phone_verification: a signup OTP authenticates into
    # the matching account, so it must reach only the PHONE being registered —
    # never a client-supplied email. The email is stored for the account record.
    delivery = send_sms(phone, f"Your Zitch verification code is {code}")
    if not delivery.get("success"):
        return fail("SMS verification is temporarily unavailable. Please try again later.", status=503)
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
    raw_ident = request.data.get("email_or_phone")
    if raw_ident is None or raw_ident == "":
        raw_ident = request.data.get("phone", "")
    if not isinstance(raw_ident, str) or len(raw_ident) > 254:
        return fail("Enter a valid phone number or email address")
    ident = raw_ident.strip()
    if not ident:
        return fail("Phone or email is required")
    if not re.fullmatch(r"\+?[0-9]{8,15}", ident):
        from django.core.exceptions import ValidationError
        from django.core.validators import validate_email

        try:
            validate_email(ident)
        except ValidationError:
            return fail("Enter a valid phone number or email address")
    user = User.objects.filter(phone=ident).first() or User.objects.filter(email__iexact=ident).first()
    if user is not None and user.is_active and user.phone and not _otp_on_cooldown(user.phone):
        code = _otp_code()
        OTP.issue(phone=user.phone, code=code, email=user.email or "", purpose=OTP.RESET)
        message = f"Your Zitch password reset code is {code}"
        send_sms(user.phone, message)
        # A chat-onboarded account's email was typed into WhatsApp and never
        # proven — one typo from being someone else's inbox. Until the in-app
        # round-trip verifies it, the reset code goes to the phone alone, which
        # is the identity these accounts actually authenticated with.
        reset_email = "" if _email_unproven_for_recovery(user) else (user.email or "")
        send_email(reset_email, "Your Zitch password reset code", message,
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
    raw_ident = request.data.get("email_or_phone") or request.data.get("phone") or ""
    raw_code = request.data.get("otp") or ""
    password = request.data.get("password") or ""
    if not all(isinstance(value, str) for value in (raw_ident, raw_code, password)):
        return fail("Phone/email, reset code and password are required")
    ident, code = raw_ident.strip(), raw_code.strip()
    if not ident or not code:
        return fail("Phone/email and reset code are required")
    weak = _weak_password(password)
    if weak:
        return fail(weak)

    # Resolve the account first; a generic 400 if unknown so the endpoint can't
    # be used to tell a registered identifier from an unregistered one.
    with db_transaction.atomic():
        user = (User.objects.select_for_update().filter(phone=ident).first()
                or User.objects.select_for_update().filter(email__iexact=ident).first())
        if user is None or not user.is_active or not user.phone:
            return fail("Invalid reset code", status=400)
        otp = (OTP.objects.select_for_update().filter(phone=user.phone, purpose=OTP.RESET)
               .order_by("-created", "-pk").first())
        if (otp is None or otp.used or (otp.email or "").strip().lower()
                != (user.email or "").strip().lower()):
            return fail("Invalid reset code", status=400)
        if otp.is_expired:
            return fail("Reset code has expired", status=400)
        if otp.too_many_attempts:
            return fail("Too many incorrect attempts. Request a new code.", status=429)
        if not otp.verify_code(code):
            OTP.objects.filter(pk=otp.pk, used=False, attempts__lt=OTP.MAX_ATTEMPTS).update(
                attempts=F("attempts") + 1)
            return fail("Invalid reset code", status=400)
        if not OTP.objects.filter(pk=otp.pk, used=False,
                                  attempts__lt=OTP.MAX_ATTEMPTS).update(used=True):
            return fail("Invalid reset code", status=400)
        user.set_password(password)
        user.save(update_fields=["password"])
        # Reset and session revocation share the challenge's transaction, so a
        # duplicate request cannot change the password or revoke the winner.
        user.tokens.all().delete()
        user.refresh_tokens.all().update(revoked_at=timezone.now())
        payload = _session_payload(user, request)
    return ok(**payload, message="Password reset")


@api
@require_user
@db_transaction.atomic
def logout(request):
    """POST /api/logout/ {access_token} — revokes the presented token.

    Server-side revocation so a signed-out (or otherwise leaked-then-cleared)
    token can't be replayed for the remainder of its TTL.
    """
    # Serialize logout with notification registration. A registration that was
    # authenticated before logout must not recreate the revoked subscription.
    User.objects.select_for_update().only("pk").get(pk=request.user_obj.pk)
    # A signed-out phone must stop receiving private account alerts. Scope the
    # removal to this installation so tablets/other phones stay subscribed.
    device_id = _session_device_id(request)
    if device_id:
        PushDevice.objects.filter(user=request.user_obj, device_id=device_id).update(enabled=False)
    AccessToken.objects.filter(key=AccessToken._hash(resolve_token(request))).delete()
    # Deleting only the access token would leave the refresh chain live, so
    # "sign out" would mean "signed out for a few hours". Revoke the presented
    # chain when the client sends it, and fall back to this install's chains
    # when it doesn't — an older client that has no refresh token to send must
    # still get a real sign-out.
    presented = request.data.get("refresh_token") or ""
    presented = presented.strip() if isinstance(presented, str) else ""
    if presented:
        row = RefreshToken.objects.filter(key=RefreshToken._hash(presented),
                                          user=request.user_obj).first()
        if row is not None:
            RefreshToken.revoke_family(row.user_id, row.family)
    elif device_id:
        RefreshToken.objects.filter(user=request.user_obj, device_id=device_id,
                                    revoked_at__isnull=True).update(
            revoked_at=timezone.now())
    return ok(message="Logged out")


@ratelimit("token_refresh", limit=60, window=300)
@api
def token_refresh(request):
    """POST /api/token/refresh/ {refresh_token} -> a new access + refresh pair.

    Deliberately NOT behind @require_user: the whole point is to be reachable
    with an access token that has already expired. The refresh token is the only
    credential, and RefreshToken.rotate does every check.

    One 401 message for every refusal — unknown, expired, revoked, reused, wrong
    device, deactivated account. Naming which one would tell a caller holding a
    stolen token whether it was ever valid, whether the chain is still live, and
    whether they guessed the device, so the distinction stays in the logs (where
    a `refresh_token_reuse` line is a theft signal worth alerting on) and out of
    the response.
    """
    presented = (request.data.get("refresh_token") or "").strip()
    fresh, outcome = RefreshToken.rotate(presented, device_id=_session_device_id(request))
    if fresh is None:
        log.info("token_refresh_refused reason=%s ip=%s", outcome, client_ip(request))
        return fail("Your session has expired. Please sign in again.", status=401)
    user = outcome
    access = AccessToken.issue(user, device_id=fresh.device_id, session_started_at=fresh.family_started)
    return ok(access_token=access.key, refresh_token=fresh.key,
              account_namespace=spend_account_namespace(user),
              expires_in=int(settings.TOKEN_TTL_HOURS) * 3600,
              message="Session renewed")


@api
@require_user
def push_register(request):
    """POST /api/push/register/ {token, platform}.

    Expo tokens are opaque credentials for addressing a device, not proof of a
    user. Ownership therefore comes exclusively from the authenticated session;
    a client-supplied user id is never accepted.
    """
    token = request.data.get("token") or ""
    if not isinstance(token, str):
        return fail("Invalid push token")
    token = token.strip()
    if len(token) > 255 or not re.fullmatch(r"(?:Exponent|Expo)PushToken\[[A-Za-z0-9_-]+\]", token):
        return fail("Invalid push token")
    platform = request.data.get("platform") or ""
    if not isinstance(platform, str):
        return fail("Invalid device platform")
    platform = platform.strip().lower()
    if platform not in ("android", "ios"):
        return fail("Invalid device platform")
    device_id = _session_device_id(request)
    with db_transaction.atomic():
        User.objects.select_for_update().only("pk").get(pk=request.user_obj.pk)
        raw_session = resolve_token(request)
        session = AccessToken.objects.select_for_update().filter(
            key=AccessToken._hash(raw_session), user=request.user_obj,
            scope=AccessToken.APP).first()
        if session is None or AccessToken.resolve(
                raw_session, required_scope=AccessToken.APP, device_id=device_id) is None:
            return fail("Invalid or expired session", status=401)
        if not session.device_id:
            return fail("Sign in again to enable notifications on this device.", status=409,
                        code="device_session_required")
        epoch = session.session_started_at or session.created
        stale = fail("A newer sign-in controls notifications on this device.", status=409,
                     code="push_session_superseded")
        if PushDevice.objects.filter(user=request.user_obj, device_id=session.device_id,
                                      session_started_at__gt=epoch).exists():
            return stale
        # The unique token arbitrates first-registration races across different
        # users. get_or_create retries a concurrent insert; select_for_update
        # then holds that winning row before examining or changing ownership.
        device, created = PushDevice.objects.select_for_update().get_or_create(
            token=token, defaults={"user": request.user_obj, "device_id": session.device_id,
                                   "platform": platform, "enabled": False,
                                   "session_started_at": epoch})
        if not created:
            watermark = device.session_started_at or device.last_seen
            legacy_owner = (device.session_started_at is None and device.enabled
                            and device.user_id == request.user_obj.pk)
            if not legacy_owner and (watermark > epoch or (not device.enabled and watermark >= epoch)):
                return stale
        # Rotation must retire this customer's previous token on this install.
        # A supplied device header alone cannot remove another customer's token,
        # or authorize cleanup for older sessions without an installation binding.
        PushDevice.objects.filter(user=request.user_obj, device_id=session.device_id).exclude(
            token=token).update(enabled=False, session_started_at=epoch)
        device.user = request.user_obj
        device.device_id = session.device_id
        device.platform = platform
        device.enabled = True
        device.session_started_at = epoch
        device.save(update_fields=["user", "device_id", "platform", "enabled",
                                   "session_started_at", "last_seen"])
    return ok(message="Notifications enabled")


@api
# Rate-limited because this endpoint CHECKS the current password, which makes it
# a password-guessing oracle for anyone holding a session token — and one that
# completely bypasses the signin lockout, since that counts failures against the
# login form. Unlimited silent guesses at the credential that gates email/phone
# changes and the PIN-reset fallback is not something a session token should buy.
@ratelimit("set_password", limit=10, window=300)
@require_user
def set_password(request):
    """POST /api/set-password/ {access_token, password}

    Authenticated: acts on the token's user. Previously this looked the user up
    by an email in the body with no auth, letting anyone overwrite any account's
    password (and an empty email matched an arbitrary blank-email account).
    """
    password = request.data.get("password") or ""
    current = request.data.get("current_password") or ""
    if not isinstance(password, str) or not isinstance(current, str):
        return fail("Enter a valid password")
    # Changing an EXISTING password requires the current one, so a stolen session
    # token alone can't overwrite it. First-time onboarding runs set-password
    # before any real password exists. Legacy signup rows used an empty hash;
    # current signups use an explicitly unusable password. Neither is a credential.
    # Revoke other sessions on a credential change: any token issued before this
    # change is now invalid. Keep the caller's current token so the onboarding
    # flow (set-password -> set-pin) and a change-password screen don't 401.
    # ...and the REFRESH chains behind them, exactly as password_reset does above
    # and for the same reason. Deleting only access tokens left a stolen refresh
    # family alive for up to REFRESH_ABSOLUTE_DAYS, so the victim performed the one
    # remediation everybody knows — change your password — was told "Password set",
    # and the attacker kept minting fresh access tokens the whole time. Device
    # binding is no obstacle: the device id is a client-supplied header.
    #
    # Every chain goes, the caller's included, and the caller is handed a new one in
    # the response: keeping theirs alive would mean deciding which chain is the
    # victim's on the strength of that same spoofable header.
    with db_transaction.atomic():
        user = User.objects.select_for_update().get(pk=request.user_obj.pk)
        if not user.is_active:
            return fail("Your session has expired. Please sign in again.", status=401)
        if user.password and user.has_usable_password():
            if not (current and user.check_password(current)):
                return fail("Enter your current password to change it",
                            status=403, code="current_password_required")
        weak = _weak_password(password, user)
        if weak:
            return fail(weak)
        user.set_password(password)
        user.save(update_fields=["password"])
        user.tokens.exclude(key=AccessToken._hash(resolve_token(request))).delete()
        user.refresh_tokens.all().update(revoked_at=timezone.now())
        fresh = RefreshToken.issue(user, device_id=_session_device_id(request))
    return ok(message="Password set", refresh_token=fresh.key)


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
    from accounts.models import transaction_pin_rejection
    from common.http import evaluate_transaction_pin

    raw_pin = request.data.get("pin") or ""
    raw_old_pin = request.data.get("old_pin") or ""
    password = request.data.get("password") or ""
    if not all(isinstance(value, str) for value in (raw_pin, raw_old_pin, password)):
        return fail("Enter a valid PIN", code="weak_pin")
    pin, old_pin = raw_pin.strip(), raw_old_pin.strip()
    rejected = transaction_pin_rejection(pin)
    if rejected:
        return fail(rejected, code="weak_pin")
    with db_transaction.atomic():
        user = User.objects.select_for_update().get(pk=request.user_obj.pk)
        if not user.is_active:
            return fail("Your session has expired. Please sign in again.", status=401)
        # old_pin MUST go through the brute-force-protected checker (lockout after
        # PIN_MAX_ATTEMPTS) — a raw check_transaction_pin here let a stolen token
        # guess the 4-digit PIN unlimited times and overwrite it. The password
        # fallback keeps forgot-PIN recovery working.
        if user.transaction_pin:
            ok_pwd = bool(password) and user.check_password(password)
            ok_old = False
            if not ok_pwd and old_pin:
                ok_old, code, message = evaluate_transaction_pin(user, old_pin)
                if code == "pin_locked":
                    return fail(message, status=403, code="pin_locked")
            if not (ok_old or ok_pwd):
                return fail("Enter your current PIN to change it",
                            status=403, code="current_pin_required")
        # set_transaction_pin also clears pin_reset_required and the whole
        # brute-force lockout (counter, deadline and escalation strikes), so a
        # legitimate password-authenticated PIN change isn't blocked by a stale lock
        # against the PIN it just replaced. Saving the named set rather than a
        # hand-written list is what keeps this in step when that method grows.
        user.set_transaction_pin(pin)
        user.save(update_fields=list(User.PIN_UPDATE_FIELDS))
    return ok(message="Transaction PIN set")


@api
@ratelimit("verify_pin", limit=5, window=300)
@require_user
def verify_pin(request):
    """POST /api/verify-transaction-pin/ {access_token, pin} -> {success}

    Confirms the caller knows their own PIN without moving money. It exists for one
    caller: turning on biometric payment approval, which caches the PIN on-device
    behind the OS biometric ACL and replays it for every later payment. Caching an
    UNVERIFIED PIN meant a single typo auto-submitted a wrong PIN on every payment
    sheet from then on, escalating the lockout — 60 minutes, then 24 hours, across
    the app AND WhatsApp — while nothing told the customer which credential was
    wrong or that they had ever mistyped it.

    Routed through verify_transaction_pin, the same brute-force-protected checker
    every money endpoint already uses, so this is not a softer oracle than the
    payment path: a wrong guess costs an attempt and locks out identically. The
    extra ratelimit bounds it further, since unlike a payment there is no amount,
    recipient or balance to make guessing expensive.
    """
    pin_err = verify_transaction_pin(request.user_obj, request.data.get("pin"))
    if pin_err:
        return pin_err
    return ok(success=True)


@api
@ratelimit("update_info", limit=10, window=300)
@require_user
def update_info(request):
    """POST /api/update_info/ {first_name, last_name, email, phone, access_token}

    Changing the email requires the current password (or transaction PIN), and
    drops its verified flag. Phone replacement needs a Support review until an
    authenticated replacement-number proof route is available.

    Both halves are load-bearing. password_forgot mails the reset code to whatever
    `user.email` currently holds, so without re-authentication a stolen session token
    alone bought a full takeover: swap the email, request a reset, set a new password
    — and password_reset then deletes the real owner's tokens, locking them out of
    the account they can no longer recover. That defeats the control set_password two
    functions above exists to provide.

    And an address the account has never proven must not stay marked verified: the
    KYC ladder and the phone_verified first-spend gate both read those flags, so
    keeping them would silently rest a tier on an unproven contact. Clearing them
    re-runs the ordinary OTP round-trip, which is the only thing that can restore it.

    A name-only update needs neither: it moves no trust and gates nothing.
    """
    from common.http import evaluate_transaction_pin

    data = request.data
    values = [data.get(field) or "" for field in ("email", "phone", "first_name", "last_name")]
    if not all(isinstance(value, str) for value in values):
        return fail("Enter valid account details")
    new_email, new_phone, first_name, last_name = (value.strip() for value in values)
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email

    if new_email:
        if len(new_email) > 254:
            return fail("Enter a valid email address")
        try:
            validate_email(new_email)
        except ValidationError:
            return fail("Enter a valid email address")
    if new_phone and not re.fullmatch(r"\+?[0-9]{8,15}", new_phone):
        return fail("Enter a valid phone number")
    if len(first_name) > 150 or len(last_name) > 150:
        return fail("First and last names must each be 150 characters or fewer")
    # Only validate uniqueness when the value is actually changing, so a plain
    # name update never trips on the user's own (or a legacy duplicate) value.
    # phone is unique in the DB — the pre-check turns a clash into a clean error
    # instead of a 500; email isn't unique but a clash would make sign-in (which
    # matches by email) ambiguous, so we guard it too.
    try:
        with db_transaction.atomic():
            # Authentication may have fetched this user before a KYC callback or
            # credential reset committed. Re-authenticate and edit the current
            # row; never write a full stale user over durable identity proof.
            user = User.objects.select_for_update().get(pk=request.user_obj.pk)
            if not user.is_active:
                return fail("Your session has expired. Please sign in again.", status=401)
            changing_phone = bool(new_phone and new_phone != (user.phone or ""))
            changing_email = bool(new_email and new_email.lower() != (user.email or "").lower())
            if changing_phone and User.objects.filter(phone=new_phone).exclude(pk=user.pk).exists():
                return fail("That phone number is already in use")
            if changing_phone:
                # Signup OTP deliberately cannot authenticate an established
                # account, and native KYC has no replacement-phone verifier.
                # Keep the proven phone/username and recovery access intact.
                return fail("To change your phone number, contact Zitch Support so we can verify the replacement safely.",
                            status=403, code="phone_change_unavailable")
            if changing_email and User.objects.filter(email__iexact=new_email).exclude(pk=user.pk).exists():
                return fail("That email is already in use")
            if changing_email or changing_phone:
                password = data.get("password") or data.get("current_password") or ""
                raw_pin = data.get("transaction_pin") or data.get("pin") or ""
                if not isinstance(password, str) or not isinstance(raw_pin, str):
                    return fail("Enter your password to change your email or phone number",
                                status=403, code="reauth_required")
                pin = raw_pin.strip()
                ok_pwd = bool(password) and user.check_password(password)
                ok_pin = False
                if not ok_pwd and pin:
                    ok_pin, code, message = evaluate_transaction_pin(user, pin)
                    if code == "pin_locked":
                        return fail(message, status=403, code="pin_locked")
                if not (ok_pwd or ok_pin):
                    return fail("Enter your password to change your email or phone number",
                                status=403, code="reauth_required")
            fields = []
            if first_name:
                user.first_name = first_name
                fields.append("first_name")
            if last_name:
                user.last_name = last_name
                fields.append("last_name")
            if new_email:
                user.email = new_email
                fields.append("email")
                if changing_email:
                    user.email_verified = False
                    fields.append("email_verified")
            old_phone = user.phone
            if new_phone:
                user.phone = new_phone
                fields.append("phone")
                if changing_phone:
                    user.phone_verified = False
                    fields.append("phone_verified")
            if changing_email or changing_phone:
                # Retire challenges for the previous contact snapshot, including
                # reset codes that may have reached a now-retired inbox/phone.
                OTP.objects.filter(phone=old_phone, used=False).update(used=True)
                user.recompute_tier()
                fields.append("tier")
            if fields:
                user.save(update_fields=fields)
    except IntegrityError:
        # The phone constraint can race the friendly pre-check on another user.
        return fail("That phone number is already in use")
    return ok(message="Account updated",
              email_verified=user.email_verified, phone_verified=user.phone_verified)


def avatar_url(request, user) -> str:
    """Absolute URL for a user's profile photo, or '' if none set."""
    from django.core.files.storage import default_storage

    if not user.avatar:
        return ""
    return request.build_absolute_uri(default_storage.url(user.avatar))


@api
@ratelimit("avatar_upload", limit=10, window=600)
@require_user
def avatar_upload(request):
    """POST /api/profile/avatar/ {access_token, image}
    `image` is a base64 data URL (or bare base64). Stores the photo and returns
    its absolute URL. -> {success, message, avatar}
    """
    import base64
    import binascii
    import secrets
    from io import BytesIO

    from django.core.files.base import ContentFile
    from django.core.files.storage import default_storage
    from PIL import Image, ImageOps, UnidentifiedImageError

    user = request.user_obj
    raw = (request.data.get("image") or request.data.get("avatar") or "").strip()
    if not raw:
        return fail("No image provided")

    if raw.startswith("data:"):
        _, _, b64 = raw.partition(",")
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

    # Decode the image rather than trusting a caller-controlled data-URL MIME or
    # filename. Re-encoding removes EXIF/GPS metadata, bounds decompression cost
    # and ensures object storage never serves arbitrary bytes as an image.
    try:
        with Image.open(BytesIO(blob)) as source:
            if source.width * source.height > 16_000_000:
                return fail("Image dimensions are too large")
            image = ImageOps.exif_transpose(source)
            image.thumbnail((1024, 1024))
            if image.mode in ("RGBA", "LA"):
                rgba = image.convert("RGBA")
                flattened = Image.new("RGB", rgba.size, "white")
                flattened.paste(rgba, mask=rgba.getchannel("A"))
                image = flattened
            elif image.mode != "RGB":
                image = image.convert("RGB")
            output = BytesIO()
            image.save(output, format="JPEG", quality=88, optimize=True)
            blob = output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError):
        return fail("Invalid image data")

    # Store and persist the replacement before deleting the previous object. A
    # transient object-storage failure must not erase the user's working photo.
    previous = user.avatar
    path = default_storage.save(
        f"avatars/{user.id}-{secrets.token_hex(4)}.jpg", ContentFile(blob),
    )
    try:
        user.avatar = path
        user.save(update_fields=["avatar"])
    except Exception:
        # The database still points at the previous photo; avoid orphaning the
        # newly uploaded object if that update fails.
        default_storage.delete(path)
        raise
    if previous and previous != path:
        try:
            default_storage.delete(previous)
        except Exception:
            # The account now points at the valid replacement. Cleanup can be
            # retried later; do not turn a successful upload into a client error.
            pass
    return ok(success=True, message="Photo updated",
              avatar=avatar_url(request, user))


# --------------------------------- KYC ---------------------------------
# Partnership OTP/face completions retain their server-bound bank attempts.
# Only durable proof can mark an identity verified or supply its legal account name.

_TIER_NAMES = {0: "Unverified", 1: "Verified", 2: "Enhanced", 3: "Premium"}
MAX_KYC_IMAGE_BASE64 = 2_800_000  # ~2 MiB decoded; bounds memory/PII exposure
_IDENTITY_CONFLICT_MESSAGE = (
    "This identity is already linked to another account. Contact support if this is unexpected."
)



def _email_verification_required(user) -> bool:
    """The bank identity flow requires both verified Zitch contact channels."""
    return not user.email_verified


def _email_unproven_for_recovery(user) -> bool:
    """A chat-claimed inbox cannot receive recovery codes until verified.

    This recovery policy is independent of the bank's identity prerequisites.
    The verified phone remains a recovery channel while the email is unproven.
    """
    return bool(getattr(user, "onboarded_via_whatsapp", False) and not user.email_verified)


def _email_gate(user):
    if _email_verification_required(user):
        return fail("Confirm your email address first — check Settings → Verify email.",
                    status=403)
    return None


@ratelimit("otp_send", limit=5, window=60)
@api
@require_user
def email_verify_start(request):
    """POST /api/email/verify/start/ {access_token} — email a code to the address
    on file. EMAIL ONLY, never SMS: this code proves control of the inbox, and
    delivering it to the phone would verify nothing."""
    # The address and its challenge change together, under the same user lock
    # confirmation takes. A resend cooldown must never quietly replace the
    # inbox while an older inbox's code remains live.
    with db_transaction.atomic():
        user = User.objects.select_for_update().get(pk=request.user_obj.pk)
        if not user.is_active:
            return fail("Your session has expired. Please sign in again.", status=401)
        if user.email_verified:
            return ok(success=True, message="Email already verified", **_kyc_state(user))
        supplied_email = request.data.get("email") or ""
        if not isinstance(supplied_email, str):
            return fail("Enter a valid email address")
        new_email = supplied_email.strip().lower()
        if new_email:
            from django.core.exceptions import ValidationError
            from django.core.validators import validate_email

            try:
                validate_email(new_email)
            except ValidationError:
                return fail("Enter a valid email address")
            if len(new_email) > 254:
                return fail("Enter a valid email address")
            if User.objects.filter(email__iexact=new_email).exclude(pk=user.pk).exists():
                return fail("That email is already on another account")
        target = new_email or (user.email or "").strip().lower()
        if not target:
            return fail("No email address on this account — include one in the request")
        if _otp_on_cooldown(user.phone):
            return fail("Wait a few seconds before requesting a new email code or changing the address.",
                        status=429, code="otp_cooldown")
        if user.email != target:
            user.email = target
            user.save(update_fields=["email"])
        OTP.objects.filter(phone=user.phone, purpose=OTP.EMAIL, used=False).update(used=True)
        code = _otp_code()
        challenge = OTP.issue(phone=user.phone, code=code, email=target, purpose=OTP.EMAIL)
    # Keep the external delivery call outside the database locks. Its result
    # does not authorize a different contact if another request changes it.
    try:
        delivery = send_email(target, "Confirm your email for Zitch",
                              f"Your Zitch email confirmation code is {code}",
                              html=_branded_email("Confirm your email",
                                  "Enter this code in Zitch to confirm your email address.",
                                  code=code,
                                  note="If you didn't request this, you can ignore this email."))
    except Exception:
        delivery = {"success": False}
    if (not isinstance(delivery, dict) or delivery.get("success") is not True
            or (delivery.get("mock") and mock_disabled_in_prod())):
        OTP.objects.filter(pk=challenge.pk, used=False).update(used=True)
        return fail("We could not send your email code. Please try again shortly.", status=503)
    # success=True is load-bearing, not decoration: the app advances to the code
    # screen on this flag, and ok() deliberately does not default it (a queued
    # transfer answers 200 with success LEFT OUT so it reads as "processing").
    # Dropping it here strands the customer on the email step with a 200 that the
    # app reads as a failure — and email is a Tier 1 requirement, so the whole
    # ladder stops. It has been dropped once already; leave it explicit.
    return ok(success=True, message=f"We sent a code to {user.email}")


@ratelimit("otp_verify", limit=20, window=60)
@api
@require_user
def email_verify_confirm(request):
    """POST /api/email/verify/confirm/ {access_token, otp} — mark the email
    verified. Filtered to purpose=EMAIL so a signup or reset code can never
    stand in for inbox control."""
    raw_code = request.data.get("otp") or ""
    if not isinstance(raw_code, str) or not raw_code.strip():
        return fail("Enter the code from the email")
    code = raw_code.strip()
    with db_transaction.atomic():
        # Consistent user -> OTP order serializes contact edits, guesses and
        # single-use consumption. Derive the tier from the current KYC flags.
        user = User.objects.select_for_update().get(pk=request.user_obj.pk)
        if not user.is_active:
            return fail("Your session has expired. Please sign in again.", status=401)
        otp = (OTP.objects.select_for_update().filter(phone=user.phone, purpose=OTP.EMAIL)
               .order_by("-created", "-pk").first())
        target = (user.email or "").strip().lower()
        if (otp is None or otp.used or otp.is_expired or not target
                or (otp.email or "").strip().lower() != target):
            return fail("Invalid or expired code. Request a new code for your current email address.",
                        status=400)
        if otp.too_many_attempts:
            return fail("Too many incorrect attempts. Request a new code.", status=429)
        if not otp.verify_code(code):
            otp.attempts += 1
            otp.save(update_fields=["attempts"])
            return fail("Invalid code", status=400)
        # The conditional claim additionally prevents a duplicate grant on
        # databases without row locks; no older challenge can reopen afterward.
        if not OTP.objects.filter(pk=otp.pk, used=False).update(used=True):
            return fail("Invalid or expired code", status=400)
        user.email_verified = True
        user.recompute_tier()
        user.save(update_fields=["email_verified", "tier"])
    return ok(success=True, message="Email verified", **_kyc_state(user))



def _kyc_state(user) -> dict:
    from banking.accounts import account_for, account_payload
    from banking.models import IdentityChallenge
    from banking.address import verification_status
    from banking.client import config
    from accounts.models import IdentityProof
    bank = account_for(user)
    proofs = set(IdentityProof.objects.filter(user=user, source__in=["9psb_otp", "9psb_face"]).values_list("identity_type", flat=True))
    return {**account_payload(user), "tier": user.tier, "tier_name": _TIER_NAMES.get(user.tier, "Unverified"),
        "transaction_limit": str(user.transaction_limit), "daily_transfer_limit": str(user.daily_transfer_limit),
        "daily_bill_limit": str(user.daily_bill_limit), "limit_source": "Zitch platform limits",
        "bvn_verified": "bvn" in proofs, "nin_verified": "nin" in proofs,
        "face_verified": IdentityProof.objects.filter(user=user, source="9psb_face").exists(),
        **verification_status(user),
        "account_provider": "9psb", "email": user.email or "", "email_verified": user.email_verified,
        "phone_verified": user.phone_verified, "email_verification_required": _email_verification_required(user),
        "large_txn_threshold": str(User.LARGE_TXN_THRESHOLD), "bank_tier": bank.tier if bank else 0,
        "has_bank_account": bool(bank), "identity_verification_provider": "9psb",
        "identity_verification_methods": ["OTP", "FACIAL"], "face_rail": "9psb", "tier2_face_rail": "9psb",
        "tier2_requires_nin": True, "address_rail": "prembly", "address_verification_required": True,
        "bank_tier_limits": {"single_inflow": str(bank.maximum_deposit) if bank and bank.maximum_deposit is not None else None,
            "max_balance": str(bank.maximum_balance) if bank and bank.maximum_balance is not None else None, "daily_spend": None},
        "identity_processing": IdentityChallenge.objects.filter(user=user, environment=config().get("ENVIRONMENT", "sandbox"), state__in=["initiating", "verifying", "verified", "opening"]).exists()}


def _kyc_image_error(value) -> str | None:
    if not isinstance(value, str) or not value:
        return "Upload a clear image"
    if len(value) > MAX_KYC_IMAGE_BASE64:
        return "Image is too large. Choose a photo under 2 MB."
    return None


@api
@require_user
def set_transaction_limit(request):
    """POST /api/limits/transaction/ {access_token, limit, pin}

    Lets a customer tighten their own per-transaction limit. `limit` is a naira
    amount, or the string "off"/"" to remove the self-limit and go back to the
    tier ceiling.

    THE LIMIT CAN ONLY GO DOWN. The tier ceiling is a KYC/compliance bound and is
    not negotiable from settings: a higher limit is earned by verifying identity,
    never by asking. So a value above the ceiling is refused rather than clamped —
    silently storing something other than what the customer typed, on the screen
    that tells them what their limit is, would be its own bug.

    The PIN is required for the same reason it guards a payment. A self-imposed
    limit is a control a thief holding a live session would want gone first, and
    raising it back to the ceiling is worth exactly as much to them as a transfer.
    It goes through the brute-force-protected checker, so this cannot be used as
    an unlimited PIN oracle.
    """
    from common.http import evaluate_transaction_pin

    user = request.user_obj
    raw = str(request.data.get("limit") or "").strip().lower()
    pin = (request.data.get("pin") or "").strip()

    if not user.transaction_pin:
        return fail("Set a transaction PIN first", status=403, code="no_pin")
    ok_pin, code, message = evaluate_transaction_pin(user, pin)
    if not ok_pin:
        return fail(message, status=403, code=code)

    ceiling = user.tier_transaction_limit
    if raw in ("", "off", "none", "null"):
        chosen = None
    else:
        try:
            # Commas and a naira sign are how people write an amount; rejecting
            # "50,000" as malformed would be pedantry, not validation.
            chosen = Decimal(raw.replace(",", "").replace("\u20a6", "").strip())
        except (InvalidOperation, ValueError):
            return fail("Enter the limit as a number, e.g. 50000")
        # Order matters. is_finite() first, because NaN compares False against
        # everything and Infinity would sail past a naive upper bound. The ceiling
        # next, because it is what bounds the magnitude \u2014 quantize() on something
        # like 1e9999 RAISES rather than returning a value, so it can only run
        # once the number is known to be sane. A test pins each of the three.
        if not chosen.is_finite():
            return fail("Enter the limit as a number, e.g. 50000")
        if chosen < 0:
            return fail("A limit cannot be negative")
        if chosen > ceiling:
            return fail(
                f"Your Tier {user.tier} limit is \u20a6{ceiling:,.0f} per transaction. "
                "Verify more of your identity to raise it.",
                code="above_tier_ceiling")
        if chosen != chosen.quantize(Decimal("0.01")):
            return fail("Enter the limit to at most two decimal places")

    user.self_txn_limit = chosen
    user.save(update_fields=["self_txn_limit"])
    return ok(success=True,
              message=("Limit removed \u2014 your tier limit applies" if chosen is None
                       else "Transaction limit updated"),
              **_limit_state(user))


def _limit_state(user) -> dict:
    """What the limits screen renders. `self_txn_limit` is null when unset, which
    the app shows as "tier default" rather than as zero — a customer who has
    frozen their own spending at 0 is a different state from one who never set a
    limit, and the two must not read the same."""
    return {
        "tier": user.tier,
        "tier_transaction_limit": str(user.tier_transaction_limit),
        "self_txn_limit": None if user.self_txn_limit is None else str(user.self_txn_limit),
        "transaction_limit": str(user.transaction_limit),
        "daily_transfer_limit": str(user.daily_transfer_limit),
        "daily_bill_limit": str(user.daily_bill_limit),
    }


@api
@require_user
def transaction_limits(request):
    """POST /api/limits/ {access_token} -> the customer's limits and their ceiling."""
    return ok(success=True, **_limit_state(request.user_obj))


@api
@ratelimit("kyc_status", limit=120, window=600)
@require_user
def kyc_status(request):
    """POST /api/kyc/status/ {access_token} -> tier + verification flags"""
    from utility.liveness import refresh_tier2_liveness

    refresh_tier2_liveness(request.user_obj)
    # Reload retained address state without treating bank account status as
    # proof for a submitted address. Keep _kyc_state free of provider I/O;
    # callers also use it while holding user/action locks.
    if request.user_obj.address_verification_pending:
        from wallet.address_verification import refresh_address_verification
        refresh_address_verification(request.user_obj)
    return ok(success=True, **_kyc_state(request.user_obj))




@api
@ratelimit("kyc_liveness_start", limit=5, window=600)
@require_user
def kyc_liveness_start(request):
    from utility.liveness import start_tier2_liveness

    gate = _email_gate(request.user_obj)
    if gate:
        return gate
    result = start_tier2_liveness(request.user_obj)
    return JsonResponse(result, status=503 if result.get("unavailable") else
                        202 if result.get("pending") else 200 if result.get("success") else 409)


@api
@ratelimit("kyc_liveness_status", limit=60, window=600)
@require_user
def kyc_liveness_status(request):
    user = request.user_obj
    result = verify_tier2_liveness(user, request.data)
    user.refresh_from_db()
    return JsonResponse({**_kyc_state(user), **result}, status=503 if result.get("unavailable") else
                        202 if result.get("pending") else 200 if result.get("success") else 400)


@api
@ratelimit("kyc_address", limit=10, window=600)
@require_user
def kyc_address(request):
    """Expose retained address status; new Tier 3 verification is unavailable
    until the bank supplies authenticated evidence bound to the submitted address."""
    return verify_kyc_address(request.user_obj, request.data)


def verify_kyc_address(user, data):
    """Preserve address evidence until correlated bank verification is supported.

    An account-level Tier 3/Completed result cannot certify the address entered
    here. This gate also applies to held Partnership customers and alternative
    KYC settings: neither an OCR result nor a provider-mode change can bypass it.
    """
    gate = _email_gate(user)
    if gate:
        return gate
    from wallet.address_verification import (
        ADDRESS_VERIFICATION_UNAVAILABLE_MESSAGE, refresh_address_verification,
        tier3_address_capability,
    )

    refresh_address_verification(user)
    if user.address_verified:
        return ok(success=True, message="Address verified", **_kyc_state(user))
    if user.address_verification_pending:
        return JsonResponse({
            **_kyc_state(user), **tier3_address_capability(),
            "success": False, "pending": True, "review_required": True,
            "code": "address_verification_review_required",
            "message": "Your submitted address verification needs review. Please contact Zitch Support; do not submit it again.",
        }, status=202)
    if not (user.tier1_identity_verified and user.face_verified
            and user.phone_verified and user.email_verified):
        return fail("Complete Tier 2 identity and liveness verification before verifying your address.",
                    status=409, upgrade_required=True, required_tier=2)
    return fail(ADDRESS_VERIFICATION_UNAVAILABLE_MESSAGE, status=503,
                success=False, code="address_verification_unavailable",
                **tier3_address_capability())

@api
@ratelimit("kyc_id_document", limit=10, window=600)
@require_user
def kyc_id_document(request):
    """POST /api/kyc/id/ {access_token, image, doc_type?}

    Verifies a government-issued ID document (Tier 3): passport / driver's
    licence / voter's card / NIN slip. Only the verified flag and the document
    type are retained — never the raw image.
    """
    user = request.user_obj
    gate = _email_gate(user)
    if gate:
        return gate
    image = request.data.get("image") or request.data.get("document") or ""
    doc_type = (request.data.get("doc_type") or "").strip()[:32]
    image_error = _kyc_image_error(image)
    if image_error:
        return fail(image_error)
    result = kyc_verify_id_document(image, doc_type=doc_type)
    if not result.get("success"):
        return fail(result.get("message", "Couldn't verify your ID document"), status=400)
    user.id_document_type = doc_type or "generic"
    user.id_document_verified = True
    user.recompute_tier()
    user.save(update_fields=["id_document_type", "id_document_verified", "tier"])
    return ok(success=True, message="ID document verified", **_kyc_state(user))


def prembly_face_complete(request):
    """Provider redirect is informational; only authenticated readback grants KYC."""
    response = HttpResponse(
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Return to Zitch</title><main><h1>Return to Zitch</h1>'
        '<p>Your camera session has ended. Return to the Zitch app or your secure '
        'WhatsApp verification page and choose Check verification to confirm your result.</p>'
        '<p>This page does not confirm a verification result.</p></main></html>')
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    response["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
    return response
