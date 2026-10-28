"""Prembly is used exclusively for physical Tier 3 address verification.

Official request/status contracts: docs.prembly.com/reference/address-verification-1
and docs.prembly.com/reference/address-verification-status (reviewed 2026-10-28).
Their sample returns verification.status=VERIFIED even while addressStatus=pending.
That envelope must never approve an address or unlock Tier 3.
"""
import secrets

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from accounts.models import User

from .accounts import account_for, require_enabled
from .client import ProviderError
from .models import AddressVerification
from .payments import digest
from .secrets import seal


def _request(path, body):
    key = getattr(settings, "PREMBLY", {}).get("API_KEY", "")
    if not key:
        raise ProviderError("address_credentials_missing")
    try:
        response = requests.post("https://api.prembly.com/verification/address" + path, json=body,
            headers={"x-api-key": key, "Content-Type": "application/json", "Accept": "application/json"},
            timeout=(5, 20), allow_redirects=False)
        data = response.json()
    except (requests.RequestException, ValueError):
        raise ProviderError("address_service_unavailable", uncertain=not path) from None
    if not response.ok or not isinstance(data, dict) or data.get("status") is not True or data.get("response_code") != "00":
        raise ProviderError("address_request_not_confirmed", uncertain=not path)
    return data


def start(user, address):
    require_enabled(user)
    # Starting a chargeable physical visit is inappropriate while the provider's
    # terminal decision/report contract is unavailable. A config toggle cannot
    # bypass this integration requirement.
    raise ValueError("Address verification is awaiting provider setup. Please contact support")


def _submit_request(user, address):
    """Prepared request contract; not reachable until report handling is tested."""
    bank = account_for(user)
    if not bank or bank.tier < 2:
        raise ValueError("Complete Tier 2 before requesting address verification")
    required = ("house_number", "street", "address", "city", "state", "lga", "landmark")
    if any(not isinstance(address.get(k), str) or not address[k].strip() or len(address[k]) > 255 for k in required):
        raise ValueError("Complete all residential address fields")
    address = {k: address[k].strip() for k in required}
    with transaction.atomic():
        User.objects.select_for_update().get(pk=user.pk)
        existing = AddressVerification.objects.filter(user=user, state__in=["pending", "submitted", "review"]).first()
        if existing:
            if existing.address_digest != digest(address):
                raise ValueError("An address check is already in progress")
            return existing
        row = AddressVerification.objects.create(user=user, reference="ZA" + secrets.token_hex(16),
            address_digest=digest(address), encrypted_address=seal(address))
    payload = {**address, "first_name": user.first_name, "last_name": user.last_name,
               "phone": user.phone, "email": user.email, "verification_type": "PERSON"}
    try:
        response = _request("", payload)
    except ProviderError as exc:
        AddressVerification.objects.filter(pk=row.pk).update(state="review", last_error=exc.code)
        raise
    data = response.get("data", {})
    job = data.get("job_id") or data.get("_id") if isinstance(data, dict) else None
    if not isinstance(job, str) or not job:
        AddressVerification.objects.filter(pk=row.pk).update(state="review", last_error="address_job_missing")
        raise ProviderError("address_job_missing", uncertain=True)
    row.provider_job, row.state = job, "submitted"
    row.save(update_fields=["provider_job", "state"])
    return row


def refresh(row):
    if not row.provider_job or row.state in {"verified", "declined"}:
        return row
    response = _request("/status", {"job_id": row.provider_job})
    data = response.get("data", {})
    result = data.get("message", data) if isinstance(data, dict) else {}
    job = (result.get("_id") or result.get("job_id")) if isinstance(result, dict) else None
    if job != row.provider_job:
        row.last_error = "address_job_mismatch"
    else:
        # Public documentation provides pending/unassigned samples only, with no
        # terminal enum or machine-readable report-download contract. Preserve
        # the correlated response securely for operations; never invent approval.
        row.last_error = "address_terminal_contract_required"
        row.encrypted_report = seal({"provider_response": result})
    row.checked_at = timezone.now()
    row.save(update_fields=["last_error", "encrypted_report", "checked_at"])
    return row
