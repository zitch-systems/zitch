"""ClubConnect / ClubKonnect (Nellobytes) VTU client.

A second VTU provider behind the same contract as the Baxi client in
``providers.py`` — ``cc_purchase`` / ``cc_requery`` / ``cc_verify_customer``
return the exact ``{"success", "pending"?, "provider_reference", "token", ...}``
shapes ``settle_or_refund`` and the reconcile job expect, so nothing downstream
changes when ``settings.VTU_PROVIDER == "clubconnect"``.

ClubConnect is an HTTPS **GET** API authed with ``UserID`` + ``APIKey`` query
params; each service has its own ``APIxxxV1.asp`` endpoint. It is **asynchronous**:
a purchase usually returns ``ORDER_RECEIVED`` and completes shortly after, so we
map that to ``pending`` (money held, never refunded for a maybe-delivered order)
and let the reconcile job requery it to a final state — the same safety model the
Baxi path uses.

Blank creds => MOCK mode (simulated success) so the flow is testable offline.

VERIFY-BEFORE-LIVE: the endpoint paths, the service/network/company codes, the
response field names and the status strings below are implemented to ClubConnect's
documented GET API but could not be confirmed against the live dashboard from CI.
Every provider-specific value is isolated in the maps/constants at the top of this
file — confirm them against your ClubConnect API docs before setting
VTU_PROVIDER=clubconnect in production. Until then the default provider (Baxi) and
MOCK mode keep every flow working and tested.
"""
import secrets

import requests
from django.conf import settings

CC_TIMEOUT = 30

# --- Endpoint paths (relative to CLUBCONNECT BASE_URL) — VERIFY-BEFORE-LIVE ---
_EP_AIRTIME = "APIAirtimeV1.asp"
_EP_DATA = "APIDatabundleV1.asp"
_EP_CABLE = "APICableTVV1.asp"
_EP_ELECTRIC = "APIElectricityV1.asp"
_EP_BETTING = "APIBettingV1.asp"
_EP_EXAM = "APIWAECV1.asp"
_EP_QUERY = "APIQueryV1.asp"
_EP_VERIFY_CABLE = "APIVerifyCableTVV1.asp"
_EP_VERIFY_ELECTRIC = "APIVerifyElectricityV1.asp"

# --- Service code maps — VERIFY-BEFORE-LIVE (confirm every code on the dashboard) ---
# Network slug (as the views build it) -> ClubConnect MobileNetwork code.
_CC_NETWORK = {"mtn": "01", "glo": "02", "9mobile": "03", "etisalat": "03", "airtel": "04"}
# Cable slug -> ClubConnect CableTV code.
_CC_CABLE = {"dstv": "dstv", "gotv": "gotv", "startimes": "startimes"}
# Disco slug -> ClubConnect ElectricCompany code.
_CC_DISCO = {
    "ikeja": "ikeja-electric", "eko": "eko-electric", "abuja": "abuja-electric",
    "kano": "kano-electric", "port harcourt": "portharcourt-electric",
    "jos": "jos-electric", "kaduna": "kaduna-electric", "enugu": "enugu-electric",
    "ibadan": "ibadan-electric", "benin": "benin-electric",
}
# Prepaid/postpaid -> ClubConnect MeterType code.
_CC_METER_TYPE = {"prepaid": "01", "postpaid": "02"}
# Exam product code -> ClubConnect ExamType.
_CC_EXAM = {"waec": "waec", "neco": "neco", "nabteb": "nabteb", "jamb": "jamb"}

# Status strings that mean the order is still in flight (hold PENDING, requery
# later — never refund). Anything not here and not a completion is a hard failure.
_CC_PENDING_STATES = {"ORDER_RECEIVED", "ORDER_ONHOLD", "ORDER_PROCESSING", "PROCESSING"}
_CC_SUCCESS_STATES = {"ORDER_COMPLETED", "ORDER_COMPLETE", "COMPLETED", "SUCCESSFUL", "SUCCESS"}


def _cc_live() -> bool:
    cc = settings.CLUBCONNECT
    return bool(cc["USER_ID"] and cc["API_KEY"])


def _auth() -> dict:
    cc = settings.CLUBCONNECT
    return {"UserID": cc["USER_ID"], "APIKey": cc["API_KEY"]}


def _amount(value) -> int:
    try:
        return int(round(float(value)))  # VTU amounts are whole naira
    except (TypeError, ValueError):
        return 0


def _build(service_id: str, payload: dict, reference: str):
    """Map (service_id, view payload) -> (endpoint_path, GET query params).

    Returns (None, {}) for an unrecognised service. ``reference`` is sent as
    ClubConnect's ``RequestID`` (its idempotency key) so a retry or requery of
    the same purchase reconciles to one provider order rather than charging twice.
    """
    sid = service_id.lower()
    base = {**_auth(), "RequestID": reference}

    if sid.endswith("-airtime"):
        net = sid[: -len("-airtime")]
        return _EP_AIRTIME, {**base, "MobileNetwork": _CC_NETWORK.get(net, net),
                             "Amount": _amount(payload.get("amount")),
                             "MobileNumber": payload.get("phone", "")}
    if sid.endswith("-data"):
        net = sid[: -len("-data")]
        return _EP_DATA, {**base, "MobileNetwork": _CC_NETWORK.get(net, net),
                          "DataPlan": payload.get("variation_code", ""),
                          "MobileNumber": payload.get("phone") or payload.get("billersCode", "")}
    if sid.endswith("-electric"):
        disco = sid[: -len("-electric")]
        meter_type = (payload.get("variation_code") or "prepaid").lower()
        return _EP_ELECTRIC, {**base, "ElectricCompany": _CC_DISCO.get(disco, disco),
                              "MeterType": _CC_METER_TYPE.get(meter_type, "01"),
                              "MeterNo": payload.get("billersCode", ""),
                              "Amount": _amount(payload.get("amount")),
                              "PhoneNo": payload.get("phone", "")}
    if sid in _CC_CABLE:
        return _EP_CABLE, {**base, "CableTV": _CC_CABLE[sid],
                           "Package": payload.get("variation_code", ""),
                           "SmartCardNo": payload.get("billersCode", ""),
                           "PhoneNo": payload.get("phone", "")}
    if sid.endswith("-betting"):
        company = sid[: -len("-betting")]
        return _EP_BETTING, {**base, "BettingCompany": company,
                             "CustomerID": payload.get("billersCode", ""),
                             "Amount": _amount(payload.get("amount"))}
    if sid.endswith("-pin"):
        exam = sid[: -len("-pin")]
        return _EP_EXAM, {**base, "ExamType": _CC_EXAM.get(exam, exam),
                          "PhoneNo": payload.get("phone", ""),
                          "Quantity": _amount(payload.get("quantity")) or 1}
    return None, {}


def _get(endpoint: str, params: dict) -> dict:
    """GET a ClubConnect endpoint and return parsed JSON, or raise RequestException."""
    resp = requests.get(f"{settings.CLUBCONNECT['BASE_URL']}/{endpoint}",
                        params=params, timeout=CC_TIMEOUT)
    return resp.json()


def _parse(data: dict) -> dict:
    """Normalise a ClubConnect response to settle_or_refund's contract.

    success => delivered; pending => still in flight (requery later, don't refund);
    neither => a definitive failure the caller refunds. The string ``status`` is
    the authoritative signal (statuscode numbers vary by service).
    """
    status = str(data.get("status") or data.get("Status") or "").upper()
    order_id = str(data.get("orderid") or data.get("OrderID") or data.get("requestid") or "")
    token = (data.get("metertoken") or data.get("MeterToken") or data.get("token")
             or (data.get("customer", {}) or {}).get("metertoken", "") or "")
    pins = data.get("pins") or data.get("Pins") or data.get("pin") or []
    out = {
        "success": status in _CC_SUCCESS_STATES,
        "message": data.get("remark") or data.get("status") or "Transaction processed",
        "provider_reference": order_id,
        "token": token,
        "raw": data,
    }
    if pins:
        out["pins"] = pins
    if not out["success"] and status in _CC_PENDING_STATES:
        out["pending"] = True
    return out


def cc_purchase(service_id: str, payload: dict, reference: str | None = None) -> dict:
    """Submit a ClubConnect purchase. MOCK-succeeds when no creds are set.

    On a network error returns ``pending=True`` (the order may have landed — the
    caller must NOT refund; reconciliation requeries by RequestID instead).
    """
    ref = reference or ("ZCC-" + secrets.token_hex(6).upper())
    if not _cc_live():
        return {"success": True, "mock": True,
                "message": "Transaction Successful (mock mode — no ClubConnect keys set)",
                "provider_reference": "MOCK-" + secrets.token_hex(6).upper()}
    endpoint, params = _build(service_id, payload, ref)
    if endpoint is None:
        return {"success": False, "message": f"Unsupported service: {service_id}"}
    try:
        return _parse(_get(endpoint, params))
    except requests.RequestException as exc:
        return {"success": False, "pending": True, "message": f"ClubConnect unreachable: {exc}"}
    except ValueError:
        # Non-JSON body (e.g. an HTML error page): outcome unknown, so hold the
        # purchase PENDING for requery rather than refunding a maybe-delivered order.
        return {"success": False, "pending": True, "message": "ClubConnect returned a non-JSON response"}


def cc_requery(reference: str) -> dict:
    """Requery a purchase by our RequestID to settle a PENDING transaction.

    Returns settle_or_refund's shape: success => delivered; pending => still
    unknown (retry later); neither => definitive failure (refund). An
    unrecognised/empty result is kept PENDING so a delivered order is never
    refunded by mistake. MOCK treats it as delivered.
    """
    if not _cc_live():
        return {"success": True, "mock": True, "message": "Delivered (mock requery)"}
    try:
        parsed = _parse(_get(_EP_QUERY, {**_auth(), "RequestID": reference}))
    except (requests.RequestException, ValueError):
        return {"success": False, "pending": True, "message": "Requery failed; will retry"}
    if not parsed.get("success") and not parsed.get("provider_reference"):
        parsed["pending"] = True  # no confirmed status yet — don't refund
    return parsed


def cc_verify_customer(service_id: str, billers_code: str, variation: str = "") -> dict:
    """Validate a meter / smartcard number, returning the customer name."""
    if not _cc_live():
        return {"success": True, "mock": True, "customer_name": "ADEYEMI WILLIAM"}
    sid = service_id.lower()
    try:
        if sid.endswith("-electric"):
            disco = sid[: -len("-electric")]
            params = {**_auth(), "ElectricCompany": _CC_DISCO.get(disco, disco),
                      "MeterNo": billers_code,
                      "MeterType": _CC_METER_TYPE.get((variation or "prepaid").lower(), "01")}
            data = _get(_EP_VERIFY_ELECTRIC, params)
        else:
            params = {**_auth(), "CableTV": _CC_CABLE.get(sid, sid), "SmartCardNo": billers_code}
            data = _get(_EP_VERIFY_CABLE, params)
    except (requests.RequestException, ValueError) as exc:
        return {"success": False, "message": f"ClubConnect unreachable: {exc}"}
    name = (data.get("customer_name") or data.get("customerName") or data.get("name")
            or (data.get("customer", {}) or {}).get("name", ""))
    return {"success": bool(name), "customer_name": name, "raw": data}
