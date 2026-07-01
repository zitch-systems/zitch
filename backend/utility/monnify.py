"""Monnify (fund-in) integration — dedicated virtual accounts + hosted checkout.

Scope: WALLET FUNDING only. Monnify mints a permanent NUBAN per user that funds
the wallet by bank transfer (no IP whitelisting needed for collections), and can
also open a hosted checkout page. Bank PAYOUTS and recipient NAME ENQUIRY stay on
Kora (see utility.kora) — this module deliberately has no disbursement path.

Auth: OAuth login — Basic base64(apiKey:secretKey) -> bearer access token (~1h),
cached. Base URL: live https://api.monnify.com, sandbox https://sandbox.monnify.com.
Amounts are in NAIRA (not kobo). Every function returns {"success": bool, ...}.

MOCK mode: when the Monnify keys are blank the calls simulate success so the flow
is testable offline — EXCEPT in production (DEBUG off), where account/money calls
fail closed via providers.mock_disabled_in_prod so a misconfigured deploy never
fabricates a dead NUBAN. MONNIFY_SIMULATION=true serves the mock flow even in
production (to demo/test a real build without live keys) — no real money moves.

Inbound funding arrives as a SUCCESSFUL_TRANSACTION webhook (monnify-signature =
HMAC-SHA512 of the raw body). It's credited idempotently by
wallet.views.monnify_fund_webhook -> settle_reserved_funding / settle_funding.

VERIFY-BEFORE-LIVE: endpoint paths/fields follow Monnify's published API but can't
be exercised from CI — confirm against the dashboard before go-live.
"""
import base64
import hashlib
import hmac
import logging
import secrets

import requests
from django.conf import settings
from django.core.cache import cache

from .providers import mock_disabled_in_prod

REQUEST_TIMEOUT = 30
_AUTH_TIMEOUT = 12  # interactive calls must not hang on a slow auth leg
log = logging.getLogger("zitch")


def monnify_live() -> bool:
    """Whether Monnify has full fund-in credentials configured (live, non-mock)."""
    m = settings.MONNIFY
    return bool(m.get("API_KEY") and m.get("SECRET_KEY") and m.get("CONTRACT_CODE"))


def monnify_simulation() -> bool:
    """Whether MONNIFY_SIMULATION is on — serve the mock fund-in flow even in
    production so a real build can test funding without live keys. No real money
    moves. Off by default."""
    return bool(settings.MONNIFY.get("SIMULATION"))


def _mock_blocked() -> bool:
    """True when a mock response must NOT be served: production, and simulation is
    not explicitly enabled."""
    return mock_disabled_in_prod() and not monnify_simulation()


def _unreachable(exc: Exception) -> dict:
    return {"success": False, "message": f"Payment gateway unreachable: {exc}"}


def _monnify_token() -> str:
    """OAuth login (Basic base64(apiKey:secretKey) -> bearer access token), cached
    until shortly before it expires. Returns "" on failure — callers guard on an
    empty token and degrade gracefully instead of 500-ing."""
    m = settings.MONNIFY
    ckey = "monnify:tok:" + hashlib.sha256(m["API_KEY"].encode()).hexdigest()[:16]
    cached = cache.get(ckey)
    if cached:
        return cached
    basic = base64.b64encode(f"{m['API_KEY']}:{m['SECRET_KEY']}".encode()).decode()
    try:
        resp = requests.post(
            f"{m['BASE_URL']}/api/v1/auth/login",
            headers={"Authorization": f"Basic {basic}"},
            timeout=_AUTH_TIMEOUT,
        )
        rb = (resp.json() or {}).get("responseBody", {}) or {}
    except (requests.RequestException, ValueError):
        log.warning("monnify_auth_unreachable base=%s", m["BASE_URL"])
        return ""
    token = rb.get("accessToken", "")
    if token:
        ttl = max(60, int(rb.get("expiresIn", 3000) or 3000) - 120)
        cache.set(ckey, token, ttl)
    else:
        log.warning("monnify_auth_no_token base=%s", m["BASE_URL"])
    return token


def _auth_headers() -> dict | None:
    token = _monnify_token()
    if not token:
        return None
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Hosted checkout (card / bank) — fund the wallet without a dedicated account
# ---------------------------------------------------------------------------
def payment_initialize(email: str, amount_naira, reference: str, *,
                       name: str = "", redirect_url: str = "") -> dict:
    """Start a funding transaction; returns a checkout URL the app opens.

    POST /api/v1/merchant/transactions/init-transaction -> {checkoutUrl}. MOCK
    returns a sentinel URL so funding is testable offline (the tester 'completes'
    it via the verify endpoint).
    """
    if not monnify_live():
        if _mock_blocked():
            return {"success": False, "message": "Payment gateway is not configured"}
        return {"success": True, "mock": True, "reference": reference,
                "authorization_url": f"mock://monnify/checkout/{reference}"}
    headers = _auth_headers()
    if headers is None:
        return {"success": False, "message": (
            "Payment gateway authentication failed — verify MONNIFY_API_KEY/SECRET_KEY and that "
            "MONNIFY_BASE_URL matches them (live: https://api.monnify.com, test: "
            "https://sandbox.monnify.com).")}
    m = settings.MONNIFY
    try:
        resp = requests.post(
            f"{m['BASE_URL']}/api/v1/merchant/transactions/init-transaction",
            json={
                "amount": float(amount_naira),  # Monnify amounts are in naira
                "customerName": name or (email or "Zitch user").split("@")[0],
                "customerEmail": email,
                "paymentReference": reference,
                "contractCode": m["CONTRACT_CODE"],
                "currencyCode": "NGN",
                "redirectUrl": redirect_url or m.get("REDIRECT_URL", ""),
                "paymentMethods": ["CARD", "ACCOUNT_TRANSFER"],
            },
            headers=headers, timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        rb = data.get("responseBody", {}) or {}
        success = bool(data.get("requestSuccessful")) and bool(rb.get("checkoutUrl"))
        if not success:
            log.warning("monnify_init_failed ref=%s code=%s msg=%s",
                        reference, data.get("responseCode"), data.get("responseMessage"))
        return {"success": success, "reference": rb.get("paymentReference", reference),
                "authorization_url": rb.get("checkoutUrl", ""),
                "message": data.get("responseMessage") or "Could not start payment", "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


def payment_verify(reference: str) -> dict:
    """Confirm a transaction with Monnify (source of truth for crediting).

    GET /api/v1/merchant/transactions/query -> paymentStatus PAID. MOCK treats any
    reference as paid so funding works without real money.
    """
    if not monnify_live():
        if _mock_blocked():
            return {"success": False, "message": "Payment gateway is not configured"}
        return {"success": True, "mock": True, "amount_naira": None, "reference": reference}
    headers = _auth_headers()
    if headers is None:
        return {"success": False, "message": "Monnify authentication failed"}
    m = settings.MONNIFY
    try:
        resp = requests.get(
            f"{m['BASE_URL']}/api/v1/merchant/transactions/query",
            params={"paymentReference": reference}, headers=headers, timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        rb = data.get("responseBody", {}) or {}
        paid = bool(data.get("requestSuccessful")) and rb.get("paymentStatus") == "PAID"
        return {"success": paid, "amount_naira": rb.get("amountPaid"),
                "reference": rb.get("paymentReference", reference), "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


# ---------------------------------------------------------------------------
# Reserved (dedicated) virtual accounts — fund by bank transfer
# ---------------------------------------------------------------------------
def _parse_reserved(data: dict) -> dict:
    """Normalise a reserve/get-reserved-account response into our shape (flat
    primary account + the full list of issued bank accounts)."""
    rb = data.get("responseBody", {}) or {}
    accounts = [
        {"bank_name": a.get("bankName", ""), "account_number": a.get("accountNumber", ""),
         "bank_code": a.get("bankCode", "")}
        for a in (rb.get("accounts") or []) if a.get("accountNumber")
    ]
    primary_num = accounts[0]["account_number"] if accounts else rb.get("accountNumber", "")
    primary_bank = accounts[0]["bank_name"] if accounts else rb.get("bankName", "")
    return {
        "success": bool(data.get("requestSuccessful")) and bool(primary_num),
        "account_number": primary_num,
        "bank_name": primary_bank,
        "account_name": rb.get("accountName", ""),
        "reference": rb.get("accountReference", ""),
        "reservation_reference": rb.get("reservationReference", ""),
        "accounts": accounts,
        "message": data.get("responseMessage", "Could not reserve account"),
        "raw": data,
    }


def create_virtual_account(account_reference: str, account_name: str, customer_email: str,
                           customer_name: str, bvn: str = "", nin: str = "") -> dict:
    """Reserve a dedicated virtual account for a customer (fund-in by transfer).

    POST /api/v2/bank-transfer/reserved-accounts. ``account_reference`` is our
    stable per-user key (Monnify rejects a duplicate — reuse it). Monnify requires
    a BVN/NIN (CBN) for a dedicated account. MOCK fabricates a deterministic NUBAN
    so the flow is testable; production without keys fails closed (never fabricates
    a NUBAN a user could send real money to). Signature matches
    providers.funding_account_reserve / kora.create_virtual_account.
    """
    if not monnify_live():
        if _mock_blocked():
            return {"success": False, "message": "Reserved accounts are not configured"}
        seed = int(hashlib.sha256(account_reference.encode()).hexdigest(), 16)
        num = "99" + f"{seed % 10**8:08d}"
        return {"success": True, "mock": True, "account_number": num,
                "bank_name": "Moniepoint MFB (demo)", "account_name": account_name,
                "reference": account_reference,
                "reservation_reference": "MOCK-" + secrets.token_hex(6).upper(),
                "accounts": [{"bank_name": "Moniepoint MFB (demo)", "account_number": num,
                              "bank_code": "50515"}]}
    headers = _auth_headers()
    if headers is None:
        return {"success": False, "message": "Monnify authentication failed"}
    m = settings.MONNIFY
    body = {
        "accountReference": account_reference,
        "accountName": account_name,
        "currencyCode": "NGN",
        "contractCode": m["CONTRACT_CODE"],
        "customerEmail": customer_email,
        "customerName": customer_name,
        "getAllAvailableBanks": True,
    }
    if bvn:
        body["bvn"] = bvn
    if nin:
        body["nin"] = nin
    try:
        resp = requests.post(
            f"{m['BASE_URL']}/api/v2/bank-transfer/reserved-accounts",
            json=body, headers=headers, timeout=REQUEST_TIMEOUT,
        )
        out = _parse_reserved(resp.json())
        if not out["success"]:
            log.warning("monnify_reserve_failed ref=%s msg=%s", account_reference, out.get("message"))
        return out
    except requests.RequestException as exc:
        return _unreachable(exc)


def get_virtual_account(account_reference: str) -> dict:
    """Fetch an existing reserved account by our accountReference (duplicate
    recovery). GET /api/v2/bank-transfer/reserved-accounts/{accountReference}."""
    if not monnify_live():
        if _mock_blocked():
            return {"success": False, "message": "Reserved accounts are not configured"}
        return {"success": False, "message": "mock: no stored account"}
    headers = _auth_headers()
    if headers is None:
        return {"success": False, "message": "Monnify authentication failed"}
    m = settings.MONNIFY
    try:
        resp = requests.get(
            f"{m['BASE_URL']}/api/v2/bank-transfer/reserved-accounts/{account_reference}",
            headers=headers, timeout=REQUEST_TIMEOUT,
        )
        return _parse_reserved(resp.json())
    except requests.RequestException as exc:
        return _unreachable(exc)


# ---------------------------------------------------------------------------
# KYC — BVN details-match + NIN lookup (Monnify VAS)
#
# The production KYC rail: Monnify validates the BVN against the holder's name /
# DOB / phone (details-match) and looks up NIN records, keeping identity on the
# same provider that mints the funding account. vNIN stays on Kora (Monnify has
# no vNIN product). Mock when unkeyed; fails closed in prod via _mock_blocked.
# ---------------------------------------------------------------------------
def verify_bvn(bvn: str, name: str = "", date_of_birth: str = "", mobile: str = "") -> dict:
    """Verify a BVN via Monnify's details-match (POST /api/v1/vas/bvn-details-match).

    Monnify doesn't return the holder's data — it MATCHES what we supply, so pass
    the user's name (and phone/DOB when available). Success requires the request
    to succeed AND the name (when supplied) not to be a NO_MATCH."""
    if len(bvn) != 11 or not bvn.isdigit():
        return {"success": False, "message": "BVN must be 11 digits"}
    if not monnify_live():
        # Identity NEVER mock-passes in production — deliberately stricter than
        # _mock_blocked(): MONNIFY_SIMULATION covers the fund-in demo only, and a
        # simulated KYC pass would upgrade a real tier on a fabricated identity.
        if mock_disabled_in_prod():
            return {"success": False, "message": "Identity verification is temporarily unavailable"}
        return {"success": True, "mock": True}
    headers = _auth_headers()
    if headers is None:
        return {"success": False, "message": "Monnify authentication failed"}
    m = settings.MONNIFY
    body = {"bvn": bvn, "name": name, "dateOfBirth": date_of_birth, "mobileNo": mobile}
    try:
        resp = requests.post(f"{m['BASE_URL']}/api/v1/vas/bvn-details-match",
                             json=body, headers=headers, timeout=REQUEST_TIMEOUT)
        data = resp.json()
        rb = data.get("responseBody", {}) or {}
        ok = bool(data.get("requestSuccessful"))
        name_match = ((rb.get("name") or {}).get("matchStatus") or "").upper()
        if ok and name and name_match == "NO_MATCH":
            return {"success": False, "message": "This BVN does not match your name", "raw": data}
        return {"success": ok, "match": name_match, "raw": data,
                "message": data.get("responseMessage", "")}
    except requests.RequestException as exc:
        return _unreachable(exc)


def verify_nin(nin: str) -> dict:
    """Verify a NIN via Monnify (POST /api/v1/vas/nin-details) -> holder details."""
    if len(nin) != 11 or not nin.isdigit():
        return {"success": False, "message": "NIN must be 11 digits"}
    if not monnify_live():
        # Same fail-closed rule as verify_bvn: simulation never mocks identity.
        if mock_disabled_in_prod():
            return {"success": False, "message": "Identity verification is temporarily unavailable"}
        return {"success": True, "mock": True}
    headers = _auth_headers()
    if headers is None:
        return {"success": False, "message": "Monnify authentication failed"}
    m = settings.MONNIFY
    try:
        resp = requests.post(f"{m['BASE_URL']}/api/v1/vas/nin-details",
                             json={"nin": nin}, headers=headers, timeout=REQUEST_TIMEOUT)
        data = resp.json()
        return {"success": bool(data.get("requestSuccessful")), "raw": data,
                "message": data.get("responseMessage", "")}
    except requests.RequestException as exc:
        return _unreachable(exc)


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------
def verify_webhook(body: bytes, signature: str) -> bool:
    """Validate a Monnify webhook: SHA-512 HMAC of the RAW body with the secret
    key, compared to the ``monnify-signature`` header.

    Fails closed in production when unkeyed (an unsigned callback credits a
    wallet); dev/test (or MONNIFY_SIMULATION) accept so local webhook testing works.
    """
    if not monnify_live():
        return not _mock_blocked()
    if not signature:
        return False
    digest = hmac.new(settings.MONNIFY["SECRET_KEY"].encode(), body or b"", hashlib.sha512).hexdigest()
    return hmac.compare_digest(digest, str(signature or ""))


# ---------------------------------------------------------------------------
# Diagnostics — mirrors kora_diagnostics / mono_diagnostics
# ---------------------------------------------------------------------------
def monnify_probe(bvn: str = "", name: str = "", email: str = "") -> dict:
    """Live self-test against the configured Monnify gateway (returns NO secrets).

    Proves, in order: (1) OAuth login works (keys valid), (2) the reserved-account
    product is accessible, and — optionally, when ?bvn=&name= are supplied —
    (3) Monnify can actually MINT a NUBAN end-to-end (creates a real reserved
    account under a ZITCH-DIAG-* reference; deallocate it in the Monnify
    dashboard afterwards). Also restates the webhook URL funding depends on.
    """
    import secrets as _secrets

    out = {"config": monnify_diagnostics(),
           "webhook_reminder": "Funding credits arrive via the webhook — set "
                               "https://<your-api-host>/api/fund/monnify/webhook/ in the "
                               "Monnify dashboard (Settings > Webhooks) or deposits will NOT credit."}
    if not monnify_live():
        out["hint"] = "Monnify keys incomplete — no live call was made."
        return out

    # 1) Auth: OAuth login (Basic -> bearer). Empty token == bad keys/base URL.
    token_ok = bool(_monnify_token())
    out["auth"] = {"ok": token_ok}
    if not token_ok:
        out["auth"]["hint"] = ("Login failed — check MONNIFY_API_KEY/MONNIFY_SECRET_KEY and that "
                               "MONNIFY_BASE_URL matches the key type (live https://api.monnify.com, "
                               "sandbox https://sandbox.monnify.com).")
        return out

    # 2) Reserved-account product access: fetch a reference that can't exist. A clean
    #    Monnify 'not found' proves the product responds; 401/403 or 'not enabled'
    #    surfaces in the message.
    lookup = get_virtual_account("ZITCH-DIAG-PROBE")
    out["reserved_product"] = {"reachable": True,
                               "message": lookup.get("message", "") or "responded"}

    # 3) Optional REAL mint: prove NUBAN creation end-to-end.
    if bvn and name:
        ref = f"ZITCH-DIAG-{_secrets.token_hex(4).upper()}"
        created = create_virtual_account(ref, name, email or "diag@zitch.ng", name, bvn=bvn)
        out["nuban_create"] = {"ok": created.get("success"),
                               "account_number": created.get("account_number", ""),
                               "bank_name": created.get("bank_name", ""),
                               "reference": ref if created.get("success") else "",
                               "message": created.get("message", ""),
                               "note": "Real reserved account created for diagnosis — deallocate "
                                       "it in the Monnify dashboard when done."}
    return out


def monnify_diagnostics() -> dict:
    """Structured Monnify connectivity self-test (no secrets)."""
    m = settings.MONNIFY
    out = {"base_url": m["BASE_URL"], "api_key_set": bool(m.get("API_KEY")),
           "secret_key_set": bool(m.get("SECRET_KEY")), "contract_code_set": bool(m.get("CONTRACT_CODE")),
           "monnify_live": monnify_live(), "simulation": monnify_simulation()}
    if not monnify_live():
        if monnify_simulation():
            out["status"] = "simulation"
            out["hint"] = ("MONNIFY_SIMULATION is ON — the mock fund-in flow is served even in "
                           "production. No real money moves. Set the Monnify keys and turn simulation "
                           "off to go live.")
            return out
        out["status"] = "keys_incomplete"
        out["hint"] = ("Set MONNIFY_API_KEY, MONNIFY_SECRET_KEY and MONNIFY_CONTRACT_CODE (sandbox "
                       "first), or set MONNIFY_SIMULATION=true to test the flow. Until then fund-in "
                       "fails closed in production.")
        return out
    out["status"] = "configured"
    out["hint"] = ("Keys present. Verify init/query/reserved-account field names and set the webhook "
                   "to /api/fund/monnify/webhook/ in the Monnify dashboard before go-live.")
    return out
