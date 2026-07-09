"""Monnify integration — the single money-movement rail for Zitch.

Scope: WALLET FUNDING (collections) AND BANK PAYOUTS (disbursements). Monnify mints
a permanent NUBAN per user that funds the wallet by bank transfer, opens a hosted
checkout page, and — via the Disbursement API — sends money out and does recipient
name enquiry. Monnify is the sole money-movement rail (Wema stays opt-in).

NETWORK NOTE: Monnify *collections* (funding / reserved accounts) need NO IP
whitelisting, but Monnify *disbursements* (payouts) DO — the calling server's
egress IP must be whitelisted in the Monnify dashboard (Settings > API), OR 2FA/OTP
must be authorized per transfer. Zitch uses the IP-whitelist model: a static egress
IP (see the DigitalOcean/Render setup) is whitelisted, so single transfers complete
without an OTP round-trip. Use ``egress_ip()`` / ``disbursement_diagnostics()`` to
confirm the IP Monnify sees matches the whitelisted one.

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
    providers.funding_account_reserve.
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
# Payouts / disbursements — the "sender" rail (Monnify Disbursement API)
#
# Unlike collections, disbursements REQUIRE the caller's egress IP to be
# whitelisted in the Monnify dashboard (or 2FA/OTP per transfer). Zitch uses the
# IP-whitelist model: a single transfer completes without an OTP round-trip when
# the server IP is whitelisted. Money is drawn from the merchant's Monnify wallet
# (``sourceAccountNumber`` = MONNIFY_WALLET_ACCOUNT), not a per-user NUBAN.
#
# resolve_account / disburse / verify_payout mirror the provider-agnostic contract
# in utility.providers (payout_resolve_account / payout_send). Money calls fail
# closed in production when unkeyed (mock_disabled_in_prod) — a simulated payout is
# never served, even with MONNIFY_SIMULATION on (that flag is for the fund-in demo
# only). Dev/tests keep the offline mock so the flow stays testable.
# ---------------------------------------------------------------------------
def _source_account(source_account: str = "") -> str:
    """The disbursement source: the caller's override, else the configured
    merchant wallet (MONNIFY_WALLET_ACCOUNT)."""
    return (source_account or settings.MONNIFY.get("WALLET_ACCOUNT", "") or "").strip()


def resolve_account(account_number: str, bank_code: str) -> dict:
    """Recipient name enquiry: (account number, bank code) -> account holder name.

    GET /api/v1/disbursements/account/validate. MOCK returns a stub name in
    dev/tests; fails closed in production without keys (never presents a fabricated
    name as verified). Shape matches providers.payout_resolve_account.
    """
    if not monnify_live():
        if mock_disabled_in_prod():
            return {"success": False, "message": "Name enquiry is not configured"}
        return {"success": True, "mock": True, "name": "ADEYEMI WILLIAM"}
    headers = _auth_headers()
    if headers is None:
        return {"success": False, "message": "Monnify authentication failed"}
    m = settings.MONNIFY
    try:
        resp = requests.get(
            f"{m['BASE_URL']}/api/v1/disbursements/account/validate",
            params={"accountNumber": account_number, "bankCode": bank_code},
            headers=headers, timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        rb = data.get("responseBody", {}) or {}
        name = rb.get("accountName", "") or ""
        ok = bool(data.get("requestSuccessful")) and bool(name)
        if not ok:
            log.info("monnify_resolve_miss bank=%s acct_tail=%s msg=%s",
                     bank_code, account_number[-4:], data.get("responseMessage"))
        return {"success": ok, "name": name, "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


def disburse(amount_naira, reference: str, narration: str, bank_code: str,
             account_number: str, account_name: str, *,
             source_account: str = "", customer_email: str = "",
             customer_name: str = "") -> dict:
    """Single bank payout via Monnify's Disbursement API.

    POST /api/v2/disbursements/single. Returns {success, status, reference}:
    ``SUCCESS`` => sent; ``PENDING``/``PROCESSING`` => accepted, the disbursement
    webhook settles it later; an OTP/authorization status means 2FA is still ON
    (we don't do the OTP flow) — treated as not-sent so the caller refunds and the
    misconfig surfaces. Anything else is a failure the caller refunds.

    Draws from the merchant Monnify wallet (``sourceAccountNumber``); fails closed
    on a live call with no wallet configured rather than sending an empty source.
    """
    if not monnify_live():
        if mock_disabled_in_prod():
            return {"success": False, "message": "Payouts are not configured"}
        return {"success": True, "mock": True, "status": "success"}
    headers = _auth_headers()
    if headers is None:
        return {"success": False, "message": "Monnify authentication failed"}
    src = _source_account(source_account)
    if not src:
        log.warning("monnify_disburse_no_source ref=%s", reference)
        return {"success": False,
                "message": "Payouts are temporarily unavailable — please try again shortly."}
    m = settings.MONNIFY
    body = {
        "amount": float(amount_naira),  # Monnify amounts are in naira
        "reference": reference,
        "narration": narration or "Zitch transfer",
        "destinationBankCode": bank_code,
        "destinationAccountNumber": account_number,
        "currency": "NGN",
        "sourceAccountNumber": src,
    }
    try:
        resp = requests.post(
            f"{m['BASE_URL']}/api/v2/disbursements/single",
            json=body, headers=headers, timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        rb = data.get("responseBody", {}) or {}
        status = str(rb.get("status", "")).lower()
        ok = bool(data.get("requestSuccessful"))
        if ok and status == "success":
            return {"success": True, "status": "success",
                    "reference": rb.get("reference", reference), "raw": data}
        if ok and status in ("pending", "processing", "in_progress"):
            return {"success": True, "status": "pending",
                    "reference": rb.get("reference", reference), "raw": data}
        if status in ("otp_email_dispatch", "pending_authorization"):
            # 2FA is enabled on the account but Zitch runs the IP-whitelist model
            # (no OTP flow). Don't leave money in limbo — fail closed with a fix hint.
            log.warning("monnify_disburse_needs_otp ref=%s status=%s", reference, status)
            return {"success": False, "status": status,
                    "message": "Payout blocked: disable 2FA and whitelist the server IP in Monnify."}
        log.warning("monnify_disburse_failed ref=%s code=%s msg=%s",
                    reference, data.get("responseCode"), data.get("responseMessage"))
        return {"success": False, "status": status,
                "message": data.get("responseMessage", "Transfer not completed"), "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


def verify_payout(reference: str) -> dict:
    """Confirm a payout by our reference. GET /api/v2/disbursements/single/summary.

    Returns the {success, pending, status} shape the reconciler expects: SUCCESS =>
    settled; PENDING/PROCESSING => still unknown (retry later); FAILED/REVERSED =>
    a definitive failure the caller reverses."""
    if not monnify_live():
        if mock_disabled_in_prod():
            return {"success": False, "message": "Payouts are not configured"}
        return {"success": True, "mock": True, "status": "success"}
    headers = _auth_headers()
    if headers is None:
        return {"success": False, "message": "Monnify authentication failed"}
    m = settings.MONNIFY
    try:
        resp = requests.get(
            f"{m['BASE_URL']}/api/v2/disbursements/single/summary",
            params={"reference": reference}, headers=headers, timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        rb = data.get("responseBody", {}) or {}
        status = str(rb.get("status", "")).lower()
        if status == "success":
            return {"success": True, "status": status, "raw": data}
        if status in ("pending", "processing", "in_progress", ""):
            return {"success": False, "pending": True, "status": status or "pending", "raw": data}
        return {"success": False, "status": status, "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


def get_wallet_balance(account_number: str = "") -> dict:
    """Read the merchant disbursement wallet balance.

    GET /api/v2/disbursements/wallet-balance?accountNumber=. Payouts fail on an
    empty wallet no matter how correct the code is, so this is the money-side
    counterpart to monnify_probe."""
    if not monnify_live():
        return {"success": True, "mock": True, "available_balance": 0}
    headers = _auth_headers()
    if headers is None:
        return {"success": False, "message": "Monnify authentication failed"}
    m = settings.MONNIFY
    src = _source_account(account_number)
    try:
        resp = requests.get(
            f"{m['BASE_URL']}/api/v2/disbursements/wallet-balance",
            params={"accountNumber": src}, headers=headers, timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        rb = data.get("responseBody", {}) or {}
        return {"success": bool(data.get("requestSuccessful")),
                "available_balance": rb.get("availableBalance"),
                "ledger_balance": rb.get("ledgerBalance"), "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


def egress_ip() -> dict:
    """Report the server's public egress IP — the address Monnify sees on a
    disbursement call, which MUST be whitelisted in the dashboard.

    Queries a public IP-echo service (no secrets). Use it to confirm the running
    server presents the same static IP you whitelisted on Monnify; a mismatch is
    the #1 cause of live payout rejections on shared hosts (e.g. Render)."""
    try:
        resp = requests.get("https://api.ipify.org", params={"format": "json"}, timeout=10)
        return {"success": resp.ok, "egress_ip": (resp.json() or {}).get("ip", "")}
    except requests.RequestException as exc:
        return {"success": False, "message": f"Could not determine egress IP: {exc}"}


# ---------------------------------------------------------------------------
# KYC — BVN details-match + NIN lookup (Monnify VAS)
#
# The production KYC rail: Monnify validates the BVN against the holder's name /
# DOB / phone (details-match) and looks up NIN records, keeping identity on the
# same provider that mints the funding account. vNIN (Virtual NIN) has no Monnify
# product — it is verified via Prembly (see providers.kyc_verify_vnin). Mock when
# unkeyed; fails closed in prod via _mock_blocked.
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
# Diagnostics — mirrors mono_diagnostics
# ---------------------------------------------------------------------------
def deallocate_virtual_account(account_reference: str) -> dict:
    """Deallocate (release) a reserved account by our accountReference —
    DELETE /api/v1/bank-transfer/reserved-accounts/reference/{ref}. Used to clean
    up ZITCH-DIAG-* probe accounts, which otherwise block the customer's real
    account (Monnify allows one reserved account per customer/BVN)."""
    if not monnify_live():
        return {"success": not _mock_blocked(), "mock": True}
    headers = _auth_headers()
    if headers is None:
        return {"success": False, "message": "Monnify authentication failed"}
    m = settings.MONNIFY
    try:
        resp = requests.delete(
            f"{m['BASE_URL']}/api/v1/bank-transfer/reserved-accounts/reference/{account_reference}",
            headers=headers, timeout=REQUEST_TIMEOUT,
        )
        data = resp.json() if resp.content else {}
        return {"success": bool(data.get("requestSuccessful")),
                "message": data.get("responseMessage", ""), "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


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


def disbursement_diagnostics() -> dict:
    """Structured Monnify PAYOUT self-test (no secrets) — the sender-side probe.

    Reports the server's egress IP (must be whitelisted on Monnify), whether auth
    works, whether the merchant wallet is configured + its balance, and a sample
    name-enquiry. Each step names the fix. Use this to VERIFY an IP-whitelist setup
    end-to-end: if egress_ip doesn't match what you whitelisted, live payouts fail.
    """
    m = settings.MONNIFY
    out = {
        "egress": egress_ip(),  # the IP Monnify sees — must be whitelisted
        "wallet_account_set": bool(m.get("WALLET_ACCOUNT")),
        "monnify_live": monnify_live(),
    }
    if not monnify_live():
        out["status"] = "keys_incomplete"
        out["hint"] = ("Set the Monnify keys to test payouts. Whitelist the egress IP above in the "
                       "Monnify dashboard (Settings > API) and set MONNIFY_WALLET_ACCOUNT.")
        return out
    if not out["wallet_account_set"]:
        out["status"] = "no_wallet"
        out["hint"] = "Set MONNIFY_WALLET_ACCOUNT (the merchant wallet NUBAN payouts are drawn from)."
        return out
    bal = get_wallet_balance()
    out["auth_ok"] = bool(bal.get("success"))
    out["wallet_balance"] = bal.get("available_balance")
    if not out["auth_ok"]:
        out["status"] = "auth_or_ip_failed"
        out["hint"] = ("Wallet-balance read failed — either the keys are wrong, or (most likely for a "
                       "shared host) the egress IP above is NOT whitelisted on Monnify. Whitelist it, "
                       "then retry.")
        return out
    res = resolve_account("0000000000", "058")
    out["sample_enquiry"] = {"resolved": bool(res.get("success")), "name": res.get("name", ""),
                             "message": (res.get("raw") or {}).get("responseMessage") or res.get("message", "")}
    out["status"] = "ok"
    out["hint"] = ("Auth + wallet read work and the egress IP authenticates — the IP-whitelist payout "
                   "path is live. (The sample enquiry uses a dummy account; a miss there is expected.)")
    return out
