import ipaddress
import json
import secrets
from functools import wraps

from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from wallet.models import Wallet

from .config import config, validate_configuration
from .contracts import InvalidPayload, account_number, string
from .models import VirtualAccount
from .services import ReplayConflict, account_balance, account_details, account_identity, mini_statement, process_notification

INVALID = {"status": "07", "status_desc": "Invalid Account"}


def response(body, status=200):
    result = JsonResponse(body, status=status)
    result["Cache-Control"] = "no-store"
    result["Pragma"] = "no-cache"
    result["X-Content-Type-Options"] = "nosniff"
    return result


def _unique_keys(pairs):
    body = {}
    for key, value in pairs:
        if key in body:
            raise InvalidPayload("Duplicate JSON key")
        body[key] = value
    return body


def _secure(request, values):
    if request.META.get("wsgi.url_scheme") == "https" or getattr(request, "scope", {}).get("scheme") == "https":
        return True
    return values.get("TRUST_TLS_PROXY", False) and request.META.get("HTTP_X_FORWARDED_PROTO") == "https"


def endpoint(view):
    @csrf_exempt
    @wraps(view)
    def wrapped(request):
        if not config().get("ENABLED", False):
            return response({"error": "Not found"}, 404)
        try:
            values = validate_configuration()
            if values.get("REQUIRE_HTTPS", True) and not _secure(request, values):
                return response({"error": "HTTPS required"}, 403)
            allowed = values.get("ALLOWED_SOURCE_CIDRS", [])
            if allowed:
                try:
                    address = ipaddress.ip_address(request.META.get("REMOTE_ADDR", ""))
                    valid = any(address in ipaddress.ip_network(cidr) for cidr in allowed)
                except (ValueError, TypeError):
                    valid = False
                if not valid:
                    return response({"error": "Forbidden"}, 403)
            parts = request.headers.get("Authorization", "").split()
            if len(parts) != 2 or parts[0].lower() != "bearer" or not secrets.compare_digest(parts[1].encode(), values["TOKEN"].encode()):
                return response({"error": "Unauthorized"}, 401)
            if request.method != "POST":
                result = response({"error": "POST required"}, 405)
                result["Allow"] = "POST"
                return result
            if request.content_type != "application/json":
                return response({"error": "application/json required"}, 415)
            try:
                size = int(request.META.get("CONTENT_LENGTH") or 0)
            except ValueError:
                raise InvalidPayload("Invalid content length") from None
            if size > 16384 or len(request.body) > 16384:
                return response({"error": "Payload too large"}, 413)
            body = json.loads(request.body, object_pairs_hook=_unique_keys)
            if not isinstance(body, dict):
                raise InvalidPayload("Expected JSON object")
            value, status = view(body)
            return response(value, status)
        except (InvalidPayload, UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            return response({"error": "Invalid request payload"}, 400)
        except ReplayConflict:
            return response({"error": "Conflicting transaction reference"}, 409)
        except (DatabaseError, ImproperlyConfigured, Wallet.DoesNotExist):
            return response({"error": "Temporarily unavailable; retry required"}, 503)
    return wrapped


def _account(body):
    try:
        return account_details(account_number(body))
    except InvalidPayload:
        return None


@endpoint
def lookup(body):
    account = _account(body)
    if account is None:
        return INVALID, 200
    if not account.active or not account.user.is_active:
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
    balance = account_balance(account)
    return {"accountname": identity["name"], "bvn": identity["bvn"], "nin": identity["nin"],
            "mobilenumber": identity["phone"], "walletbalance": format(balance, ".2f"),
            "status_desc": "Active" if account.active and account.user.is_active else "Inactive"}, 200


@endpoint
def block(body):
    reason = string(body, "blockreason", 200)
    with transaction.atomic():
        account = _account(body)
        if account is None:
            return INVALID, 200
        Wallet.objects.select_for_update().get(user_id=account.user_id)
        account = VirtualAccount.objects.select_for_update().get(pk=account.pk)
        VirtualAccount.objects.filter(pk=account.pk, active=True).update(
            active=False, block_reason=reason, blocked_at=timezone.now(),
        )
    return {"message": "Account Restricted Successfully"}, 200
