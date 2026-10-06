"""Strict bank-wire parsing; no HTTP clients and no live credentials."""
import hashlib
import json
import re
from datetime import timedelta, timezone as dt_timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.utils import timezone
from django.utils.dateparse import parse_datetime


class InvalidPayload(ValueError):
    pass


def string(body, key, max_length, *, optional=False):
    value = body.get(key, "" if optional else None)
    if (not isinstance(value, str) or len(value) > max_length
            or (not optional and not value.strip())
            or any(ord(char) < 32 for char in value)):
        raise InvalidPayload("Invalid field: " + key)
    return value


def account_number(body, key="accountnumber"):
    value = string(body, key, 10)
    if not re.fullmatch(r"[0-9]{10}", value):
        raise InvalidPayload("Invalid account number")
    return value


def money(body):
    raw_amount = string(body, "amount", 16)
    if not re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,2})?", raw_amount):
        raise InvalidPayload("Amount must be a positive decimal string with at most two decimals")
    amount = Decimal(raw_amount)
    if amount <= 0:
        raise InvalidPayload("Amount must be positive")
    return amount


def notification(body):
    result = {
        "craccount": account_number(body, "craccount"),
        "originatoraccountnumber": account_number(body, "originatoraccountnumber"),
    }
    for key, size in {
        "originatorname": 160, "bankcode": 20, "bankname": 120,
        "paymentreference": 128, "sessionid": 128, "craccountname": 160,
    }.items():
        result[key] = string(body, key, size)
    string(body, "narration", 500, optional=True)
    amount = money(body)
    # Normalize amount so 10, 10.0 and 10.00 do not create false replay conflicts.
    result["amount"] = format(amount, ".2f")
    raw_time = string(body, "created_at", 40)
    try:
        occurred_at = parse_datetime(raw_time)
    except ValueError as exc:
        raise InvalidPayload("Invalid created_at") from exc
    if occurred_at is None:
        raise InvalidPayload("Invalid created_at")
    try:
        if timezone.is_naive(occurred_at):
            occurred_at = timezone.make_aware(occurred_at, ZoneInfo("Africa/Lagos"))
        if occurred_at > timezone.now() + timedelta(minutes=5):
            raise InvalidPayload("Future created_at")
        result["created_at"] = occurred_at.astimezone(dt_timezone.utc).isoformat()
    except (OverflowError, ValueError) as exc:
        raise InvalidPayload("Invalid created_at") from exc
    fingerprint = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result, amount, occurred_at, fingerprint


def search_request(*, session_id="", account=""):
    """Build documented INBOUND search payload only; never make a bank request."""
    if bool(session_id) == bool(account):
        raise InvalidPayload("Supply exactly one of session_id or account")
    if session_id:
        return {"sessionid": string({"sessionid": session_id}, "sessionid", 128)}
    return {"craccount": account_number({"craccount": account}, "craccount")}


def search_findings(body):
    """Read-only classification. Never book money/refund from a search result."""
    if not isinstance(body, dict) or body.get("status") != "00":
        raise InvalidPayload("Unsuccessful or invalid transaction-search envelope")
    rows = body.get("transactions")
    if not isinstance(rows, list):
        raise InvalidPayload("Expected transactions array")
    if len(rows) > 5000:
        raise InvalidPayload("Transaction-search snapshot exceeds the inspection limit")
    findings = []
    sessions, references = set(), set()
    for row in rows:
        if not isinstance(row, dict):
            raise InvalidPayload("Invalid transaction-search row")
        session_id = string(row, "sessionid", 128)
        number = account_number(row, "craccount")
        reference = string(row, "paymentreference", 128)
        if session_id in sessions or reference in references:
            raise InvalidPayload("Duplicate transaction identity in search response")
        sessions.add(session_id)
        references.add(reference)
        amount = money(row)
        source_account = account_number(row, "originatoraccountnumber")
        source_bank = string(row, "bankname", 120)
        account_name = string(row, "craccountname", 160)
        originator_name = string(row, "originatorname", 160)
        bank_code = string(row, "bankcode", 20)
        string(row, "narration", 500, optional=True)
        # Wema documents a date but not its exact encoding/timezone. Retain no
        # date in the report and do not equate requestdate with created_at.
        string(row, "requestdate", 64)
        nibss = string(row, "nibssresponse", 30)
        send = string(row, "sendresponse", 30, optional=True)
        outcome = ("uncertain_contact_bank" if nibss != "00" else
                   "notification_repush_required" if send != "00" else "acknowledged")
        findings.append({"sessionid": session_id, "craccount": number, "outcome": outcome,
                         "paymentreference": reference, "amount": format(amount, ".2f"),
                         "originatoraccountnumber": source_account, "bankname": source_bank,
                         "craccountname": account_name, "originatorname": originator_name,
                         "bankcode": bank_code})
    return findings
