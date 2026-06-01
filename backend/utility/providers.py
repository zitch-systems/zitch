"""Third-party integration layer (VTU aggregator, payments, SMS).

Each function returns a dict: {"success": bool, "message": str, ...}. When the
relevant API key is blank (dev), it runs in MOCK mode and simulates success so
the entire app flow is testable end-to-end without external accounts.

TODO before go-live: fill in the real request/response mapping for your chosen
aggregator (VTpass shown) and verify field names against their docs.
"""
import hashlib
import hmac
import secrets
from decimal import ROUND_HALF_UP, Decimal

import requests
from django.conf import settings

REQUEST_TIMEOUT = 30


def to_kobo(amount_naira) -> int:
    """Exact naira -> kobo conversion.

    Using floats here truncates money: int(float("1234.56") * 100) == 123455,
    losing a kobo. Decimal keeps it exact: 123456.
    """
    return int((Decimal(str(amount_naira)) * 100).to_integral_value(rounding=ROUND_HALF_UP))


def paystack_live() -> bool:
    return bool(settings.PAYSTACK["SECRET_KEY"])


# ---------------------------------------------------------------------------
# VTU aggregator (airtime / data / cable / electricity) — VTpass example
# ---------------------------------------------------------------------------
def _vtpass_live() -> bool:
    return bool(settings.VTPASS["API_KEY"] and settings.VTPASS["SECRET_KEY"])


def _vtpass_headers() -> dict:
    return {
        "api-key": settings.VTPASS["API_KEY"],
        "secret-key": settings.VTPASS["SECRET_KEY"],
        "Content-Type": "application/json",
    }


def vtu_purchase(service_id: str, payload: dict) -> dict:
    """Submit a VTU purchase. MOCK-succeeds when no keys are configured."""
    if not _vtpass_live():
        return {
            "success": True,
            "mock": True,
            "message": "Transaction Successful (mock mode — no aggregator keys set)",
            "provider_reference": "MOCK-" + secrets.token_hex(6).upper(),
        }
    try:
        resp = requests.post(
            f"{settings.VTPASS['BASE_URL']}/pay",
            json={"serviceID": service_id, **payload},
            headers=_vtpass_headers(),
            timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        # VTpass returns code "000" on success.
        success = str(data.get("code")) == "000"
        return {
            "success": success,
            "message": data.get("response_description", "Transaction processed"),
            "provider_reference": data.get("requestId", ""),
            "raw": data,
        }
    except requests.RequestException as exc:
        return {"success": False, "message": f"Aggregator unreachable: {exc}"}


def vtu_verify_customer(service_id: str, billers_code: str, variation: str = "") -> dict:
    """Validate a meter / smartcard number, returning the customer name."""
    if not _vtpass_live():
        return {"success": True, "mock": True, "customer_name": "ADEYEMI WILLIAM"}
    try:
        resp = requests.post(
            f"{settings.VTPASS['BASE_URL']}/merchant-verify",
            json={"serviceID": service_id, "billersCode": billers_code, "type": variation},
            headers=_vtpass_headers(),
            timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        content = data.get("content", {}) or {}
        name = content.get("Customer_Name") or content.get("customerName") or ""
        return {"success": bool(name), "customer_name": name, "raw": data}
    except requests.RequestException as exc:
        return {"success": False, "message": f"Aggregator unreachable: {exc}"}


# ---------------------------------------------------------------------------
# Payments (wallet funding) — Paystack example
# ---------------------------------------------------------------------------
def paystack_initialize(email: str, amount_naira, reference: str) -> dict:
    """Start a funding transaction. Returns a checkout URL the app opens.

    In MOCK mode (no secret key) we return a sentinel URL the app/tester can
    'complete' by calling the verify endpoint, so funding is testable offline.
    """
    if not paystack_live():
        return {
            "success": True,
            "mock": True,
            "reference": reference,
            "authorization_url": f"mock://paystack/checkout/{reference}",
        }
    try:
        resp = requests.post(
            "https://api.paystack.co/transaction/initialize",
            json={
                "email": email,
                "amount": to_kobo(amount_naira),
                "reference": reference,
            },
            headers={"Authorization": f"Bearer {settings.PAYSTACK['SECRET_KEY']}"},
            timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        d = data.get("data", {}) or {}
        return {
            "success": bool(data.get("status")),
            "reference": d.get("reference", reference),
            "authorization_url": d.get("authorization_url", ""),
            "raw": data,
        }
    except requests.RequestException as exc:
        return {"success": False, "message": f"Payment gateway unreachable: {exc}"}


def paystack_verify(reference: str) -> dict:
    """Confirm a transaction with Paystack. Source of truth for crediting.

    MOCK mode treats any reference as a successful payment so the funding flow
    can be exercised without real money.
    """
    if not paystack_live():
        return {"success": True, "mock": True, "amount_naira": None, "reference": reference}
    try:
        resp = requests.get(
            f"https://api.paystack.co/transaction/verify/{reference}",
            headers={"Authorization": f"Bearer {settings.PAYSTACK['SECRET_KEY']}"},
            timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        d = data.get("data", {}) or {}
        ok = bool(data.get("status")) and d.get("status") == "success"
        return {
            "success": ok,
            "amount_naira": (d.get("amount", 0) / 100) if d.get("amount") is not None else None,
            "reference": d.get("reference", reference),
            "raw": data,
        }
    except requests.RequestException as exc:
        return {"success": False, "message": f"Payment gateway unreachable: {exc}"}


def paystack_verify_signature(body: bytes, signature: str) -> bool:
    """Validate a Paystack webhook via the x-paystack-signature header (HMAC-SHA512)."""
    if not paystack_live():
        return True  # mock mode: accept so local webhook testing works
    if not signature:
        return False
    digest = hmac.new(
        settings.PAYSTACK["SECRET_KEY"].encode(), body, hashlib.sha512
    ).hexdigest()
    return hmac.compare_digest(digest, signature)


# ---------------------------------------------------------------------------
# SMS / OTP — Termii example
# ---------------------------------------------------------------------------
def send_sms(phone: str, message: str) -> dict:
    if not settings.TERMII["API_KEY"]:
        return {"success": True, "mock": True, "message": "SMS sent (mock mode)"}
    try:
        resp = requests.post(
            "https://api.ng.termii.com/api/sms/send",
            json={
                "to": phone,
                "from": settings.TERMII["SENDER_ID"],
                "sms": message,
                "type": "plain",
                "channel": "generic",
                "api_key": settings.TERMII["API_KEY"],
            },
            timeout=REQUEST_TIMEOUT,
        )
        return {"success": resp.ok, "raw": resp.json()}
    except requests.RequestException as exc:
        return {"success": False, "message": f"SMS provider unreachable: {exc}"}


# ---------------------------------------------------------------------------
# KYC — BVN / NIN / liveness (Dojah example)
# ---------------------------------------------------------------------------
def _kyc_live() -> bool:
    return bool(settings.KYC["APP_ID"] and settings.KYC["SECRET_KEY"])


def _kyc_headers() -> dict:
    return {"AppId": settings.KYC["APP_ID"], "Authorization": settings.KYC["SECRET_KEY"]}


def kyc_verify_bvn(bvn: str) -> dict:
    """Verify a BVN. MOCK mode accepts any 11-digit value.

    TODO: confirm the exact Dojah endpoint/response shape (or swap provider).
    """
    if len(bvn) != 11 or not bvn.isdigit():
        return {"success": False, "message": "BVN must be 11 digits"}
    if not _kyc_live():
        return {"success": True, "mock": True, "first_name": "", "last_name": ""}
    try:
        resp = requests.get(
            f"{settings.KYC['BASE_URL']}/api/v1/kyc/bvn/full",
            params={"bvn": bvn}, headers=_kyc_headers(), timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        entity = data.get("entity", {}) or {}
        return {"success": bool(entity), "raw": data,
                "first_name": entity.get("first_name", ""), "last_name": entity.get("last_name", "")}
    except requests.RequestException as exc:
        return {"success": False, "message": f"KYC provider unreachable: {exc}"}


def kyc_verify_nin(nin: str) -> dict:
    """Verify a NIN. MOCK mode accepts any 11-digit value."""
    if len(nin) != 11 or not nin.isdigit():
        return {"success": False, "message": "NIN must be 11 digits"}
    if not _kyc_live():
        return {"success": True, "mock": True}
    try:
        resp = requests.get(
            f"{settings.KYC['BASE_URL']}/api/v1/kyc/nin",
            params={"nin": nin}, headers=_kyc_headers(), timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        return {"success": bool(data.get("entity")), "raw": data}
    except requests.RequestException as exc:
        return {"success": False, "message": f"KYC provider unreachable: {exc}"}
