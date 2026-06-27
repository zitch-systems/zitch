"""Mono (open banking) integration — link an external bank to the Zitch wallet.

Covers what the banklink app needs, mirroring the conventions in utility.kora:

- Account linking: exchange the Mono Connect auth code for a permanent account id.
- Account data: details, balance, transactions of a linked account.
- DirectPay funding: pull money from a linked bank into the Zitch wallet.
- Webhook verification (shared-secret header).

Auth is a single static secret key sent as the ``mono-sec-key`` header (no OAuth
handshake); the Connect widget itself runs client-side with the PUBLIC_KEY. Base
URL is ``https://api.withmono.com``. Every function returns ``{"success": bool, ...}``.

Amounts: Mono works in KOBO; helpers convert to/from naira at the boundary.

MOCK mode: when ``MONO_SECRET_KEY`` is blank the calls simulate success so the
flow is testable offline — EXCEPT in production (DEBUG off), where money /
account-linking calls fail closed via ``providers.mock_disabled_in_prod`` so a
misconfigured deploy never fakes a link or a funding.

VERIFY-BEFORE-LIVE: endpoint paths and field names follow Mono's published API
(https://docs.mono.co) but can't be exercised from CI — confirm each against the
dashboard before go-live. The MOCK paths are the source of truth until a real key
is configured.
"""
import hashlib
import hmac
import logging
from decimal import Decimal

import requests
from django.conf import settings

from .providers import mock_disabled_in_prod

REQUEST_TIMEOUT = 30
log = logging.getLogger("zitch")


def mono_live() -> bool:
    """Whether Mono has a secret key configured (live, non-mock)."""
    return bool(settings.MONO.get("SECRET_KEY"))


def _headers() -> dict:
    return {"mono-sec-key": settings.MONO["SECRET_KEY"], "Content-Type": "application/json"}


def _url(path: str) -> str:
    return f"{settings.MONO['BASE_URL'].rstrip('/')}{path}"


def _ok(data: dict) -> bool:
    """Mono's envelope status — "successful" (v1/v2) or a truthy boolean."""
    s = data.get("status")
    return s is True or str(s).lower() in ("successful", "success", "true") or bool(data.get("data"))


def _get(path: str, params: dict | None = None) -> requests.Response:
    return requests.get(_url(path), params=params or {}, headers=_headers(), timeout=REQUEST_TIMEOUT)


def _post(path: str, body: dict) -> requests.Response:
    return requests.post(_url(path), json=body, headers=_headers(), timeout=REQUEST_TIMEOUT)


def _unreachable(exc: Exception) -> dict:
    return {"success": False, "message": f"Bank link provider unreachable: {exc}"}


def _naira(kobo) -> Decimal | None:
    try:
        return (Decimal(str(kobo)) / 100).quantize(Decimal("0.01"))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Account linking
# ---------------------------------------------------------------------------
def exchange_token(code: str) -> dict:
    """Exchange a Mono Connect auth code for a permanent account id.

    POST /v2/accounts/auth {code} -> {id}. MOCK returns a deterministic id so the
    link flow is testable offline.
    """
    if not mono_live():
        if mock_disabled_in_prod():
            return {"success": False, "message": "Bank linking is not configured"}
        seed = hashlib.sha256((code or "x").encode()).hexdigest()[:16]
        return {"success": True, "mock": True, "account_id": f"mock_acct_{seed}"}
    try:
        resp = _post("/v2/accounts/auth", {"code": code})
        data = resp.json()
        d = data.get("data", {}) or {}
        acct_id = d.get("id") or data.get("id", "")
        if not (_ok(data) and acct_id):
            log.warning("mono_exchange_failed msg=%s", data.get("message"))
        return {"success": _ok(data) and bool(acct_id), "account_id": acct_id,
                "message": data.get("message", "Could not link account"), "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


def _parse_account(d: dict) -> dict:
    acct = d.get("account", d) or {}
    inst = acct.get("institution", {}) or {}
    number = acct.get("accountNumber", "") or acct.get("account_number", "")
    return {
        "account_id": acct.get("id", "") or d.get("id", ""),
        "bank_name": inst.get("name", "") or acct.get("bank_name", ""),
        "account_number": number,
        "account_name": acct.get("name", ""),
        "balance_naira": _naira(acct.get("balance")),
        "currency": acct.get("currency", "NGN"),
        "data_status": d.get("meta", {}).get("data_status", "") if isinstance(d.get("meta"), dict) else "",
    }


def get_account(account_id: str) -> dict:
    """Fetch a linked account's details. GET /v2/accounts/{id}."""
    if not mono_live():
        if mock_disabled_in_prod():
            return {"success": False, "message": "Bank linking is not configured"}
        return {"success": True, "mock": True, "account_id": account_id,
                "bank_name": "GTBank (mock)", "account_number": "0123456789",
                "account_name": "ADA EZE", "balance_naira": Decimal("84200.10"), "currency": "NGN"}
    try:
        data = _get(f"/v2/accounts/{account_id}").json()
        out = _parse_account(data.get("data", {}) or {})
        out["success"] = _ok(data) and bool(out["account_number"])
        out["raw"] = data
        return out
    except requests.RequestException as exc:
        return _unreachable(exc)


def get_balance(account_id: str) -> dict:
    """Fetch a linked account's balance. GET /v2/accounts/{id}/balance."""
    if not mono_live():
        if mock_disabled_in_prod():
            return {"success": False, "message": "Bank linking is not configured"}
        return {"success": True, "mock": True, "balance_naira": Decimal("84200.10")}
    try:
        data = _get(f"/v2/accounts/{account_id}/balance").json()
        d = data.get("data", {}) or {}
        return {"success": _ok(data), "balance_naira": _naira(d.get("balance")), "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


def get_transactions(account_id: str, page: int = 1) -> dict:
    """Fetch a linked account's transactions. GET /v2/accounts/{id}/transactions."""
    if not mono_live():
        return {"success": True, "mock": True, "transactions": []}
    try:
        data = _get(f"/v2/accounts/{account_id}/transactions", {"page": page}).json()
        return {"success": _ok(data), "transactions": data.get("data", []) or [], "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


# ---------------------------------------------------------------------------
# DirectPay — fund the Zitch wallet from a linked bank
# ---------------------------------------------------------------------------
def initiate_directpay(amount_naira, reference: str, *, email: str = "", name: str = "",
                       redirect_url: str = "") -> dict:
    """Start a DirectPay debit to fund the wallet.

    POST /v1/payments/initiate -> {authorization_url, reference}. Amount is sent in
    kobo. MOCK returns a sentinel URL so funding is testable offline.
    """
    if not mono_live():
        if mock_disabled_in_prod():
            return {"success": False, "message": "Bank funding is not configured"}
        return {"success": True, "mock": True, "reference": reference,
                "authorization_url": f"mock://mono/directpay/{reference}"}
    try:
        body = {
            "amount": int(Decimal(str(amount_naira)) * 100),  # kobo
            "type": "onetime-debit",
            "description": "Zitch wallet funding",
            "reference": reference,
            "redirect_url": redirect_url,
            "customer": {"email": email, "name": name or (email or "Zitch user").split("@")[0]},
        }
        data = _post("/v1/payments/initiate", body).json()
        d = data.get("data", {}) or {}
        url = d.get("mono_url", "") or d.get("payment_link", "")
        if not (_ok(data) and url):
            log.warning("mono_directpay_failed ref=%s msg=%s", reference, data.get("message"))
        return {"success": _ok(data) and bool(url), "authorization_url": url,
                "reference": d.get("reference", reference),
                "message": data.get("message", "Could not start bank funding"), "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------
def verify_webhook(payload: dict, signature: str) -> bool:
    """Validate a Mono webhook via the ``mono-webhook-secret`` header.

    Mono signs webhooks with a shared secret you set in the dashboard, sent as the
    header value; we constant-time compare it to MONO['WEBHOOK_SECRET']. Fails
    closed in production when no secret is set (an unsigned callback could credit a
    wallet on a funding event); dev/test accept so local webhook testing works.
    """
    secret = settings.MONO.get("WEBHOOK_SECRET", "")
    if not secret:
        return not mock_disabled_in_prod()
    return hmac.compare_digest(str(signature or ""), secret)


# ---------------------------------------------------------------------------
# Diagnostics — mirrors providers.kora_diagnostics
# ---------------------------------------------------------------------------
def mono_diagnostics() -> dict:
    """Structured Mono connectivity self-test (no secrets)."""
    m = settings.MONO
    out = {"base_url": m["BASE_URL"], "secret_key_set": bool(m.get("SECRET_KEY")),
           "public_key_set": bool(m.get("PUBLIC_KEY")),
           "webhook_secret_set": bool(m.get("WEBHOOK_SECRET")), "mono_live": mono_live()}
    if not mono_live():
        out["status"] = "keys_incomplete"
        out["hint"] = "Set MONO_SECRET_KEY (test key first). Until then bank linking runs in mock mode."
        return out
    out["status"] = "configured"
    out["hint"] = ("Keys present. Verify exchange/balance/DirectPay/webhook field names against the "
                   "Mono dashboard before disabling mock.")
    return out
