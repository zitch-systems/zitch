"""Existing-account sign-in using the registered phone, email OTP and private PIN.

Uses already-published CODE_SCREEN -> PIN_CHAIN routes. No account token, PIN or
OTP is placed in the chat, and an OTP alone never connects a banking channel.
"""
import hmac
import json
import logging
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.views.decorators.debug import sensitive_variables

from accounts.models import User
from common.http import evaluate_transaction_pin
from common.ratelimit import opaque_cache_identifier
from utility.providers import email_live, send_email
from .models import PendingAction, WaOnboarding, WhatsAppLink

PREFIX = "lg"
CODE_STATE = "flow_login_code"
PIN_STATE = "flow_login_pin"
STATES = (CODE_STATE, PIN_STATE)
TTL = timedelta(minutes=10)
log = logging.getLogger("zitch.security")


def _credentials(user):
    """Revoke an unfinished login after any credential/contact change.

    Only a keyed digest is retained, never password/PIN hashes or a plaintext
    email address in the challenge payload.
    """
    value = json.dumps([user.pk, user.password, user.transaction_pin, user.phone, user.email])
    return salted_hmac("whatsapp.login.credentials.v1", value, algorithm="sha256").hexdigest()


def _canonical_phone(value):
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 11 and digits.startswith("0"):
        return "234" + digits[1:]
    return digits


def _token(ob):
    from .flows import _sig
    return f"{PREFIX}{ob.id}." + _sig(
        f"login:{ob.id}:{ob.msisdn}:{ob.payload.get('user_id')}:"
        f"{ob.payload.get('credentials')}:{ob.payload['nonce']}"
    )


def _resolve(token, *, lock=False):
    # Bound before int()/ORM access; isdigit() also admits non-ASCII characters
    # int() cannot parse. The HMAC alphabet is ASCII, so compare_digest is safe.
    if not isinstance(token, str) or len(token) > 44:
        return None
    match = re.fullmatch(r"lg([1-9][0-9]{0,18})\.([A-Za-z0-9_-]{22})", token)
    if match is None:
        return None
    action_id, signature = int(match[1]), match[2]
    if action_id >= 2**63:
        return None
    rows = WaOnboarding.objects.select_for_update() if lock else WaOnboarding.objects
    ob = rows.filter(pk=action_id, step__in=STATES).first()
    if (not ob or ob.expired or not isinstance(ob.payload, dict)
            or not isinstance(ob.payload.get("credentials"), str)
            or len(ob.payload["credentials"]) != 64 or not ob.payload.get("nonce")):
        return None
    expected = _token(ob).partition(".")[2]
    return ob if hmac.compare_digest(signature, expected) else None


def _screen(ob, error=""):
    from .flows import CODE_SCREEN, PIN_CHAIN, _identity_screen, _pin_screen
    screen = ob.payload.get("screen") or CODE_SCREEN
    if ob.step == PIN_STATE:
        return _pin_screen("Confirm your identity to connect WhatsApp. No payment will be made.",
                           error=error, screen=screen or PIN_CHAIN)
    return _identity_screen("email", label="Email code", error=error, screen=screen,
                            summary="Enter the code sent to your verified Zitch email.")


@sensitive_variables()
def start_login(msisdn):
    from .providers import flows_live, send_flow
    from .router import _local_phone, reply
    from .flows import CODE_SCREEN
    if not flows_live():
        return reply(msisdn, "Secure sign-in is temporarily unavailable. Please try again shortly.")
    key = "wa-login-send:" + opaque_cache_identifier("wa-login-send", msisdn)
    if not cache.add(key, True, 60):
        return reply(msisdn, "Please wait a minute before requesting another sign-in code.")
    user = User.objects.filter(phone__in=[_local_phone(msisdn), msisdn, "+" + msisdn],
                               is_active=True, phone_verified=True).first()
    if not user or not user.email_verified or not user.email or not user.transaction_pin:
        return reply(msisdn, "We couldn't start secure sign-in for this number. Use the WhatsApp number "
                     "registered on your Zitch account, or contact support to recover access.")
    if user.pin_reset_required:
        return reply(msisdn, "Your transaction PIN needs a reset. Recover it securely before signing in.")
    if not email_live() and not (settings.DEBUG or getattr(settings, "TESTING", False)):
        return reply(msisdn, "We couldn't send your sign-in code. Please try again shortly.")
    code = f"{secrets.randbelow(10**6):06d}"
    sent = send_email(user.email, "Connect your Zitch WhatsApp",
                      f"Your Zitch sign-in code is {code}. It expires in 10 minutes. "
                      "Enter it only in the secure WhatsApp form. Never share it.")
    if not sent.get("success"):
        return reply(msisdn, "We couldn't send your sign-in code. Please try again shortly.")
    ob, _ = WaOnboarding.objects.update_or_create(msisdn=msisdn, defaults={
        "step": CODE_STATE, "expires_at": timezone.now() + TTL,
        "payload": {"user_id": user.pk, "credentials": _credentials(user), "nonce": secrets.token_urlsafe(24),
                    "code_hash": make_password(code), "attempts": 0, "screen": CODE_SCREEN},
    })
    response = _screen(ob)
    sent = send_flow(msisdn, _token(ob), header="Sign in to Zitch",
                     body="Enter your email code and existing PIN privately to connect this WhatsApp.",
                     screen=response["screen"], screen_data=response["data"],
                     cta="Sign in", on_open="data_exchange")
    if not sent.get("success"):
        ob.delete()
        return reply(msisdn, "The secure sign-in form could not open. Please try again shortly.")


def _send_login_menu(msisdn):
    """Delivery failure after commit must not turn successful authentication into
    a failed Flow or log provider exception text containing customer data."""
    from .router import send_menu

    try:
        send_menu(msisdn)
    except Exception:
        log.warning("wa_login_menu_delivery_failed")


@sensitive_variables()
def handle_login(token, action, data):
    from .flows import CODE_RETRY, PIN_CHAIN, PIN_RETRY, _result_screen
    from .router import EXECUTING_STATE, _mark_verified
    with transaction.atomic():
        ob = _resolve(token, lock=True)
        if ob is None:
            return _result_screen("This sign-in has ended. Reply 2 in the chat to start again.", "failed")
        # Payment dispatch locks action -> user. Use that same order when a
        # successful re-link will retire old chat challenges. Browser grants are
        # excluded: they lock user -> link -> action and their link binding already
        # revokes them when we replace the old link below.
        from .verification_web import ACTION_TYPE as WEB_ACTION_TYPE
        old_actions = []
        if ob.step == PIN_STATE and action == "data_exchange":
            old_actions = list(PendingAction.objects.select_for_update().filter(
                user_id=ob.payload.get("user_id"),
            ).exclude(action_type=WEB_ACTION_TYPE).exclude(state=EXECUTING_STATE))
        user = User.objects.select_for_update().filter(pk=ob.payload.get("user_id"), is_active=True).first()
        registered = _canonical_phone(user.phone) if user else ""
        if (not user or not registered or registered != ob.msisdn
                or not user.phone_verified or not user.email_verified
                or not hmac.compare_digest(ob.payload["credentials"], _credentials(user))):
            ob.delete()
            return _result_screen("Account details changed. Start sign-in again in the chat.", "failed")
        if user.pin_reset_required:
            ob.delete()
            return _result_screen("Reset your transaction PIN securely before signing in again.", "failed")
        if action != "data_exchange":
            return _screen(ob)
        if ob.step == CODE_STATE:
            code = str(data.get("number", "")).strip()
            if not re.fullmatch(r"[0-9]{6}", code) or not check_password(code, ob.payload.get("code_hash", "")):
                ob.payload["attempts"] = int(ob.payload.get("attempts", 0)) + 1
                if ob.payload["attempts"] >= 2:
                    ob.delete()
                    return _result_screen("Incorrect code. Reply 2 to request a fresh sign-in.", "failed")
                ob.payload["screen"] = CODE_RETRY
                ob.save(update_fields=["payload"])
                return _screen(ob, "Incorrect code. Please try again.")
            ob.step = PIN_STATE
            ob.payload.pop("code_hash", None)
            ob.payload.update(screen=PIN_CHAIN, attempts=0)
            ob.save(update_fields=["step", "payload"])
            return _screen(ob)
        raw_pin = str(data.get("pin", "")).strip()
        pin = raw_pin if re.fullmatch(r"[0-9]{6}", raw_pin) else ""
        valid, reason, message = evaluate_transaction_pin(user, pin)
        if not valid:
            ob.payload["attempts"] = int(ob.payload.get("attempts", 0)) + 1
            if reason in ("pin_locked", "no_pin") or ob.payload["attempts"] >= 2:
                ob.delete()
                return _result_screen(message + " Start sign-in again in the chat.", "failed")
            ob.payload["screen"] = PIN_RETRY
            ob.save(update_fields=["payload"])
            return _screen(ob, message)
        # A forwarded form can authenticate only the account registered to the
        # original WhatsApp number. It cannot choose a different destination.
        if WhatsAppLink.objects.filter(wa_msisdn=ob.msisdn, status=WhatsAppLink.ACTIVE).exclude(user=user).exists():
            ob.delete()
            return _result_screen("This number is already connected. Contact support for recovery.", "failed")
        prior = list(WhatsAppLink.objects.select_for_update().filter(user=user).order_by("-created"))
        preferences = [link for link in prior if link.status == WhatsAppLink.ACTIVE] or prior[:1]
        preserved = ({
            "ai_enabled": all(link.ai_enabled for link in preferences),
            "marketing_opt_in": all(link.marketing_opt_in for link in preferences),
        } if preferences else {})
        # Retire unsubmitted capabilities at previous linked numbers, including
        # this number. Already-authorized payments/provider work must survive.
        # Portal grants additionally become invalid when their link row disappears.
        old_numbers = {link.wa_msisdn for link in prior if link.wa_msisdn} | {ob.msisdn}
        PendingAction.objects.filter(pk__in=[
            action.pk for action in old_actions if action.msisdn in old_numbers
        ]).update(expires_at=timezone.now())
        WhatsAppLink.objects.filter(user=user).delete()
        WhatsAppLink.objects.create(user=user, wa_msisdn=ob.msisdn,
                                    status=WhatsAppLink.ACTIVE, linked_at=timezone.now(), **preserved)
        msisdn = ob.msisdn
        ob.delete()
        _mark_verified(msisdn)
        transaction.on_commit(lambda: _send_login_menu(msisdn))
    return _result_screen("WhatsApp is connected. Return to the chat to use your account.", "done")
