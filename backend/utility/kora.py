"""Kora (Korapay) integration — the money-movement rail for Zitch.

Covers the Kora features Zitch needs, each mirroring the conventions in
``utility.providers``:

- Funding (pay-in): hosted checkout + dedicated Virtual Bank Accounts.
- Payouts: single bank disbursement, name enquiry (bank resolve), verify.
- Balances: merchant balance read.
- Identity / KYC: BVN, NIN, vNIN lookups (the app's KYC backend; Prembly
  handles the selfie/liveness step only).
- Card issuing: cardholder + virtual card create / fund / withdraw / status.
- Webhook signature verification (HMAC-SHA256 over the payload ``data`` object).

Auth is a single static bearer **secret key** (no OAuth handshake),
so there is no token cache. Base URL is ``https://api.korapay.com/merchant`` and
every endpoint hangs off ``/api/v1/...``. Every function returns
``{"success": bool, ...}``.

MOCK mode: when ``KORA_SECRET_KEY`` is blank the calls simulate success so the
whole flow is testable offline — EXCEPT in production (DEBUG off), where money /
identity calls fail closed via ``providers.mock_disabled_in_prod`` so a
misconfigured deploy never fakes a money movement or an identity pass.

VERIFY-BEFORE-LIVE: endpoint paths and field names follow Kora's published API
(https://docs.korapay.com) but can't be exercised from CI — confirm each against
the dashboard before go-live. The MOCK paths are the source of truth until a real
key is configured.
"""
import hashlib
import hmac
import json
import logging
import secrets

import requests
from django.conf import settings

from .providers import mock_disabled_in_prod

REQUEST_TIMEOUT = 30
log = logging.getLogger("zitch")


def kora_live() -> bool:
    """Whether Kora has a secret key configured (live, non-mock)."""
    return bool(settings.KORA.get("SECRET_KEY"))


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.KORA['SECRET_KEY']}",
        "Content-Type": "application/json",
    }


def _url(path: str) -> str:
    # BASE_URL already includes the /merchant segment; paths start with /api/v1.
    return f"{settings.KORA['BASE_URL'].rstrip('/')}{path}"


def _ok(data: dict) -> bool:
    """Kora's envelope status, which may be a bool or the string "true"."""
    s = data.get("status")
    return s is True or str(s).lower() == "true"


def _post(path: str, body: dict) -> requests.Response:
    return requests.post(_url(path), json=body, headers=_headers(), timeout=REQUEST_TIMEOUT)


def _get(path: str, params: dict | None = None) -> requests.Response:
    return requests.get(_url(path), params=params or {}, headers=_headers(), timeout=REQUEST_TIMEOUT)


def _unreachable(kind: str, exc: Exception) -> dict:
    return {"success": False, "message": f"{kind} unreachable: {exc}"}


# ---------------------------------------------------------------------------
# Funding (pay-in) — hosted checkout
# ---------------------------------------------------------------------------
def payment_initialize(email: str, amount_naira, reference: str, *,
                       name: str = "", redirect_url: str = "",
                       notification_url: str = "") -> dict:
    """Start a funding charge; returns a hosted checkout URL the app opens.

    POST /api/v1/charges/initialize. MOCK returns a sentinel URL so funding is
    testable offline (the tester 'completes' it by calling verify).
    """
    if not kora_live():
        if mock_disabled_in_prod():
            return {"success": False, "message": "Kora payments are not configured"}
        return {"success": True, "mock": True, "reference": reference,
                "authorization_url": f"mock://kora/checkout/{reference}"}
    try:
        body = {
            "amount": float(amount_naira),
            "currency": "NGN",
            "reference": reference,
            "customer": {"email": email, "name": name or (email or "Zitch user").split("@")[0]},
        }
        if redirect_url:
            body["redirect_url"] = redirect_url
        if notification_url:
            body["notification_url"] = notification_url
        resp = _post("/api/v1/charges/initialize", body)
        data = resp.json()
        d = data.get("data", {}) or {}
        url = d.get("checkout_url", "")
        if not (_ok(data) and url):
            log.warning("kora_init_failed ref=%s msg=%s", reference, data.get("message"))
        return {
            "success": _ok(data) and bool(url),
            "reference": d.get("reference", reference),
            "authorization_url": url,
            "message": data.get("message") or "Could not start payment",
            "raw": data,
        }
    except requests.RequestException as exc:
        return _unreachable("Payment gateway", exc)


def payment_verify(reference: str) -> dict:
    """Confirm a charge with Kora (source of truth for crediting).

    GET /api/v1/charges/:reference. MOCK treats any reference as paid.
    """
    if not kora_live():
        if mock_disabled_in_prod():
            return {"success": False, "message": "Kora payments are not configured"}
        return {"success": True, "mock": True, "amount_naira": None, "reference": reference}
    try:
        resp = _get(f"/api/v1/charges/{reference}")
        data = resp.json()
        d = data.get("data", {}) or {}
        paid = _ok(data) and str(d.get("status", "")).lower() == "success"
        return {"success": paid, "amount_naira": d.get("amount"),
                "reference": d.get("reference", reference), "status": d.get("status", ""), "raw": data}
    except requests.RequestException as exc:
        return _unreachable("Payment gateway", exc)


# ---------------------------------------------------------------------------
# Dedicated Virtual Bank Accounts (funding by transfer)
# ---------------------------------------------------------------------------
def _parse_vba(data: dict) -> dict:
    d = data.get("data", {}) or {}
    acct = d.get("account_number", "") or (d.get("bank_account", {}) or {}).get("account_number", "")
    bank = d.get("bank_name", "") or (d.get("bank_account", {}) or {}).get("bank_name", "")
    # Kora issues a single dedicated account; surface it as a one-item `accounts`
    # list too, matching the shape the wallet/app expects (Wallet.bank_accounts).
    accounts = ([{"bank_name": bank, "account_number": acct,
                  "bank_code": d.get("bank_code", "")}] if acct else [])
    return {
        "success": _ok(data) and bool(acct),
        "account_number": acct,
        "bank_name": bank,
        "account_name": d.get("account_name", ""),
        "reference": d.get("account_reference", ""),
        "accounts": accounts,
        "message": data.get("message", "Could not create virtual account"),
        "raw": data,
    }


def create_virtual_account(account_reference: str, account_name: str, customer_email: str,
                           customer_name: str, bvn: str = "", nin: str = "",
                           permanent: bool = True) -> dict:
    """Provision a dedicated NUBAN that funds the wallet by bank transfer.

    POST /api/v1/virtual-bank-account. ``account_reference`` is our stable
    per-user key. Kora requires the customer's BVN (CBN compliance) for a
    permanent account. MOCK fabricates a deterministic NUBAN in dev/test only.
    """
    if not kora_live():
        if mock_disabled_in_prod():
            # Never fabricate an account in production — a user would transfer real
            # money to a number Kora never issued. Fail closed.
            return {"success": False, "message": "Virtual accounts are not configured"}
        seed = int(hashlib.sha256(account_reference.encode()).hexdigest(), 16)
        num = "88" + f"{seed % 10**8:08d}"
        return {"success": True, "mock": True,
                "account_number": num,
                "bank_name": "Kora (Wema) MFB", "account_name": account_name,
                "reference": account_reference,
                "accounts": [{"bank_name": "Kora (Wema) MFB", "account_number": num, "bank_code": "035"}]}
    try:
        body = {
            "account_name": account_name,
            "account_reference": account_reference,
            "permanent": bool(permanent),
            "customer": {"name": customer_name, "email": customer_email},
        }
        kyc = {}
        if bvn:
            kyc["bvn"] = bvn
        if nin:
            kyc["nin"] = nin
        if kyc:
            body["kyc"] = kyc
        out = _parse_vba(_post("/api/v1/virtual-bank-account", body).json())
        if not out["success"]:
            log.warning("kora_vba_failed ref=%s msg=%s", account_reference, out.get("message"))
        return out
    except requests.RequestException as exc:
        return _unreachable("Payment gateway", exc)


def get_virtual_account(account_reference: str) -> dict:
    """Fetch an existing virtual account by our reference.

    GET /api/v1/virtual-bank-account/:accountReference. Used to recover the
    details when a prior create succeeded at Kora but we failed to persist it.
    """
    if not kora_live():
        return {"success": False, "message": "mock"}
    try:
        return _parse_vba(_get(f"/api/v1/virtual-bank-account/{account_reference}").json())
    except requests.RequestException as exc:
        return _unreachable("Payment gateway", exc)


# ---------------------------------------------------------------------------
# Payouts / disbursements
# ---------------------------------------------------------------------------
def resolve_account(account_number: str, bank_code: str) -> dict:
    """Name enquiry: account number + bank code -> account holder name.

    POST /api/v1/misc/banks/resolve (requires the secret key — the published
    Postman sample's `noauth` is a bug). MOCK returns a stub name.
    """
    if not kora_live():
        return {"success": True, "mock": True, "name": "ADEYEMI WILLIAM"}
    try:
        resp = _post("/api/v1/misc/banks/resolve", {"bank": bank_code, "account": account_number})
        data = resp.json()
        d = data.get("data", {}) or {}
        name = d.get("account_name", "")
        ok_ = _ok(data) and bool(name)
        if not ok_:
            log.info("kora_resolve_miss bank=%s acct_tail=%s msg=%s",
                     bank_code, account_number[-4:], data.get("message"))
        return {"success": ok_, "name": name, "raw": data}
    except requests.RequestException as exc:
        return _unreachable("Payout provider", exc)


def disburse(amount_naira, reference: str, narration: str, bank_code: str,
             account_number: str, account_name: str, *,
             customer_email: str = "", customer_name: str = "") -> dict:
    """Single payout to a bank account.

    POST /api/v1/transactions/disburse. success/processing means Kora accepted
    it (sent or queued); anything else is treated as not-sent so the caller
    refunds the wallet. Draws from your Kora payout balance.
    """
    if not kora_live():
        return {"success": True, "mock": True, "status": "success"}
    try:
        body = {
            "reference": reference,
            "destination": {
                "type": "bank_account",
                "amount": str(amount_naira),
                "currency": "NGN",
                "narration": narration or "Zitch transfer",
                "bank_account": {"bank": bank_code, "account": account_number},
                "customer": {"name": customer_name or account_name, "email": customer_email},
            },
        }
        resp = _post("/api/v1/transactions/disburse", body)
        data = resp.json()
        d = data.get("data", {}) or {}
        status = str(d.get("status", "")).lower()
        return {
            "success": _ok(data) and status in ("success", "processing", "pending"),
            "status": status,
            "reference": d.get("reference", reference),
            "message": data.get("message", "Transfer not completed"),
            "raw": data,
        }
    except requests.RequestException as exc:
        return _unreachable("Payout provider", exc)


def verify_payout(reference: str) -> dict:
    """Verify a payout by our reference. GET /api/v1/transactions/:reference."""
    if not kora_live():
        return {"success": True, "mock": True, "status": "success"}
    try:
        resp = _get(f"/api/v1/transactions/{reference}")
        data = resp.json()
        d = data.get("data", {}) or {}
        status = str(d.get("status", "")).lower()
        if status == "success":
            return {"success": True, "status": status, "raw": data}
        if status in ("processing", "pending", ""):
            return {"success": False, "pending": True, "status": status or "pending", "raw": data}
        return {"success": False, "status": status, "raw": data}
    except requests.RequestException as exc:
        return _unreachable("Payout provider", exc)


def get_balances() -> dict:
    """Read merchant balances. GET /api/v1/balances."""
    if not kora_live():
        return {"success": True, "mock": True, "balances": {"NGN": {"available_balance": 0}}}
    try:
        resp = _get("/api/v1/balances")
        data = resp.json()
        return {"success": _ok(data), "balances": data.get("data", {}) or {}, "raw": data}
    except requests.RequestException as exc:
        return _unreachable("Payout provider", exc)


# ---------------------------------------------------------------------------
# Identity / KYC — BVN / NIN / vNIN (Prembly handles liveness only)
# ---------------------------------------------------------------------------
def _identity(country_doc: str, number: str, *, length: int = 11, label: str = "ID") -> dict:
    """Shared lookup for the /identities/{country}/{doc} endpoints.

    ``country_doc`` is e.g. "ng/bvn". MOCK accepts any value of the right length.
    """
    if length and (len(number) != length or not number.isdigit()):
        return {"success": False, "message": f"{label} must be {length} digits"}
    if not kora_live():
        if mock_disabled_in_prod():
            return {"success": False, "message": "Identity verification is temporarily unavailable"}
        return {"success": True, "mock": True, "first_name": "", "last_name": ""}
    try:
        resp = _post(f"/api/v1/identities/{country_doc}", {"id": number})
        data = resp.json()
        d = data.get("data", {}) or {}
        return {"success": _ok(data) and bool(d), "raw": data,
                "first_name": d.get("first_name", ""), "last_name": d.get("last_name", "")}
    except requests.RequestException as exc:
        return _unreachable("KYC provider", exc)


def verify_bvn(bvn: str, **_ignored) -> dict:
    """Verify a Nigerian BVN. POST /api/v1/identities/ng/bvn."""
    return _identity("ng/bvn", bvn, label="BVN")


def verify_nin(nin: str) -> dict:
    """Verify a Nigerian NIN. POST /api/v1/identities/ng/nin."""
    return _identity("ng/nin", nin, label="NIN")


def verify_vnin(vnin: str) -> dict:
    """Verify a Virtual NIN. POST /api/v1/identities/ng/vnin.

    vNIN is the 16-char tokenised NIN NIMC recommends over the raw NIN.
    """
    if not vnin or len(vnin) != 16:
        return {"success": False, "message": "Virtual NIN must be 16 characters"}
    return _identity("ng/vnin", vnin, length=0, label="Virtual NIN")


# ---------------------------------------------------------------------------
# Card issuing (virtual cards)
# ---------------------------------------------------------------------------
def create_cardholder(name: str, email: str, phone: str = "") -> dict:
    """Create a cardholder (the KYC'd identity a card is issued to).

    POST /api/v1/cardholders. Returns the cardholder ``reference``.
    """
    if not kora_live():
        if mock_disabled_in_prod():
            return {"success": False, "message": "Card issuing is not configured"}
        return {"success": True, "mock": True, "reference": "chr_mock_" + secrets.token_hex(6)}
    try:
        resp = _post("/api/v1/cardholders", {"name": name, "email": email, "phone_number": phone})
        data = resp.json()
        d = data.get("data", {}) or {}
        ref = d.get("reference") or d.get("id", "")
        return {"success": _ok(data) and bool(ref), "reference": ref, "raw": data}
    except requests.RequestException as exc:
        return _unreachable("Card issuer", exc)


def create_card(cardholder_reference: str, currency: str = "NGN") -> dict:
    """Create a virtual card for a cardholder. POST /api/v1/cards."""
    if not kora_live():
        if mock_disabled_in_prod():
            return {"success": False, "message": "Card issuing is not configured"}
        return {"success": True, "mock": True, "card_token": "card_mock_" + secrets.token_hex(8),
                "brand": "Verve", "last4": f"{secrets.randbelow(10000):04d}",
                "expiry": f"{1 + secrets.randbelow(12):02d}/{29 + secrets.randbelow(3)}"}
    try:
        resp = _post("/api/v1/cards", {"cardholder_reference": cardholder_reference,
                                       "type": "virtual", "currency": currency})
        data = resp.json()
        d = data.get("data", {}) or {}
        ref = d.get("reference") or d.get("id", "")
        return {
            "success": _ok(data) and bool(ref),
            "card_token": ref,
            "brand": d.get("brand", "Verve"),
            "last4": (d.get("masked_pan") or d.get("last_four") or "")[-4:],
            "expiry": f"{d.get('expiry_month', '')}/{str(d.get('expiry_year', ''))[-2:]}",
            "raw": data,
        }
    except requests.RequestException as exc:
        return _unreachable("Card issuer", exc)


def card_details(card_reference: str) -> dict:
    """Fetch a card's details. GET /api/v1/cards/:reference."""
    if not kora_live():
        return {"success": False, "message": "mock"}
    try:
        resp = _get(f"/api/v1/cards/{card_reference}")
        data = resp.json()
        return {"success": _ok(data), "data": data.get("data", {}) or {}, "raw": data}
    except requests.RequestException as exc:
        return _unreachable("Card issuer", exc)


def fund_card(card_reference: str, amount) -> dict:
    """Top up an issued card. POST /api/v1/cards/:reference/fund."""
    if not kora_live():
        if mock_disabled_in_prod():
            return {"success": False, "message": "Card issuing is not configured"}
        return {"success": True, "mock": True}
    try:
        resp = _post(f"/api/v1/cards/{card_reference}/fund", {"amount": str(amount), "currency": "NGN"})
        data = resp.json()
        return {"success": _ok(data), "raw": data}
    except requests.RequestException as exc:
        return _unreachable("Card issuer", exc)


def withdraw_from_card(card_reference: str, amount) -> dict:
    """Pull funds back off a card. POST /api/v1/cards/:reference/withdraw."""
    if not kora_live():
        if mock_disabled_in_prod():
            return {"success": False, "message": "Card issuing is not configured"}
        return {"success": True, "mock": True}
    try:
        resp = _post(f"/api/v1/cards/{card_reference}/withdraw", {"amount": str(amount), "currency": "NGN"})
        data = resp.json()
        return {"success": _ok(data), "raw": data}
    except requests.RequestException as exc:
        return _unreachable("Card issuer", exc)


def set_card_status(card_reference: str, active: bool) -> dict:
    """Freeze/unfreeze a card. PATCH /api/v1/cards/:reference/status."""
    if not kora_live():
        if mock_disabled_in_prod():
            return {"success": False, "message": "Card issuing is not configured"}
        return {"success": True, "mock": True}
    try:
        resp = requests.patch(
            _url(f"/api/v1/cards/{card_reference}/status"),
            json={"status": "active" if active else "inactive"},
            headers=_headers(), timeout=REQUEST_TIMEOUT,
        )
        return {"success": resp.ok and _ok(resp.json()), "raw": (resp.json() if resp.content else {})}
    except requests.RequestException as exc:
        return _unreachable("Card issuer", exc)


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------
def verify_webhook(payload: dict, signature: str) -> bool:
    """Validate a Kora webhook via the ``x-korapay-signature`` header.

    Kora signs ONLY the
    ``data`` object of the payload: HMAC-SHA256 of the JSON-encoded ``data``
    with the secret key. Pass the parsed payload dict and the header value.

    Fails closed in production when no key is set (an unsigned/forged callback
    moves money — credits a wallet on funding, reverses a payout on failure).
    Dev/test accept so local webhook testing works.
    """
    if not kora_live():
        return not mock_disabled_in_prod()
    if not signature:
        return False
    data_obj = payload.get("data", {}) if isinstance(payload, dict) else {}
    encoded = json.dumps(data_obj, separators=(",", ":")).encode()
    digest = hmac.new(settings.KORA["SECRET_KEY"].encode(), encoded, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature)


# ---------------------------------------------------------------------------
# Diagnostics — Kora connectivity self-test
# ---------------------------------------------------------------------------
def kora_diagnostics(account: str = "0000000000", bank: str = "058") -> dict:
    """Structured Kora connectivity self-test (no secrets).

    Reports config booleans + base URL, whether a balances read authenticates,
    and what a sample name-enquiry returns — each step short-circuiting with a
    ``status`` + ``hint`` that names the fix.
    """
    k = settings.KORA
    out = {
        "base_url": k["BASE_URL"],
        "secret_key_set": bool(k.get("SECRET_KEY")),
        "public_key_set": bool(k.get("PUBLIC_KEY")),
        "kora_live": kora_live(),
    }
    if not kora_live():
        out["status"] = "keys_incomplete"
        out["hint"] = ("Set KORA_SECRET_KEY (use sk_test_… while developing). "
                       "Until it's set, every Kora feature runs in mock mode.")
        return out
    bal = get_balances()
    out["auth_ok"] = bool(bal.get("success"))
    if not out["auth_ok"]:
        out["status"] = "auth_failed"
        out["hint"] = ("Balances read failed — the secret key is invalid, or you're using a "
                       "test key against the live base URL (or vice-versa). Both share "
                       "https://api.korapay.com/merchant; the key prefix (sk_test_/sk_live_) "
                       "selects the mode.")
        return out
    res = resolve_account(account, bank)
    out["sample_enquiry"] = {"account": account, "bank_code": bank,
                             "resolved": bool(res.get("success")), "name": res.get("name", ""),
                             "message": (res.get("raw") or {}).get("message") or res.get("message", "")}
    out["status"] = "ok" if res.get("success") else "auth_ok_enquiry_failed"
    out["hint"] = ("Auth + name-enquiry both work." if res.get("success")
                   else "Auth works but name-enquiry failed — check the test account/bank code, "
                        "or that the Payout product is enabled on your account.")
    return out
