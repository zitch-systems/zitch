"""Private one-entry app enrollment; no raw identity leaves the server response.

Only authenticated ciphertext is cached. A ten-minute challenge binds the input,
explicit consent and proof completion to one authenticated app session. All bank
account allocation still passes the normal enrollment and migration controls.
"""
import functools
import hmac
import json
import math
import re
import secrets
import time

from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import DatabaseError
from django.utils.crypto import salted_hmac
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from redis.exceptions import RedisError

from accounts.models import hash_identifier
from common.http import api, fail, ok, require_user, resolve_token
from common.ratelimit import opaque_cache_identifier, ratelimit
from wallet.services import customer_funding_account

from .config import config
from .enrollment import (consent_version, customer_enrollment_available,
                         enroll_customer, enrollment_eligibility, _verified_proofs)
from .identity import cipher
from .views import _secure

TTL = 600
RESEND_WAIT = 60
MAX_RESENDS = 3


class ChallengeUnavailable(Exception):
    pass


def _key(reference):
    if not isinstance(reference, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32}", reference):
        raise ChallengeUnavailable()
    return "app-vas-identity:" + opaque_cache_identifier("app-vas-identity", reference)


def _binding(request):
    return {"user": request.user_obj.pk,
            "session": salted_hmac("app.vas.identity.session.v1", resolve_token(request),
                                   algorithm="sha256").hexdigest(),
            "device": (request.headers.get("X-Zitch-Device") or "").strip()[:64]}


@sensitive_variables()
def _store(reference, value, *, new=False):
    try:
        ttl = min(TTL, int(value["expires"] - time.time()))
        if ttl < 1:
            raise ChallengeUnavailable()
        encrypted = cipher().encrypt(json.dumps(value, separators=(",", ":")).encode()).decode("ascii")
        key = _key(reference)
        if new:
            if not cache.add(key, encrypted, timeout=ttl):
                raise ChallengeUnavailable()
        else:
            cache.set(key, encrypted, timeout=ttl)
        if cache.get(key) != encrypted:
            raise ChallengeUnavailable()
    except Exception:
        raise ChallengeUnavailable() from None


@sensitive_variables()
def _load(request, reference):
    try:
        encrypted = cache.get(_key(reference))
        if not isinstance(encrypted, str) or len(encrypted) > 16384:
            raise ChallengeUnavailable()
        value = json.loads(cipher().decrypt_at_time(encrypted.encode("ascii"), ttl=TTL,
                         current_time=int(time.time())))
        if (value["binding"] != _binding(request) or value["expires"] <= time.time()
                or value["mode"] != config().get("MODE", "validation")
                or value["consent_version"] != consent_version(value["mode"])
                or value["consent"] is not True or value["kind"] not in {"bvn", "nin"}
                or not re.fullmatch(r"[0-9]{11}", value["number"])
                or not hmac.compare_digest(hash_identifier(value["number"]), value["identity_hash"])):
            raise ChallengeUnavailable()
        return value
    except Exception:
        raise ChallengeUnavailable() from None


def _discard(reference):
    try:
        key = _key(reference)
        cache.delete_many([key, key + ":otp"])
    except Exception:
        pass  # Fixed expiry remains the deletion fallback during a cache outage.


def _endpoint(view):
    @functools.wraps(view)
    @sensitive_variables()
    def wrapped(request):
        try:
            if config().get("REQUIRE_HTTPS", True) and not _secure(request, config()):
                response = fail("HTTPS required", status=403)
            else:
                response = view(request)
        except ChallengeUnavailable:
            response = fail("This private setup session expired or could not be recovered. Start account setup again.",
                            status=409, code="vas_identity_challenge_expired", retry_available=False)
        except (DatabaseError, ImproperlyConfigured, ConnectionError, TimeoutError, RedisError):
            response = fail("Account setup is temporarily unavailable. Please try again.", status=503)
        response["Cache-Control"] = "no-store"
        return response
    return wrapped


def _consent_error(request):
    data = request.data
    mode = config().get("MODE", "validation")
    if (data.get("consent") is not True or data.get("enrollment_mode") != mode
            or data.get("consent_version") != consent_version(mode)):
        return fail("Review and accept the current account setup consent before continuing.",
                    status=409, code="vas_consent_refresh_required")
    return None


def _eligibility_error(user):
    eligibility = enrollment_eligibility(user)
    # Ownership proof is the one requirement this private journey may complete.
    if (not customer_enrollment_available(user)
            or any(item != "identity_verification" for item in eligibility["enrollment_blockers"])):
        return fail(eligibility["enrollment_message"], status=409,
                    code="vas_enrollment_pending", **eligibility)
    return None


@sensitive_variables()
def _finish(request, value, *, reference=""):
    try:
        enroll_customer(request.user_obj, **{value["kind"]: value["number"]}, consent=True,
                        expected_mode=value["mode"], expected_consent_version=value["consent_version"],
                        consent_reference=f"{value['consent_version']}:app:{request.user_obj.pk}:{value['consent_at']}")
    except ValidationError as exc:
        return fail(" ".join(exc.messages), status=409, success=False, identity_verified=True,
                    retry_available=bool(reference), challenge_id=reference, otp_required=False,
                    code="vas_enrollment_pending",
                    **customer_funding_account(request.user_obj))
    except (DatabaseError, ImproperlyConfigured):
        return fail("Your identity is verified. Account setup is temporarily unavailable; retry shortly.",
                    status=503, success=False, identity_verified=True, retry_available=bool(reference),
                    challenge_id=reference, otp_required=False, code="vas_enrollment_retry")
    if reference:
        _discard(reference)
    return ok(success=True, identity_verified=True, enrollment_completed=True, otp_required=False,
              **customer_funding_account(request.user_obj))


@sensitive_post_parameters("number", "bvn", "nin")
@api
@require_user
@ratelimit("vas_identity_start", limit=5, window=600)
@_endpoint
@sensitive_variables()
def start(request):
    from accounts import views as identity
    from utility.providers import _record_email

    error = _consent_error(request) or _eligibility_error(request.user_obj)
    if error is not None:
        return error
    kind, number = request.data.get("identity_type"), request.data.get("number")
    if (not isinstance(kind, str) or kind not in {"bvn", "nin"}
            or not isinstance(number, str) or not re.fullmatch(r"[0-9]{11}", number)):
        return fail("Enter your 11-digit BVN or NIN.")
    user = request.user_obj
    digest = hash_identifier(number)
    if (identity._identity_owned_by_another_user(user, kind, number)
            or (getattr(user, kind + "_verified") and not hmac.compare_digest(getattr(user, kind + "_hash"), digest))):
        return fail(identity._IDENTITY_CONFLICT_MESSAGE, status=409)
    now = time.time()
    value = {"binding": _binding(request), "expires": now + TTL, "consent_at": int(now),
             "consent": True, "mode": config().get("MODE", "validation"),
             "consent_version": consent_version(), "kind": kind, "number": number,
             "identity_hash": digest, "verified": False, "resends": 0, "last_sent": now}
    reference = secrets.token_urlsafe(24)
    _store(reference, value, new=True)  # Prove encrypted storage before paid lookup/SMS.
    try:
        _verified_proofs(user, **{"bvn": "", "nin": "", kind: number})
    except ValidationError:
        pass
    else:
        # Existing named ownership evidence follows the same allocation rules as
        # the original enroll endpoint; no repeated paid lookup or OTP is needed.
        value["verified"] = True
        _store(reference, value)
        return _finish(request, value, reference=reference)
    result = identity._lookup_identity(user, kind, number)
    if not result.get("success") or result.get("mock"):
        _discard(reference)
        return fail(result.get("message", "Identity verification is temporarily unavailable."), status=400)
    # Keep only the encrypted delivery/name fields needed for a bounded resend.
    value["record"] = {field: result.get(field, "") for field in ("phone", "first_name", "middle_name", "last_name")}
    value["record"].update(success=True, provider="prembly", email=_record_email(result))
    _store(reference, value)
    response = identity._start_identity_ownership_challenge(user, kind, number, value["record"],
                                                           challenge_key=_key(reference) + ":otp")
    if response.status_code != 200:
        _discard(reference)
        return response
    return ok(**json.loads(response.content), challenge_id=reference, expires_in=TTL,
              resend_after=RESEND_WAIT, identity_verified=False)


def _with_lock(view):
    @functools.wraps(view)
    @sensitive_variables()
    def wrapped(request):
        reference = request.data.get("challenge_id")
        value = _load(request, reference)
        key = _key(reference) + ":lock"
        if not cache.add(key, True, timeout=120):
            return fail("Account setup is already being processed. Please wait a moment.", status=409,
                        code="vas_identity_busy")
        try:
            value = _load(request, reference)
            return view(request, reference, value)
        finally:
            try:
                cache.delete(key)
            except Exception:
                pass  # The short lock expiry is the outage fallback.
    return wrapped


@sensitive_post_parameters("otp", "challenge_id")
@api
@require_user
@ratelimit("vas_identity_confirm", limit=20, window=300)
@_endpoint
@_with_lock
@sensitive_variables()
def confirm(request, reference, value):
    from accounts import views as identity

    if value["verified"] is not True:
        otp = request.data.get("otp", "")
        if not isinstance(otp, str) or not re.fullmatch(r"[0-9]{6}", otp):
            return fail("Enter the six-digit verification code.")
        number, error = identity._confirm_identity_ownership_challenge(request.user_obj, value["kind"], otp,
                    challenge_key=_key(reference) + ":otp", consume=False)
        if error is not None:
            return error
        if not hmac.compare_digest(number, value["number"]):
            _discard(reference)
            raise ChallengeUnavailable()
        if not identity._save_verified_identity(request.user_obj, value["kind"], number,
                verified_name=getattr(request.user_obj, "_provider_verified_name", "")):
            _discard(reference)
            return fail(identity._IDENTITY_CONFLICT_MESSAGE, status=409)
        value["verified"] = True
        _store(reference, value)
        # Consume only after durable proof and retry recovery are saved. A DB
        # outage can therefore retry the same valid code without another lookup.
        cache.delete(_key(reference) + ":otp")
    return _finish(request, value, reference=reference)


@sensitive_post_parameters("challenge_id")
@api
@require_user
@ratelimit("vas_identity_resend", limit=5, window=600)
@_endpoint
@_with_lock
@sensitive_variables()
def resend(request, reference, value):
    from accounts import views as identity

    if value["verified"] is True:
        return fail("Your identity is already verified. Continue account setup.", status=409,
                    identity_verified=True, retry_available=True)
    error = _eligibility_error(request.user_obj)
    if error is not None:
        return error
    wait = max(0, math.ceil(RESEND_WAIT - (time.time() - value["last_sent"])))
    if wait or value["resends"] >= MAX_RESENDS:
        return fail("Please wait before requesting another code." if wait else "Start account setup again to request another code.",
                    status=429, resend_after=wait, code="vas_identity_resend_limited")
    value["resends"] += 1
    value["last_sent"] = time.time()
    _store(reference, value)
    response = identity._start_identity_ownership_challenge(request.user_obj, value["kind"], value["number"], value["record"],
                                                           challenge_key=_key(reference) + ":otp")
    if response.status_code != 200:
        return response
    return ok(**json.loads(response.content), challenge_id=reference,
              expires_in=max(0, int(value["expires"] - time.time())), resend_after=RESEND_WAIT)
