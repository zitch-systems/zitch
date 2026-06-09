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
#
# Baxi exposes a distinct endpoint per service (not one generic path), so we
# route on the service_id the views pass ("mtn-airtime", "mtn-data", "dstv",
# "ikeja-electric", ...) and translate to Baxi's request body.
#
# VERIFY-BEFORE-LIVE: the endpoint *paths* below are stable, but the exact
# service_type codes, body field names, and the prepaid meter token's location
# in the response must be confirmed against your Baxi dashboard/docs (they
# couldn't be fetched from CI). The maps below are the single place to adjust.
# ---------------------------------------------------------------------------
_BAXI_AIRTIME = {  # network slug -> Baxi service_type
    "mtn": "mtn", "glo": "glo", "airtel": "airtel",
    "9mobile": "etisalat", "etisalat": "etisalat",
}
_BAXI_CABLE = {"dstv": "dstv", "gotv": "gotv", "startimes": "startimes"}
_BAXI_DISCO = {  # disco slug -> Baxi service_type
    "ikeja": "ikeja_electric", "eko": "eko_electric", "abuja": "abuja_electric",
    "kano": "kano_electric", "port harcourt": "portharcourt_electric",
    "jos": "jos_electric", "kaduna": "kaduna_electric", "enugu": "enugu_electric",
    "ibadan": "ibadan_electric", "benin": "benin_electric",
}


def _baxi_live() -> bool:
    return bool(settings.BAXI["API_KEY"])


def _baxi_headers() -> dict:
    return {"x-api-key": settings.BAXI["API_KEY"], "Content-Type": "application/json"}


def _baxi_amount(value) -> int:
    try:
        return int(round(float(value)))  # VTU amounts are whole naira
    except (TypeError, ValueError):
        return 0


def _disco_service_type(slug: str) -> str:
    return _BAXI_DISCO.get(slug, slug.replace(" ", "") + "_electric")


def _baxi_build_request(service_id: str, payload: dict, reference: str | None = None):
    """Map (service_id, view payload) -> (endpoint_path, Baxi request body).

    Returns (None, {}) for an unrecognised service. The wallet ledger reference
    is threaded through as Baxi's agentReference (its idempotency key), so a
    retry or requery of the same purchase reconciles to one provider
    transaction rather than charging twice.
    """
    sid = service_id.lower()
    ref = reference or ("ZB-" + secrets.token_hex(6).upper())
    if sid.endswith("-airtime"):
        net = sid[: -len("-airtime")]
        return "services/airtime/request", {
            "service_type": _BAXI_AIRTIME.get(net, net),
            "phone": payload.get("phone", ""),
            "amount": _baxi_amount(payload.get("amount")),
            "agentReference": ref,
        }
    if sid.endswith("-data"):
        net = sid[: -len("-data")]
        return "services/databundle/request", {
            "service_type": f"{_BAXI_AIRTIME.get(net, net)}-data",
            "phone": payload.get("phone") or payload.get("billersCode", ""),
            "datacode": payload.get("variation_code", ""),
            "agentReference": ref,
        }
    if sid.endswith("-electric"):
        return "services/electricity/request", {
            "service_type": _disco_service_type(sid[: -len("-electric")]),
            "account_number": payload.get("billersCode", ""),
            "amount": _baxi_amount(payload.get("amount")),
            "MeterType": payload.get("variation_code") or "prepaid",
            "phone": payload.get("phone", ""),
            "agentReference": ref,
        }
    if sid in _BAXI_CABLE:
        return "services/multichoice/request", {
            "service_type": _BAXI_CABLE[sid],
            "account_number": payload.get("billersCode", ""),
            "product_code": payload.get("variation_code", ""),
            "agentReference": ref,
        }
    return None, {}


def _baxi_parse(data: dict) -> dict:
    d = data.get("data", {}) or {}
    success = (
        str(data.get("code")) == "200"
        or str(data.get("status")).lower() == "success"
        or str(d.get("statusCode")) == "0"
    )
    return {
        "success": success,
        "message": data.get("message") or d.get("transactionMessage", "Transaction processed"),
        "provider_reference": str(d.get("transactionReference") or d.get("baxiReference", "")),
        "token": d.get("token") or (d.get("rawOutput", {}) or {}).get("standardTokenValue", ""),
        "raw": data,
    }


def vtu_purchase(service_id: str, payload: dict, reference: str | None = None) -> dict:
    """Submit a VTU purchase, routing to Baxi's per-service endpoint.

    Pass the wallet ledger `reference` so it becomes Baxi's agentReference
    (idempotency key). MOCK-succeeds when no key is configured so the flow is
    testable offline. On a network error returns ``pending=True``: the purchase
    may actually have landed, so the caller must NOT refund — reconciliation
    requeries it by reference instead.
    """
    if not _baxi_live():
        return {
            "success": True, "mock": True,
            "message": "Transaction Successful (mock mode — no aggregator keys set)",
            "provider_reference": "MOCK-" + secrets.token_hex(6).upper(),
        }
    endpoint, body = _baxi_build_request(service_id, payload, reference)
    if endpoint is None:
        return {"success": False, "message": f"Unsupported service: {service_id}"}
    try:
        resp = requests.post(
            f"{settings.BAXI['BASE_URL']}/{endpoint}",
            json=body, headers=_baxi_headers(), timeout=REQUEST_TIMEOUT,
        )
        return _baxi_parse(resp.json())
    except requests.RequestException as exc:
        return {"success": False, "pending": True, "message": f"Aggregator unreachable: {exc}"}


def vtu_requery(reference: str) -> dict:
    """Requery a submitted purchase by our agentReference to settle a PENDING
    transaction (e.g. one whose original send timed out).

    Returns the {"success", "pending", ...} shape settle_or_refund expects:
    success => delivered; pending => still unknown (retry later); neither =>
    a definitive failure the caller refunds. MOCK treats it as delivered.

    VERIFY-BEFORE-LIVE: confirm Baxi's requery path and response shape; an
    unrecognised/empty result is kept PENDING so a delivered purchase is never
    refunded by mistake.
    """
    if not _baxi_live():
        return {"success": True, "mock": True, "message": "Delivered (mock requery)"}
    try:
        resp = requests.post(
            f"{settings.BAXI['BASE_URL']}/services/transaction/requery",
            json={"agentReference": reference}, headers=_baxi_headers(), timeout=REQUEST_TIMEOUT,
        )
        parsed = _baxi_parse(resp.json())
        if not parsed.get("success") and not parsed.get("provider_reference"):
            parsed["pending"] = True  # no confirmed status yet — don't refund
        return parsed
    except requests.RequestException:
        return {"success": False, "pending": True, "message": "Requery failed; will retry"}


def vtu_verify_customer(service_id: str, billers_code: str, variation: str = "") -> dict:
    """Validate a meter / smartcard number, returning the customer name."""
    if not _baxi_live():
        return {"success": True, "mock": True, "customer_name": "ADEYEMI WILLIAM"}
    sid = service_id.lower()
    if sid.endswith("-electric"):
        service_type = _disco_service_type(sid[: -len("-electric")])
    else:
        service_type = _BAXI_CABLE.get(sid, sid)
    body = {"service_type": service_type, "account_number": billers_code}
    if variation:
        body["type"] = variation  # prepaid / postpaid for electricity
    try:
        resp = requests.post(
            f"{settings.BAXI['BASE_URL']}/services/verify",
            json=body, headers=_baxi_headers(), timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        d = data.get("data", {}) or {}
        name = (d.get("name") or d.get("customer_name") or d.get("customerName")
                or (d.get("customer", {}) or {}).get("name", ""))
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


# --------------------------------------------------------------------------- #
# Fincra — FX conversion rail (multi-currency). MOCK mode when no secret key:
# deterministic mid-market rates + auto-settle, so the flow is testable offline.
# --------------------------------------------------------------------------- #
def fincra_live() -> bool:
    return bool(settings.FINCRA.get("SECRET_KEY"))


# Mock mid-market reference (NGN per 1 unit) — only used without keys.
_NGN_PER = {"NGN": "1", "USD": "1600", "GBP": "2000", "CAD": "1150", "CNY": "220"}


def fx_quote(from_ccy: str, to_ccy: str, sell_amount) -> dict:
    """Quote a conversion: {success, rate, receive_amount, quote_ref, ttl_seconds}.
    `rate` is units of `to` per 1 `from`."""
    from decimal import Decimal

    if not fincra_live():
        f, t = _NGN_PER.get(from_ccy), _NGN_PER.get(to_ccy)
        if f is None or t is None:
            return {"success": False, "message": f"Unsupported pair {from_ccy}/{to_ccy}"}
        rate = Decimal(f) / Decimal(t)
        receive = Decimal(str(sell_amount)) * rate
        return {"success": True, "mock": True, "rate": rate, "receive_amount": receive,
                "quote_ref": "FXQ-" + secrets.token_hex(6).upper(), "ttl_seconds": 90}
    try:
        r = requests.post(
            f"{settings.FINCRA['BASE_URL']}/quotes",
            json={"action": "send", "sourceCurrency": from_ccy, "destinationCurrency": to_ccy,
                  "amount": str(sell_amount), "feeBearer": "business"},
            headers={"api-key": settings.FINCRA["SECRET_KEY"]}, timeout=20,
        )
        d = (r.json() or {}).get("data", {})
        if not r.ok or not d.get("rate"):
            return {"success": False, "message": (r.json() or {}).get("message", "Quote failed")}
        return {"success": True, "rate": d["rate"], "receive_amount": d.get("destinationAmount"),
                "quote_ref": d.get("reference") or d.get("quoteReference", ""), "ttl_seconds": int(d.get("expiry", 90))}
    except requests.RequestException as exc:
        return {"success": False, "message": f"FX provider unreachable: {exc}"}


def fx_execute(quote_ref: str) -> dict:
    """Execute a previously quoted conversion against its quote reference."""
    if not fincra_live():
        return {"success": True, "mock": True}
    try:
        r = requests.post(
            f"{settings.FINCRA['BASE_URL']}/conversions",
            json={"quoteReference": quote_ref, "business": settings.FINCRA.get("BUSINESS_ID", "")},
            headers={"api-key": settings.FINCRA["SECRET_KEY"]}, timeout=30,
        )
        return {"success": bool(r.ok), "raw": (r.json() if r.content else {})}
    except requests.RequestException as exc:
        return {"success": False, "message": f"FX execute failed: {exc}"}
