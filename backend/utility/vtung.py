"""VTU.ng (v2) VTU client.

The sole VTU provider. ``vt_purchase`` / ``vt_requery`` / ``vt_verify_customer``
return the ``{"success", "pending"?, "provider_reference", "token", ...}`` shapes
that ``settle_or_refund`` and the reconcile job expect; the thin
``utility.providers.vtu_*`` wrappers delegate straight to them.

VTU.ng v2 is a modern REST/JSON API:
  * Auth: JWT Bearer. Either a long-lived token (VTUNG_API_KEY) or a username +
    password from which we fetch a JWT (``POST /wp-json/jwt-auth/v1/token``) and
    cache it (the token lasts ~7 days; we cache for 6 and re-login on a 401).
    No server-IP whitelisting required.
  * Purchases: ``POST /wp-json/api/v2/{airtime,data,tv,electricity,betting}`` with
    a client ``request_id`` (our ledger reference = idempotency key).
  * Status: ``GET /wp-json/api/v2/requery?request_id=...``.
  * Response: ``{"code","message","data":{"order_id","status",...}}``; status is
    ``completed-api`` (delivered), ``processing-api`` (in flight → requery, never
    refund), or ``failed``/``refunded`` (definitive failure → refund).

Blank creds => MOCK mode (simulated success) so the flow is testable offline.

VTU.ng does NOT sell exam e-PINs, so ``*-pin`` services are unsupported here
(they resolve to a clean failure -> refund; deactivate exam products in the
admin until an e-PIN provider is wired).

VERIFY-BEFORE-LIVE: the airtime/data endpoints, the JWT auth, the requery
endpoint and the status strings are confirmed against the v2 docs. Still confirm
on your dashboard: the tv/electricity/betting request field names, the
customer-verification endpoint path, the prepaid-meter token field, the 9mobile
service_id, and that your data/cable variation_id codes match VTU.ng's. All are
isolated in the maps/constants below.
"""
import logging
import secrets

import requests
from django.conf import settings
from django.core.cache import cache

log = logging.getLogger("zitch")

VT_TIMEOUT = 30
_TOKEN_CACHE_KEY = "vtung_jwt_token"
_TOKEN_TTL = 6 * 24 * 3600  # 6 days (< the ~7-day JWT lifetime)

# --- API paths ---
_TOKEN_PATH = "wp-json/jwt-auth/v1/token"
_V2 = "wp-json/api/v2"
_EP_AIRTIME = f"{_V2}/airtime"
_EP_DATA = f"{_V2}/data"
_EP_TV = f"{_V2}/tv"
_EP_ELECTRIC = f"{_V2}/electricity"
_EP_BETTING = f"{_V2}/betting"
_EP_REQUERY = f"{_V2}/requery"
_EP_VERIFY = f"{_V2}/verify-customer"   # VERIFY-BEFORE-LIVE: confirm exact path

# --- Service code maps (VTU.ng uses lowercase service_id names) ---
# Network slug (as the views build it) -> VTU.ng airtime/data service_id.
_VT_NETWORK = {"mtn": "mtn", "glo": "glo", "airtel": "airtel",
               "9mobile": "etisalat", "etisalat": "etisalat"}  # VERIFY: 9mobile id
# Cable slug -> VTU.ng tv service_id.
_VT_CABLE = {"dstv": "dstv", "gotv": "gotv", "startimes": "startimes"}
# Disco slug (lowercased DISCO_NAMES from utility.views) -> VTU.ng electricity
# service_id (hyphenated, e.g. "ikeja-electric").
_VT_DISCO = {
    "ikeja": "ikeja-electric", "eko": "eko-electric", "abuja": "abuja-electric",
    "kano": "kano-electric", "port harcourt": "portharcourt-electric",
    "jos": "jos-electric", "kaduna": "kaduna-electric", "enugu": "enugu-electric",
    "ibadan": "ibadan-electric",
}

# data.status values (lowercased) -> outcome.
_VT_SUCCESS = {"completed-api", "completed", "delivered", "successful", "success"}
_VT_PENDING = {"processing-api", "processing", "pending", "initiated", "queued"}
_VT_FAILED = {"failed", "refunded", "cancelled", "reversed", "declined"}


def _live() -> bool:
    cfg = settings.VTUNG
    return bool(cfg["API_KEY"] or (cfg["USERNAME"] and cfg["PASSWORD"]))


def _base() -> str:
    return settings.VTUNG["BASE_URL"].rstrip("/")


def _login() -> str:
    """Fetch a fresh JWT with username/password and cache it. Returns "" on failure."""
    cfg = settings.VTUNG
    try:
        resp = requests.post(f"{_base()}/{_TOKEN_PATH}",
                             json={"username": cfg["USERNAME"], "password": cfg["PASSWORD"]},
                             timeout=VT_TIMEOUT)
        body = resp.json() or {}
        # Accept the token at the top level or nested under "data" (provider JSON
        # shapes vary); without this a nested token reads as "" and every call goes
        # out unauthenticated -> 401 -> the purchase looks like a provider failure.
        token = body.get("token", "") or (body.get("data") or {}).get("token", "")
    except (requests.RequestException, ValueError):
        return ""
    if token:
        cache.set(_TOKEN_CACHE_KEY, token, _TOKEN_TTL)
    else:
        # Surface a silent auth failure instead of letting it masquerade as a VTU
        # failure (which would refund the user but never explain why).
        log.warning("vtung_login_no_token status=%s", getattr(resp, "status_code", "?"))
    return token


def _token(force_refresh: bool = False) -> str:
    """Bearer token: a static API key if set, else a cached/fresh JWT."""
    cfg = settings.VTUNG
    if cfg["API_KEY"]:
        return cfg["API_KEY"]
    if not force_refresh:
        cached = cache.get(_TOKEN_CACHE_KEY)
        if cached:
            return cached
    return _login()


def _request(method: str, path: str, *, json_body=None, params=None) -> dict:
    """Authorized v2 call returning parsed JSON. Re-logins once on a 401.

    Raises requests.RequestException on network failure; raises ValueError on a
    non-JSON body — callers map both to a safe 'pending' (never a wrong refund).
    """
    url = f"{_base()}/{path}"
    token = _token()
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    resp = requests.request(method, url, headers=headers, json=json_body,
                            params=params, timeout=VT_TIMEOUT)
    if resp.status_code == 401 and not settings.VTUNG["API_KEY"]:
        # Token expired/invalid: re-login once and retry.
        token = _token(force_refresh=True)
        headers["Authorization"] = f"Bearer {token}"
        resp = requests.request(method, url, headers=headers, json=json_body,
                                params=params, timeout=VT_TIMEOUT)
    return resp.json()


def _build(service_id: str, payload: dict, reference: str):
    """Map (service_id, view payload) -> (endpoint_path, JSON body).

    Returns (None, {}) for an unsupported service (e.g. exam e-PIN). ``reference``
    is sent as ``request_id`` (VTU.ng's idempotency key + requery handle).
    """
    sid = service_id.lower()
    body = {"request_id": reference}

    if sid.endswith("-airtime"):
        net = sid[: -len("-airtime")]
        return _EP_AIRTIME, {**body, "service_id": _VT_NETWORK.get(net, net),
                             "phone": payload.get("phone", ""),
                             "amount": _amount(payload.get("amount"))}
    if sid.endswith("-data"):
        net = sid[: -len("-data")]
        return _EP_DATA, {**body, "service_id": _VT_NETWORK.get(net, net),
                          "phone": payload.get("phone") or payload.get("billersCode", ""),
                          "variation_id": payload.get("variation_code", "")}
    if sid in _VT_CABLE:
        return _EP_TV, {**body, "service_id": _VT_CABLE[sid],
                        "customer_id": payload.get("billersCode", ""),
                        "variation_id": payload.get("variation_code", "")}
    if sid.endswith("-electric"):
        disco = sid[: -len("-electric")]
        return _EP_ELECTRIC, {**body, "service_id": _VT_DISCO.get(disco, disco),
                              "customer_id": payload.get("billersCode", ""),
                              "variation_id": (payload.get("variation_code") or "prepaid"),
                              "amount": _amount(payload.get("amount"))}
    if sid.endswith("-betting"):
        company = sid[: -len("-betting")]
        return _EP_BETTING, {**body, "service_id": company,
                             "customer_id": payload.get("billersCode", ""),
                             "amount": _amount(payload.get("amount"))}
    return None, {}   # exam e-PIN and anything else: unsupported on VTU.ng


def _amount(value):
    try:
        n = round(float(value), 2)
        return int(n) if n == int(n) else n
    except (TypeError, ValueError):
        return 0


def _parse(body: dict) -> dict:
    """Normalise a VTU.ng response to settle_or_refund's contract."""
    data = body.get("data") or {}
    status = str(data.get("status") or "").lower()
    code = str(body.get("code") or "").lower()
    order_id = str(data.get("order_id") or data.get("orderid") or "")
    token = (data.get("token") or data.get("meter_token") or data.get("metertoken")
             or data.get("recharge_token") or "")
    out = {
        "success": status in _VT_SUCCESS,
        "message": body.get("message") or data.get("status") or "Transaction processed",
        "provider_reference": order_id,
        "token": token,
        "raw": body,
    }
    if out["success"]:
        return out
    if status in _VT_PENDING or (code in ("processing", "pending") and status not in _VT_FAILED):
        out["pending"] = True
    elif status not in _VT_FAILED and code == "success" and not status:
        # Accepted (code=success) but no terminal status yet -> hold & requery.
        out["pending"] = True
    return out


def vt_purchase(service_id: str, payload: dict, reference: str | None = None) -> dict:
    """Submit a VTU.ng purchase. MOCK-succeeds when no creds are set.

    On a network error returns ``pending=True`` (the order may have landed — the
    caller must NOT refund; reconciliation requeries by request_id instead).
    """
    ref = reference or ("ZVT-" + secrets.token_hex(6).upper())
    if not _live():
        from .providers import mock_disabled_in_prod
        if mock_disabled_in_prod():
            return {"success": False, "message": "VTU.ng is not configured"}
        return {"success": True, "mock": True,
                "message": "Transaction Successful (mock mode — no VTU.ng keys set)",
                "provider_reference": "MOCK-" + secrets.token_hex(6).upper()}
    endpoint, json_body = _build(service_id, payload, ref)
    if endpoint is None:
        return {"success": False, "message": f"Unsupported service: {service_id}"}
    if not _token():
        # Configured (_live) but no usable token — the JWT login failed (wrong
        # VTUNG_USERNAME/PASSWORD, or the account's JWT auth isn't enabled). Don't
        # send a guaranteed-bad "Bearer " header (VTU.ng answers the cryptic
        # "Authorization header malformed"); fail clearly so the wallet refunds and
        # ops can see the cause (also logged in _login as vtung_login_no_token).
        log.warning("vtung_purchase_no_token service=%s", service_id)
        return {"success": False,
                "message": "Airtime provider sign-in failed — please try again shortly."}
    try:
        return _parse(_request("POST", endpoint, json_body=json_body))
    except requests.RequestException as exc:
        return {"success": False, "pending": True, "message": f"VTU.ng unreachable: {exc}"}
    except ValueError:
        return {"success": False, "pending": True, "message": "VTU.ng returned a non-JSON response"}


def vt_requery(reference: str) -> dict:
    """Requery a purchase by our request_id to settle a PENDING transaction.

    success => delivered; pending => still unknown (retry later); neither =>
    definitive failure (refund). An unrecognised/empty result is kept PENDING so
    a delivered order is never refunded by mistake. MOCK treats it as delivered.
    """
    if not _live():
        from .providers import mock_disabled_in_prod
        if mock_disabled_in_prod():
            return {"success": False, "pending": True, "message": "VTU.ng is not configured"}
        return {"success": True, "mock": True, "message": "Delivered (mock requery)"}
    try:
        parsed = _parse(_request("GET", _EP_REQUERY, params={"request_id": reference}))
    except (requests.RequestException, ValueError):
        return {"success": False, "pending": True, "message": "Requery failed; will retry"}
    if not parsed.get("success") and not parsed.get("provider_reference") and "pending" not in parsed:
        parsed["pending"] = True  # no confirmed status yet — don't refund
    return parsed


def vt_verify_customer(service_id: str, billers_code: str, variation: str = "") -> dict:
    """Validate a meter / smartcard number, returning the customer name."""
    if not _live():
        return {"success": True, "mock": True, "customer_name": "ADEYEMI WILLIAM"}
    sid = service_id.lower()
    if sid.endswith("-electric"):
        svc = _VT_DISCO.get(sid[: -len("-electric")], sid)
    else:
        svc = _VT_CABLE.get(sid, sid)
    body = {"customer_id": billers_code, "service_id": svc}
    if variation:
        body["variation_id"] = variation
    try:
        data = _request("POST", _EP_VERIFY, json_body=body)
    except (requests.RequestException, ValueError) as exc:
        return {"success": False, "message": f"VTU.ng unreachable: {exc}"}
    d = data.get("data") or {}
    name = (d.get("customer_name") or d.get("customerName") or d.get("name")
            or data.get("customer_name") or "")
    return {"success": bool(name), "customer_name": name, "raw": data}
