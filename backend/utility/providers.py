"""Third-party integration layer.

Providers: Monnify (payments), Baxi (airtime/data/cable/electricity),
Sendchamp (SMS/OTP), Prembly/IdentityPass (KYC: BVN/NIN/face). Each function
returns {"success": bool, ...}. When the relevant key is blank (dev) it runs in
MOCK mode and simulates success, so the whole app flow is testable without any
external account.

TODO before go-live: verify each provider's exact request/response field names,
endpoints and auth against their dashboards/docs. Live calls can't be exercised
from CI, so the shapes below are documented best-effort scaffolding; the MOCK
paths are the source of truth until real keys are configured.
"""
import base64
import hashlib
import hmac
import secrets

import requests
from django.conf import settings

REQUEST_TIMEOUT = 30


# ---------------------------------------------------------------------------
# Payments (wallet funding) — Monnify
# ---------------------------------------------------------------------------
def payments_live() -> bool:
    m = settings.MONNIFY
    return bool(m["API_KEY"] and m["SECRET_KEY"] and m["CONTRACT_CODE"])


def _monnify_token() -> str:
    """OAuth login: Basic base64(apiKey:secretKey) -> bearer access token."""
    m = settings.MONNIFY
    basic = base64.b64encode(f"{m['API_KEY']}:{m['SECRET_KEY']}".encode()).decode()
    resp = requests.post(
        f"{m['BASE_URL']}/api/v1/auth/login",
        headers={"Authorization": f"Basic {basic}"},
        timeout=REQUEST_TIMEOUT,
    )
    return (resp.json().get("responseBody", {}) or {}).get("accessToken", "")


def payment_initialize(email: str, amount_naira, reference: str) -> dict:
    """Start a funding transaction; returns a checkout URL the app opens.

    MOCK mode returns a sentinel URL so funding is testable offline (the tester
    'completes' it by calling the verify endpoint).
    """
    if not payments_live():
        return {
            "success": True, "mock": True, "reference": reference,
            "authorization_url": f"mock://monnify/checkout/{reference}",
        }
    try:
        token = _monnify_token()
        if not token:
            return {"success": False, "message": "Monnify authentication failed"}
        m = settings.MONNIFY
        resp = requests.post(
            f"{m['BASE_URL']}/api/v1/merchant/transactions/init-transaction",
            json={
                "amount": float(amount_naira),  # Monnify amounts are in naira
                "customerName": (email or "Zitch user").split("@")[0],
                "customerEmail": email,
                "paymentReference": reference,
                "contractCode": m["CONTRACT_CODE"],
                "currencyCode": "NGN",
                "redirectUrl": m.get("REDIRECT_URL", ""),
                "paymentMethods": ["CARD", "ACCOUNT_TRANSFER"],
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        rb = data.get("responseBody", {}) or {}
        return {
            "success": bool(data.get("requestSuccessful")) and bool(rb.get("checkoutUrl")),
            "reference": rb.get("paymentReference", reference),
            "authorization_url": rb.get("checkoutUrl", ""),
            "raw": data,
        }
    except requests.RequestException as exc:
        return {"success": False, "message": f"Payment gateway unreachable: {exc}"}


def payment_verify(reference: str) -> dict:
    """Confirm a transaction with Monnify (source of truth for crediting).

    MOCK mode treats any reference as paid so funding works without real money.
    """
    if not payments_live():
        return {"success": True, "mock": True, "amount_naira": None, "reference": reference}
    try:
        token = _monnify_token()
        m = settings.MONNIFY
        resp = requests.get(
            f"{m['BASE_URL']}/api/v1/merchant/transactions/query",
            params={"paymentReference": reference},
            headers={"Authorization": f"Bearer {token}"},
            timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        rb = data.get("responseBody", {}) or {}
        paid = bool(data.get("requestSuccessful")) and rb.get("paymentStatus") == "PAID"
        return {
            "success": paid,
            "amount_naira": rb.get("amountPaid"),
            "reference": rb.get("paymentReference", reference),
            "raw": data,
        }
    except requests.RequestException as exc:
        return {"success": False, "message": f"Payment gateway unreachable: {exc}"}


def payment_verify_signature(body: bytes, signature: str) -> bool:
    """Validate a Monnify webhook via the `monnify-signature` header
    (SHA-512 HMAC of the raw body with the secret key)."""
    if not payments_live():
        return True  # mock mode: accept so local webhook testing works
    if not signature:
        return False
    digest = hmac.new(settings.MONNIFY["SECRET_KEY"].encode(), body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(digest, signature)


# ---------------------------------------------------------------------------
# Bank transfers / payouts — Monnify disbursements
#
# Draws from your Monnify wallet (MONNIFY_SOURCE_ACCOUNT) to any NIBSS bank.
# Name enquiry is mandatory: Monnify rejects a single transfer whose
# destinationAccountName doesn't match the enquiry result, so callers resolve
# server-side and pass the authoritative name.
# ---------------------------------------------------------------------------
def disbursement_resolve_account(account_number: str, bank_code: str) -> dict:
    """Name enquiry: account number + NIBSS bank code -> account holder name."""
    if not payments_live():
        return {"success": True, "mock": True, "name": "ADEYEMI WILLIAM"}
    try:
        token = _monnify_token()
        m = settings.MONNIFY
        resp = requests.get(
            f"{m['BASE_URL']}/api/v1/disbursements/account/validate",
            params={"accountNumber": account_number, "bankCode": bank_code},
            headers={"Authorization": f"Bearer {token}"},
            timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        rb = data.get("responseBody", {}) or {}
        name = rb.get("accountName", "")
        return {"success": bool(data.get("requestSuccessful")) and bool(name), "name": name, "raw": data}
    except requests.RequestException as exc:
        return {"success": False, "message": f"Payout provider unreachable: {exc}"}


def disbursement_send(amount_naira, reference: str, narration: str,
                      bank_code: str, account_number: str, account_name: str) -> dict:
    """Initiate a single transfer to a bank account.

    SUCCESS/PENDING means Monnify accepted it (money sent or queued); anything
    else — including a 2FA OTP-authorization requirement — is treated as
    not-sent so the caller refunds the wallet.
    TODO before relying on PENDING: handle the OTP-authorization step and a
    disbursement webhook (SUCCESSFUL/FAILED_DISBURSEMENT) to reconcile finally.
    """
    if not payments_live():
        return {"success": True, "mock": True, "status": "SUCCESS"}
    m = settings.MONNIFY
    if not m.get("SOURCE_ACCOUNT"):
        return {"success": False, "message": "MONNIFY_SOURCE_ACCOUNT is not configured"}
    try:
        token = _monnify_token()
        resp = requests.post(
            f"{m['BASE_URL']}/api/v2/disbursements/single",
            json={
                "amount": float(amount_naira),
                "reference": reference,
                "narration": narration or "Zitch transfer",
                "destinationBankCode": bank_code,
                "destinationAccountNumber": account_number,
                "destinationAccountName": account_name,
                "currency": "NGN",
                "sourceAccountNumber": m["SOURCE_ACCOUNT"],
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        status = (data.get("responseBody", {}) or {}).get("status", "")
        return {
            "success": bool(data.get("requestSuccessful")) and status in ("SUCCESS", "PENDING", "COMPLETED"),
            "status": status,
            "message": data.get("responseMessage", "Transfer not completed"),
            "raw": data,
        }
    except requests.RequestException as exc:
        return {"success": False, "message": f"Payout provider unreachable: {exc}"}


# ---------------------------------------------------------------------------
# VTU aggregator (airtime / data / cable / electricity) — Baxi
# ---------------------------------------------------------------------------
def _baxi_live() -> bool:
    return bool(settings.BAXI["API_KEY"])


def _baxi_headers() -> dict:
    return {"x-api-key": settings.BAXI["API_KEY"], "Content-Type": "application/json"}


def vtu_purchase(service_id: str, payload: dict) -> dict:
    """Submit a VTU purchase. MOCK-succeeds when no key is configured.

    TODO: Baxi uses per-service endpoints (airtime/databundle/electricity/
    multichoice) and field names — map service_id to the right endpoint/body per
    their docs. This generic call is scaffolding.
    """
    if not _baxi_live():
        return {
            "success": True, "mock": True,
            "message": "Transaction Successful (mock mode — no aggregator keys set)",
            "provider_reference": "MOCK-" + secrets.token_hex(6).upper(),
        }
    try:
        resp = requests.post(
            f"{settings.BAXI['BASE_URL']}/services/{service_id}/request",
            json=payload, headers=_baxi_headers(), timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        # Baxi signals success via code "200"/status "success"/statusCode 0.
        success = (
            str(data.get("code")) == "200"
            or str(data.get("status")).lower() == "success"
            or str((data.get("data", {}) or {}).get("statusCode")) == "0"
        )
        return {
            "success": success,
            "message": data.get("message", "Transaction processed"),
            "provider_reference": str((data.get("data", {}) or {}).get("transactionReference", "")),
            "raw": data,
        }
    except requests.RequestException as exc:
        return {"success": False, "message": f"Aggregator unreachable: {exc}"}


def vtu_verify_customer(service_id: str, billers_code: str, variation: str = "") -> dict:
    """Validate a meter / smartcard number, returning the customer name."""
    if not _baxi_live():
        return {"success": True, "mock": True, "customer_name": "ADEYEMI WILLIAM"}
    try:
        resp = requests.post(
            f"{settings.BAXI['BASE_URL']}/services/verify",
            json={"service_type": service_id, "account_number": billers_code, "type": variation},
            headers=_baxi_headers(), timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        d = data.get("data", {}) or {}
        name = d.get("name") or d.get("customer_name") or d.get("customerName") or ""
        return {"success": bool(name), "customer_name": name, "raw": data}
    except requests.RequestException as exc:
        return {"success": False, "message": f"Aggregator unreachable: {exc}"}


# ---------------------------------------------------------------------------
# SMS / OTP — Sendchamp
# ---------------------------------------------------------------------------
def send_sms(phone: str, message: str) -> dict:
    cfg = settings.SENDCHAMP
    if not cfg["API_KEY"]:
        return {"success": True, "mock": True, "message": "SMS sent (mock mode)"}
    try:
        resp = requests.post(
            f"{cfg['BASE_URL']}/sms/send",
            json={
                "to": [phone],
                "message": message,
                "sender_name": cfg["SENDER_NAME"],
                "route": "dnd",
            },
            headers={
                "Authorization": f"Bearer {cfg['API_KEY']}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        return {"success": resp.ok and str(data.get("status", "")).lower() == "success", "raw": data}
    except requests.RequestException as exc:
        return {"success": False, "message": f"SMS provider unreachable: {exc}"}


# ---------------------------------------------------------------------------
# KYC — BVN / NIN / liveness — Prembly (IdentityPass)
# ---------------------------------------------------------------------------
def _prembly_live() -> bool:
    return bool(settings.PREMBLY["API_KEY"] and settings.PREMBLY["APP_ID"])


def _prembly_headers() -> dict:
    return {
        "x-api-key": settings.PREMBLY["API_KEY"],
        "app-id": settings.PREMBLY["APP_ID"],
        "Content-Type": "application/json",
    }


def kyc_verify_bvn(bvn: str) -> dict:
    """Verify a BVN. MOCK mode accepts any 11-digit value."""
    if len(bvn) != 11 or not bvn.isdigit():
        return {"success": False, "message": "BVN must be 11 digits"}
    if not _prembly_live():
        return {"success": True, "mock": True, "first_name": "", "last_name": ""}
    try:
        resp = requests.post(
            f"{settings.PREMBLY['BASE_URL']}/identitypass/verification/bvn",
            json={"number": bvn}, headers=_prembly_headers(), timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        d = data.get("data", {}) or {}
        return {"success": bool(data.get("status")) and bool(d), "raw": data,
                "first_name": d.get("first_name", ""), "last_name": d.get("last_name", "")}
    except requests.RequestException as exc:
        return {"success": False, "message": f"KYC provider unreachable: {exc}"}


def kyc_verify_nin(nin: str) -> dict:
    """Verify a NIN. MOCK mode accepts any 11-digit value."""
    if len(nin) != 11 or not nin.isdigit():
        return {"success": False, "message": "NIN must be 11 digits"}
    if not _prembly_live():
        return {"success": True, "mock": True}
    try:
        resp = requests.post(
            f"{settings.PREMBLY['BASE_URL']}/identitypass/verification/nin",
            json={"number": nin}, headers=_prembly_headers(), timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        return {"success": bool(data.get("status")), "raw": data}
    except requests.RequestException as exc:
        return {"success": False, "message": f"KYC provider unreachable: {exc}"}


def kyc_verify_face(selfie: str = "") -> dict:
    """Liveness / selfie-match — the gate for large transfers.

    MOCK accepts offline. LIVE requires a real liveness result AND a captured
    selfie; fails closed without one, so the step-up can't be cleared without
    genuine verification once a provider is configured.
    """
    if not _prembly_live():
        return {"success": True, "mock": True}
    if not selfie:
        return {"success": False, "message": "A selfie capture is required for face verification"}
    try:
        resp = requests.post(
            f"{settings.PREMBLY['BASE_URL']}/identitypass/verification/biometrics/face",
            json={"image": selfie}, headers=_prembly_headers(), timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        d = data.get("data", {}) or {}
        return {"success": bool(data.get("status")) and bool(d.get("liveness") or d.get("face_match")), "raw": data}
    except requests.RequestException as exc:
        return {"success": False, "message": f"KYC provider unreachable: {exc}"}


# ---------------------------------------------------------------------------
# Card issuer (virtual cards) — provider TBD. Blank key => MOCK mode.
# ---------------------------------------------------------------------------
def _card_issuer_live() -> bool:
    return bool(settings.CARD_ISSUER["API_KEY"])


def _card_issuer_headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.CARD_ISSUER['API_KEY']}",
        "Content-Type": "application/json",
    }


def issue_card(holder: str, customer_ref: str) -> dict:
    """Create a virtual card with the issuer. MOCK fabricates presentation data."""
    if not _card_issuer_live():
        return {
            "success": True, "mock": True,
            "card_token": "mock_" + secrets.token_hex(8),
            "brand": settings.CARD_ISSUER.get("BRAND", "Verve"),
            "last4": f"{secrets.randbelow(10000):04d}",
            "expiry": f"{1 + secrets.randbelow(12):02d}/{29 + secrets.randbelow(3)}",
        }
    try:
        resp = requests.post(
            f"{settings.CARD_ISSUER['BASE_URL']}/cards",
            json={"type": "virtual", "currency": "NGN",
                  "brand": settings.CARD_ISSUER.get("BRAND", "Verve"),
                  "holderName": holder, "customerId": customer_ref},
            headers=_card_issuer_headers(), timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        d = data.get("data", {}) or {}
        return {
            "success": bool(d.get("_id") or d.get("id")),
            "card_token": d.get("_id") or d.get("id", ""),
            "brand": d.get("brand", settings.CARD_ISSUER.get("BRAND", "Verve")),
            "last4": (d.get("maskedPan") or d.get("number") or "")[-4:],
            "expiry": f"{d.get('expiryMonth', '')}/{str(d.get('expiryYear', ''))[-2:]}",
            "raw": data,
        }
    except requests.RequestException as exc:
        return {"success": False, "message": f"Card issuer unreachable: {exc}"}


def set_card_status(card_token: str, active: bool) -> dict:
    """Freeze/unfreeze a card with the issuer. MOCK always succeeds."""
    if not _card_issuer_live():
        return {"success": True, "mock": True}
    try:
        resp = requests.put(
            f"{settings.CARD_ISSUER['BASE_URL']}/cards/{card_token}",
            json={"status": "active" if active else "inactive"},
            headers=_card_issuer_headers(), timeout=REQUEST_TIMEOUT,
        )
        return {"success": resp.ok, "raw": resp.json()}
    except requests.RequestException as exc:
        return {"success": False, "message": f"Card issuer unreachable: {exc}"}


def card_secure_details(card_token: str) -> dict:
    """Fetch full PAN/CVV for a one-time reveal. Never persisted server-side.

    MOCK returns a deterministic-looking fake so the reveal UI works.
    """
    if not _card_issuer_live():
        seed = int(hashlib.sha256(card_token.encode()).hexdigest(), 16)
        pan = "5061" + "".join(str((seed >> (i * 4)) % 10) for i in range(12))
        cvv = f"{seed % 1000:03d}"
        return {"success": True, "mock": True, "pan": pan, "cvv": cvv}
    try:
        resp = requests.get(
            f"{settings.CARD_ISSUER['BASE_URL']}/cards/{card_token}/secure-data",
            headers=_card_issuer_headers(), timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        d = data.get("data", {}) or {}
        return {"success": resp.ok, "pan": d.get("number", ""), "cvv": d.get("cvv2", ""), "raw": data}
    except requests.RequestException as exc:
        return {"success": False, "message": f"Card issuer unreachable: {exc}"}


def fund_card(card_token: str, amount) -> dict:
    """Top up an issued card from the funding source. MOCK succeeds."""
    if not _card_issuer_live():
        return {"success": True, "mock": True}
    try:
        resp = requests.post(
            f"{settings.CARD_ISSUER['BASE_URL']}/cards/{card_token}/fund",
            json={"amount": float(amount), "currency": "NGN"},
            headers=_card_issuer_headers(), timeout=REQUEST_TIMEOUT,
        )
        return {"success": resp.ok, "raw": resp.json()}
    except requests.RequestException as exc:
        return {"success": False, "message": f"Card issuer unreachable: {exc}"}
