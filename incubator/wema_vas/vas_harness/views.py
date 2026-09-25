import ipaddress
import json
import secrets
from functools import wraps

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import OperationalError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .contracts import InvalidPayload, account_number, string
from .models import VirtualAccount
from .services import ReplayConflict, account_details, account_identity, mini_statement, process_notification


def response(body, status=200):
    result = JsonResponse(body, status=status)
    result["Cache-Control"] = "no-store"
    if settings.VAS_MODE == "synthetic":
        result["X-Zitch-VAS-Mode"] = "synthetic-only"
    return result


def _unique_keys(pairs):
    body = {}
    for key, value in pairs:
        if key in body:
            raise InvalidPayload("Duplicate JSON key")
        body[key] = value
    return body


def health(request):
    if request.method != "GET":
        return response({"error": "GET required"}, 405)
    # Liveness alone does not mean the bank integration has been enabled.
    return response({"alive": True, "bank_enabled": settings.VAS_ENABLED})


def endpoint(view):
    @csrf_exempt
    @wraps(view)
    def wrapped(request):
        if not settings.VAS_ENABLED:
            return response({"error": "Not found"}, 404)
        engine = settings.DATABASES["default"]["ENGINE"]
        engine_allowed = ((settings.VAS_MODE == "synthetic" and engine == "django.db.backends.sqlite3") or
                          (settings.VAS_MODE == "validation" and engine == "django.db.backends.postgresql") or
                          (settings.TESTING and settings.VAS_MODE == "validation" and engine == "django.db.backends.sqlite3"))
        if settings.INSTALLED_APPS != ["vas_harness"] or not engine_allowed:
            return response({"error": "Isolation guard failed"}, 503)
        if settings.VAS_MODE == "synthetic":
            # Never trust forwarded-for headers; synthetic mode is local only.
            try:
                local = ipaddress.ip_address(request.META.get("REMOTE_ADDR", "")).is_loopback
            except ValueError:
                local = False
            if not local:
                return response({"error": "Local development only"}, 403)
        elif settings.VAS_REQUIRE_HTTPS and not request.is_secure():
            return response({"error": "HTTPS required"}, 403)
        if len(settings.VAS_TOKEN) < (48 if settings.VAS_MODE == "validation" else 32):
            return response({"error": "Authentication is not configured"}, 503)
        parts = request.headers.get("Authorization", "").split()
        if len(parts) != 2 or parts[0].lower() != "bearer" or not secrets.compare_digest(parts[1].encode(), settings.VAS_TOKEN.encode()):
            return response({"error": "Unauthorized"}, 401)
        if request.method != "POST":
            result = response({"error": "POST required"}, 405)
            result["Allow"] = "POST"
            return result
        if request.content_type != "application/json":
            return response({"error": "application/json required"}, 415)
        try:
            length = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            return response({"error": "Invalid content length"}, 400)
        if length > 16384:
            return response({"error": "Payload too large"}, 413)
        try:
            if len(request.body) > 16384:
                return response({"error": "Payload too large"}, 413)
            body = json.loads(request.body, object_pairs_hook=_unique_keys)
            if not isinstance(body, dict):
                raise InvalidPayload("Expected JSON object")
            result, status = view(body)
            return response(result, status)
        except (InvalidPayload, UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            return response({"error": "Invalid request payload"}, 400)
        except ReplayConflict:
            # Never ACK a conflicting session/payment reference or credit twice.
            return response({"error": "Conflicting transaction reference"}, 409)
        except OperationalError:
            # SQLite writer contention: ask for a retry, never return status 00.
            return response({"error": "Temporarily unavailable; retry required"}, 503)
        except ImproperlyConfigured:
            # Never disclose a decryption/key problem or return partial KYC.
            return response({"error": "Temporarily unavailable; retry required"}, 503)
    return wrapped


def _account(body):
    try:
        number = account_number(body)
    except InvalidPayload:
        return None
    return account_details(number)


INVALID = {"status": "07", "status_desc": "Invalid Account"}


@endpoint
def lookup(body):
    account = _account(body)
    if account is None:
        return INVALID, 200
    if not account.active:
        return {"status": "07", "status_desc": "Inactive Account"}, 200
    identity = account_identity(account)
    return {"accountname": identity["name"], "status": "00", "status_desc": "Okay",
            "bvn": identity["bvn"], "nin": identity["nin"]}, 200


@endpoint
def notify(body):
    return process_notification(body)


@endpoint
def statement(body):
    account = _account(body)
    return (mini_statement(account), 200) if account else (INVALID, 200)


@endpoint
def kyc(body):
    account = _account(body)
    if account is None:
        return INVALID, 200
    identity = account_identity(account)
    return {"accountname": identity["name"], "bvn": identity["bvn"], "nin": identity["nin"],
            "mobilenumber": identity["phone"], "walletbalance": format(account.simulated_balance, ".2f"),
            "status_desc": "Active" if account.active else "Inactive"}, 200


@endpoint
def block(body):
    reason = string(body, "blockreason", 200)
    with transaction.atomic():
        account = _account(body)
        if account is None:
            return INVALID, 200
        account = VirtualAccount.objects.select_for_update().get(pk=account.pk)
        # Idempotent replays preserve the first reason/time.
        VirtualAccount.objects.filter(pk=account.pk, active=True).update(
            active=False, block_reason=reason, blocked_at=timezone.now(),
        )
    return {"message": "Account Restricted Successfully"}, 200
