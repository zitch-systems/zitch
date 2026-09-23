import ipaddress
import json
import secrets
from functools import wraps

from django.conf import settings
from django.db import OperationalError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .contracts import InvalidPayload, account_number, string
from .fixtures import CUSTOMERS
from .models import VirtualAccount
from .services import ReplayConflict, account_details, mini_statement, process_notification


def response(body, status=200):
    result = JsonResponse(body, status=status)
    result["Cache-Control"] = "no-store"
    result["X-Zitch-VAS-Mode"] = "synthetic-only"
    return result


def _unique_keys(pairs):
    body = {}
    for key, value in pairs:
        if key in body:
            raise InvalidPayload("Duplicate JSON key")
        body[key] = value
    return body


def endpoint(view):
    @csrf_exempt
    @wraps(view)
    def wrapped(request):
        if not settings.VAS_ENABLED:
            return response({"error": "Not found"}, 404)
        if (settings.VAS_MODE != "synthetic" or settings.INSTALLED_APPS != ["vas_harness"]
                or settings.DATABASES["default"]["ENGINE"] != "django.db.backends.sqlite3"):
            return response({"error": "Isolation guard failed"}, 503)
        # Never trust forwarded-for headers; this service must not be exposed.
        try:
            local = ipaddress.ip_address(request.META.get("REMOTE_ADDR", "")).is_loopback
        except ValueError:
            local = False
        if not local:
            return response({"error": "Local development only"}, 403)
        if len(settings.VAS_TOKEN) < 32:
            return response({"error": "Development authentication is not configured"}, 503)
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
    fixture = CUSTOMERS[account.number]
    return {"accountname": fixture["name"], "status": "00", "status_desc": "Okay",
            "bvn": fixture["bvn"], "nin": fixture["nin"]}, 200


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
    fixture = CUSTOMERS[account.number]
    return {"accountname": fixture["name"], "bvn": fixture["bvn"], "nin": fixture["nin"],
            "mobilenumber": "00000000000", "walletbalance": format(account.simulated_balance, ".2f"),
            "status_desc": "Active" if account.active else "Inactive"}, 200


@endpoint
def block(body):
    reason = string(body, "blockreason", 200)
    account = _account(body)
    if account is None:
        return INVALID, 200
    with transaction.atomic():
        # Idempotent replays preserve the first reason/time.
        VirtualAccount.objects.filter(pk=account.pk, active=True).update(
            active=False, block_reason=reason, blocked_at=timezone.now(),
        )
    return {"message": "Account Restricted Successfully"}, 200
