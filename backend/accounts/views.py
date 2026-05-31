import random

from django.contrib.auth import authenticate
from django.db.models import Q

from common.http import api, fail, ok, require_user
from utility.providers import send_sms
from wallet.services import get_or_create_wallet

from .models import OTP, AccessToken, User


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


@api
def phone_verification(request):
    """POST /api/phone_verification/ {email, phone} -> sends OTP"""
    phone = (request.data.get("phone") or "").strip()
    email = (request.data.get("email") or "").strip()
    if not phone:
        return fail("Phone is required")
    if User.objects.filter(phone=phone).exists():
        return fail("An account with this phone already exists")

    code = f"{random.randint(0, 99999):05d}"
    OTP.objects.create(phone=phone, email=email, code=code)
    send_sms(phone, f"Your Zitch verification code is {code}")
    return ok(message="OTP sent")


@api
def verify_otp(request):
    """POST /api/verify_otp/ {otp, phone} -> creates user + {access_token}"""
    phone = (request.data.get("phone") or "").strip()
    code = (request.data.get("otp") or "").strip()
    if not phone or not code:
        return fail("Phone and OTP are required")

    otp = OTP.objects.filter(phone=phone, used=False).order_by("-created").first()
    if otp is None or otp.code != code:
        return fail("Invalid OTP", status=400)
    if otp.is_expired:
        return fail("OTP has expired", status=400)

    otp.used = True
    otp.save(update_fields=["used"])

    user, _ = User.objects.get_or_create(
        phone=phone,
        defaults={"username": phone, "email": otp.email or ""},
    )
    get_or_create_wallet(user)
    token = AccessToken.issue(user)
    return ok(access_token=token.key, message="Verified")


@api
def resend_verify_otp(request):
    """POST /api/resend_verify_otp/ {phone}"""
    phone = (request.data.get("phone") or "").strip()
    if not phone:
        return fail("Phone is required")
    code = f"{random.randint(0, 99999):05d}"
    OTP.objects.create(phone=phone, code=code)
    send_sms(phone, f"Your Zitch verification code is {code}")
    return ok(message="OTP resent")


@api
def set_password(request):
    """POST /api/set-password/ {email, password}"""
    email = (request.data.get("email") or "").strip()
    password = request.data.get("password") or ""
    if len(password) < 8:
        return fail("Password must be at least 8 characters")
    user = User.objects.filter(Q(email__iexact=email) | Q(phone=email)).first()
    if user is None:
        return fail("Account not found", status=404)
    user.set_password(password)
    user.save(update_fields=["password"])
    return ok(message="Password set")


@api
def set_transaction_pin(request):
    """POST /api/set-transaction-pin/ {email, pin}"""
    email = (request.data.get("email") or "").strip()
    pin = (request.data.get("pin") or "").strip()
    if len(pin) < 4:
        return fail("PIN must be at least 4 digits")
    user = User.objects.filter(Q(email__iexact=email) | Q(phone=email)).first()
    if user is None:
        return fail("Account not found", status=404)
    user.set_transaction_pin(pin)
    user.save(update_fields=["transaction_pin"])
    return ok(message="Transaction PIN set")


@api
@require_user
def update_info(request):
    """POST /api/update_info/ {first_name, last_name, email, phone, access_token}"""
    user = request.user_obj
    data = request.data
    if data.get("first_name"):
        user.first_name = data["first_name"]
    if data.get("last_name"):
        user.last_name = data["last_name"]
    if data.get("email"):
        user.email = data["email"]
    if data.get("phone"):
        user.phone = data["phone"]
    user.save()
    return ok(message="Account updated")
