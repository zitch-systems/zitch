"""Korapay (Kora) integration — the single money-movement + Nigeria KYC rail for Zitch.

Scope: WALLET FUNDING (collections) — dedicated (reserved) virtual accounts +
hosted checkout — AND BANK PAYOUTS (disbursements) + recipient name enquiry +
merchant balance, plus Nigeria identity verification (BVN / NIN / vNIN). Kora
replaces Monnify as the sole default money rail (Wema stays opt-in).

Kora's reserved virtual accounts are issued on Wema Bank (bank code 035) and can
be permanent (static). They accept payments immediately; an inbound bank transfer
arrives as a ``charge.success`` webhook carrying ``virtual_bank_account_details``.
Hosted checkout returns a ``checkout_url`` the app opens.

Auth: a single Bearer token (the SECRET key) on every call here — no OAuth login,
no token cache, and (unlike Monnify) NO IP whitelisting / forward proxy for
payouts. Base URL https://api.korapay.com, paths under /merchant/api/v1. Amounts
are in NAIRA (the major unit), not kobo. Every function returns {"success": bool, ...}.

Envelope: Kora replies {"status": bool, "message": str, "data": {...}}. Success is
``status is True``; ``data`` carries the result (object or list).

MOCK mode: when the Kora secret key is blank the calls simulate success so the flow
is testable offline — EXCEPT in production (DEBUG off), where account/money/identity
calls fail closed via providers.mock_disabled_in_prod so a misconfigured deploy never
fabricates a dead NUBAN or a mock KYC pass. KORA_SIMULATION=true serves the mock
fund-in flow even in production (to demo/test a real build without live keys) — no
real money moves; payouts and identity still fail closed.

Inbound funding arrives as a ``charge.success`` webhook (x-korapay-signature =
HMAC-SHA256 of the JSON ``data`` object with the secret key). It's credited
idempotently by wallet.views.kora_fund_webhook -> settle_reserved_funding /
settle_funding; payout terminal states arrive as ``transfer.success`` /
``transfer.failed`` on /api/transfers/webhook/.

VERIFY-BEFORE-LIVE: endpoint paths/fields follow Kora's published API but can't be
exercised from CI — confirm against the dashboard/docs (especially the webhook
signature scheme, the VA kyc field, and the identity request/response shapes)
before go-live.
"""
import hashlib
import hmac
import json
import logging
import re
import secrets

import requests
from django.conf import settings

from .providers import mock_disabled_in_prod

REQUEST_TIMEOUT = 30
log = logging.getLogger("zitch")


def kora_live() -> bool:
    """Whether Kora has a secret key configured (live, non-mock)."""
    return bool(settings.KORA.get("SECRET_KEY"))


def kora_simulation() -> bool:
    """Whether KORA_SIMULATION is on — serve the mock fund-in flow even in
    production so a real build can test funding without live keys. No real money
    moves. Off by default."""
    return bool(settings.KORA.get("SIMULATION"))


def _mock_blocked() -> bool:
    """True when a mock response must NOT be served: production, and simulation is
    not explicitly enabled."""
    return mock_disabled_in_prod() and not kora_simulation()


def _unreachable(exc: Exception) -> dict:
    return {"success": False, "message": f"Payment gateway unreachable: {exc}"}


def _headers(public: bool = False) -> dict:
    """Bearer auth. Secret key for pay-ins / payouts / virtual accounts / balances /
    verification; the public key only for the (unused-here) Miscellaneous group."""
    k = settings.KORA
    key = (k.get("PUBLIC_KEY") if public else k.get("SECRET_KEY")) or ""
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
            "Accept": "application/json"}


def _url(path: str) -> str:
    base = settings.KORA["BASE_URL"].rstrip("/")
    return f"{base}/merchant/api/v1{path}"


def _va_bank_code() -> str:
    """The bank Kora issues reserved accounts on (Wema, 035, by default)."""
    return (settings.KORA.get("VA_BANK_CODE") or "").strip() or "035"


def _ok(data: dict) -> bool:
    """Kora success: the envelope ``status`` boolean is true."""
    return isinstance(data, dict) and data.get("status") is True


def _name_tokens(name: str) -> set:
    """Significant lowercased word tokens of a holder name (drops 1-char bits),
    for tolerant BVN/NIN name comparison."""
    return {t for t in re.sub(r"[^a-z ]", " ", (name or "").lower()).split() if len(t) > 1}


def _name_mismatch(supplied: str, resolved: str) -> bool:
    """True only when BOTH names are non-empty and share NO tokens — a clear
    mismatch. Tolerant of order / middle names so a legitimate holder is never
    blocked by a formatting difference."""
    a, b = _name_tokens(supplied), _name_tokens(resolved)
    return bool(a and b and not (a & b))


# ---------------------------------------------------------------------------
# Hosted checkout (card / bank) — fund the wallet without a dedicated account
# ---------------------------------------------------------------------------
def payment_initialize(email: str, amount_naira, reference: str, *,
                       name: str = "", redirect_url: str = "") -> dict:
    """Start a funding charge; returns a checkout URL the app opens.

    POST /charges/initialize -> {data.checkout_url}. MOCK returns a sentinel URL so
    funding is testable offline (the tester 'completes' it via the verify endpoint).
    """
    if not kora_live():
        if _mock_blocked():
            return {"success": False, "message": "Payment gateway is not configured"}
        return {"success": True, "mock": True, "reference": reference,
                "authorization_url": f"mock://kora/checkout/{reference}"}
    k = settings.KORA
    body = {
        "amount": float(amount_naira),  # Kora amounts are in naira (major unit)
        "currency": "NGN",
        "reference": reference,
        "narration": "Wallet top-up",
        "customer": {"name": name or (email or "Zitch user").split("@")[0], "email": email},
    }
    redirect = redirect_url or k.get("REDIRECT_URL", "")
    if redirect:
        body["redirect_url"] = redirect
    if k.get("FUND_WEBHOOK_URL"):
        body["notification_url"] = k["FUND_WEBHOOK_URL"]
    try:
        resp = requests.post(_url("/charges/initialize"), json=body,
                             headers=_headers(), timeout=REQUEST_TIMEOUT)
        data = resp.json()
        d = data.get("data", {}) or {}
        url = d.get("checkout_url", "")
        ok = _ok(data) and bool(url)
        if not ok:
            log.warning("kora_init_failed ref=%s msg=%s", reference, data.get("message"))
        return {"success": ok, "reference": d.get("reference", reference),
                "authorization_url": url,
                "message": data.get("message") or "Could not start payment", "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


def payment_verify(reference: str) -> dict:
    """Confirm a charge with Kora (source of truth for crediting).

    GET /charges/:reference -> data.status == "success". MOCK treats any reference
    as paid so funding works without real money.
    """
    if not kora_live():
        if _mock_blocked():
            return {"success": False, "message": "Payment gateway is not configured"}
        return {"success": True, "mock": True, "amount_naira": None, "reference": reference}
    try:
        resp = requests.get(_url(f"/charges/{reference}"), headers=_headers(), timeout=REQUEST_TIMEOUT)
        data = resp.json()
        d = data.get("data", {}) or {}
        paid = _ok(data) and str(d.get("status", "")).lower() == "success"
        return {"success": paid, "amount_naira": d.get("amount"),
                "reference": d.get("reference", reference), "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


# ---------------------------------------------------------------------------
# Reserved (dedicated) virtual accounts — fund by bank transfer
# ---------------------------------------------------------------------------
def _parse_va(data: dict) -> dict:
    """Normalise a create/get virtual-account response into our shape (flat primary
    account + the single-element bank_accounts list the wallet stores)."""
    d = data.get("data", {}) or {}
    number = d.get("account_number", "")
    bank_name = d.get("bank_name", "")
    accounts = ([{"bank_name": bank_name, "account_number": number,
                  "bank_code": d.get("bank_code", _va_bank_code())}]
                if number else [])
    return {
        "success": _ok(data) and bool(number),
        "account_number": number,
        "bank_name": bank_name,
        "account_name": d.get("account_name", ""),
        "reference": d.get("account_reference", ""),
        "accounts": accounts,
        "message": data.get("message", "Could not reserve account"),
        "raw": data,
    }


def create_virtual_account(account_reference: str, account_name: str, customer_email: str,
                           customer_name: str, bvn: str = "", nin: str = "") -> dict:
    """Reserve a permanent virtual account for a customer (fund-in by transfer).

    POST /virtual-bank-account. ``account_reference`` is our stable per-user key
    (Kora rejects a duplicate — reuse it). Kora requires a BVN in ``kyc`` to mint a
    permanent account. MOCK fabricates a deterministic NUBAN so the flow is testable;
    production without keys fails closed (never fabricates a NUBAN a user could send
    real money to). Signature matches providers.funding_account_reserve.
    """
    if not kora_live():
        if _mock_blocked():
            return {"success": False, "message": "Reserved accounts are not configured"}
        seed = int(hashlib.sha256(account_reference.encode()).hexdigest(), 16)
        num = "99" + f"{seed % 10**8:08d}"
        return {"success": True, "mock": True, "account_number": num,
                "bank_name": "Wema Bank (demo)", "account_name": account_name,
                "reference": account_reference,
                "accounts": [{"bank_name": "Wema Bank (demo)", "account_number": num,
                              "bank_code": _va_bank_code()}]}
    kyc = {}
    if bvn:
        kyc["bvn"] = bvn
    if nin:
        kyc["nin"] = nin
    body = {
        "account_name": account_name,
        "account_reference": account_reference,
        "permanent": True,
        "bank_code": _va_bank_code(),
        "customer": {"name": customer_name, "email": customer_email},
    }
    if kyc:
        body["kyc"] = kyc
    if settings.KORA.get("FUND_WEBHOOK_URL"):
        body["notification_url"] = settings.KORA["FUND_WEBHOOK_URL"]
    try:
        resp = requests.post(_url("/virtual-bank-account"), json=body,
                             headers=_headers(), timeout=REQUEST_TIMEOUT)
        out = _parse_va(resp.json())
        if not out["success"]:
            log.warning("kora_reserve_failed ref=%s msg=%s", account_reference, out.get("message"))
        return out
    except requests.RequestException as exc:
        return _unreachable(exc)


def get_virtual_account(account_reference: str) -> dict:
    """Fetch an existing reserved account by our account_reference (duplicate
    recovery). GET /virtual-bank-account/{account_reference}."""
    if not kora_live():
        if _mock_blocked():
            return {"success": False, "message": "Reserved accounts are not configured"}
        return {"success": False, "message": "mock: no stored account"}
    try:
        resp = requests.get(_url(f"/virtual-bank-account/{account_reference}"),
                            headers=_headers(), timeout=REQUEST_TIMEOUT)
        return _parse_va(resp.json())
    except requests.RequestException as exc:
        return _unreachable(exc)


# ---------------------------------------------------------------------------
# Payouts / disbursements — the "sender" rail (Kora Payout API)
#
# Money is drawn from the merchant's Kora balance (like Monnify's pooled wallet),
# so the sender's own NUBAN (`source_account`) is ignored here. No IP whitelisting.
# resolve_account / disburse / verify_payout mirror the provider-agnostic contract
# in utility.providers. Money calls fail closed in production when unkeyed.
# ---------------------------------------------------------------------------
# Kora payout states that mean ACCEPTED-but-not-yet-delivered (hold the debit).
_DISBURSE_IN_FLIGHT = ("processing", "pending", "in_progress", "queued")
# Terminal failure states (definitively not delivered — refunding is safe).
_DISBURSE_TERMINAL_FAIL = ("failed", "reversed", "expired", "cancelled", "declined",
                           "rejected", "returned")


def resolve_account(account_number: str, bank_code: str) -> dict:
    """Recipient name enquiry: (account number, bank code) -> account holder name.

    POST /misc/banks/resolve {bank, account}. MOCK returns a stub name in dev/tests;
    fails closed in production without keys. Shape matches
    providers.payout_resolve_account.
    """
    if not kora_live():
        if mock_disabled_in_prod():
            return {"success": False, "message": "Name enquiry is not configured"}
        return {"success": True, "mock": True, "name": "ADEYEMI WILLIAM"}
    try:
        resp = requests.post(_url("/misc/banks/resolve"),
                             json={"bank": bank_code, "account": account_number},
                             headers=_headers(), timeout=REQUEST_TIMEOUT)
        data = resp.json()
        d = data.get("data", {}) or {}
        name = d.get("account_name", "") or ""
        ok = _ok(data) and bool(name)
        if not ok:
            log.info("kora_resolve_miss bank=%s acct_tail=%s msg=%s",
                     bank_code, account_number[-4:], data.get("message"))
        return {"success": ok, "name": name, "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


def disburse(amount_naira, reference: str, narration: str, bank_code: str,
             account_number: str, account_name: str, *,
             source_account: str = "", customer_email: str = "",
             customer_name: str = "") -> dict:
    """Single bank payout via Kora's Payout API.

    POST /transactions/disburse. Returns {success, status, reference}: ``success``
    => sent; ``processing``/``pending`` => accepted, the transfer webhook settles it
    later; anything else is a failure the caller refunds.

    A NETWORK error (timeout / lost response) is AMBIGUOUS on this non-idempotent
    POST — Kora may already have executed the transfer — so it returns
    ``{"success": False, "pending": True, "status": "pending"}`` (NOT a hard
    failure); the caller HOLDS the debit and the payout reconciler / webhook later
    settles or reverses it. Draws from the merchant Kora balance.
    """
    if not kora_live():
        if mock_disabled_in_prod():
            return {"success": False, "message": "Payouts are not configured"}
        return {"success": True, "mock": True, "status": "success"}
    body = {
        "reference": reference,
        "destination": {
            "type": "bank_account",
            "amount": float(amount_naira),  # naira (major unit)
            "currency": "NGN",
            "narration": narration or "Zitch transfer",
            "bank_account": {"bank": bank_code, "account": account_number},
            "customer": {"name": account_name or customer_name or "Zitch recipient"},
        },
    }
    if customer_email:
        body["destination"]["customer"]["email"] = customer_email
    if settings.KORA.get("PAYOUT_WEBHOOK_URL"):
        body["notification_url"] = settings.KORA["PAYOUT_WEBHOOK_URL"]
    try:
        resp = requests.post(_url("/transactions/disburse"), json=body,
                             headers=_headers(), timeout=REQUEST_TIMEOUT)
        data = resp.json()
        d = data.get("data", {}) or {}
        status = str(d.get("status", "") or "").strip().lower()
        ok = _ok(data)
        if ok and status == "success":
            return {"success": True, "status": "success",
                    "reference": d.get("reference", reference), "raw": data}
        if ok and status in _DISBURSE_IN_FLIGHT:
            # Accepted and in flight — hold the debit; the webhook / reconciler
            # settles or reverses it once the terminal state is known.
            return {"success": True, "status": "processing",
                    "reference": d.get("reference", reference), "raw": data}
        if ok and status in _DISBURSE_TERMINAL_FAIL:
            # Accepted request, terminal transfer state: definitively not delivered.
            log.warning("kora_disburse_terminal ref=%s status=%s", reference, status)
            return {"success": False, "status": status,
                    "message": data.get("message") or "The bank could not complete this transfer. "
                                                       "Please try again shortly.", "raw": data}
        if ok:
            # Accepted but an unrecognised status — treat as in-flight, NEVER as a
            # definitive failure (refunding a maybe-delivered payout double-spends).
            log.warning("kora_disburse_unrecognized_status ref=%s status=%s", reference, status)
            return {"success": True, "status": "processing",
                    "reference": d.get("reference", reference), "raw": data}
        # status is false: Kora REJECTED the request outright — not executed, so the
        # caller's refund is safe.
        log.warning("kora_disburse_failed ref=%s msg=%s", reference, data.get("message"))
        return {"success": False, "status": status,
                "message": data.get("message") or "Transfer not completed", "raw": data}
    except requests.RequestException as exc:
        # Ambiguous outcome on a non-idempotent POST: signal PENDING so the caller
        # holds the debit; the reconciler requeries verify_payout to settle/reverse.
        log.warning("kora_disburse_unreachable ref=%s err=%s", reference, exc)
        return {"success": False, "pending": True, "status": "pending",
                "reference": reference, "message": "Transfer is processing"}


def verify_payout(reference: str) -> dict:
    """Confirm a payout by our reference. GET /transactions/:reference.

    Returns the {success, pending, status} shape the reconciler expects: success =>
    settled; processing/pending => still unknown (retry later); failed/reversed =>
    a definitive failure the caller reverses."""
    if not kora_live():
        if mock_disabled_in_prod():
            return {"success": False, "message": "Payouts are not configured"}
        return {"success": True, "mock": True, "status": "success"}
    try:
        resp = requests.get(_url(f"/transactions/{reference}"),
                            headers=_headers(), timeout=REQUEST_TIMEOUT)
        data = resp.json()
        d = data.get("data", {}) or {}
        status = str(d.get("status", "") or "").strip().lower()
        if status == "success":
            return {"success": True, "status": status, "raw": data}
        if status in _DISBURSE_IN_FLIGHT or status == "":
            return {"success": False, "pending": True, "status": status or "pending", "raw": data}
        return {"success": False, "status": status, "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


def get_balances() -> dict:
    """Read the merchant Kora balance. GET /balances -> available + pending per
    currency. Payouts fail on an empty balance no matter how correct the code is,
    so this is the money-side counterpart to kora_probe."""
    if not kora_live():
        return {"success": True, "mock": True, "available_balance": 0}
    try:
        resp = requests.get(_url("/balances"), headers=_headers(), timeout=REQUEST_TIMEOUT)
        data = resp.json()
        d = data.get("data", {}) or {}
        ngn = d.get("NGN", d) if isinstance(d, dict) else {}
        return {"success": _ok(data),
                "available_balance": ngn.get("available_balance"),
                "pending_balance": ngn.get("pending_balance"), "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


# ---------------------------------------------------------------------------
# KYC — Nigeria identity lookups (Kora Identity / eIDV): BVN / NIN / vNIN
#
# Kora returns the holder's record on a valid lookup (data-based, not a match
# product). verify_bvn additionally rejects a clear name mismatch when a name is
# supplied. Identity NEVER mock-passes in production (fails closed via
# mock_disabled_in_prod) so a misconfigured deploy can't upgrade a real tier on a
# fabricated identity — deliberately stricter than the fund-in mock (which
# KORA_SIMULATION can serve).
# ---------------------------------------------------------------------------
def _identity_blocked() -> bool:
    """Identity must never mock-pass in production, even with KORA_SIMULATION on."""
    return mock_disabled_in_prod()


def verify_bvn(bvn: str, name: str = "", date_of_birth: str = "", mobile: str = "") -> dict:
    """Verify a BVN via Kora's identity lookup (POST /identities/ng/bvn {id}).

    On a valid lookup Kora returns the holder's record; when ``name`` is supplied we
    reject only a CLEAR mismatch (no shared name tokens), tolerant of order/middle
    names. Fails closed in production without keys."""
    if len(bvn) != 11 or not bvn.isdigit():
        return {"success": False, "message": "BVN must be 11 digits"}
    if not kora_live():
        if _identity_blocked():
            return {"success": False, "message": "Identity verification is temporarily unavailable"}
        return {"success": True, "mock": True, "first_name": "", "last_name": ""}
    try:
        resp = requests.post(_url("/identities/ng/bvn"), json={"id": bvn},
                             headers=_headers(), timeout=REQUEST_TIMEOUT)
        data = resp.json()
        d = data.get("data", {}) or {}
        ok = _ok(data) and bool(d)
        first, last = d.get("first_name", ""), d.get("last_name", "")
        if ok and name and _name_mismatch(name, f"{first} {last}"):
            return {"success": False, "message": "This BVN does not match your name", "raw": data}
        return {"success": ok, "first_name": first, "last_name": last, "raw": data,
                "message": data.get("message", "")}
    except requests.RequestException as exc:
        return _unreachable(exc)


def verify_nin(nin: str) -> dict:
    """Verify a NIN via Kora (POST /identities/ng/nin {id}) -> holder details."""
    if len(nin) != 11 or not nin.isdigit():
        return {"success": False, "message": "NIN must be 11 digits"}
    if not kora_live():
        if _identity_blocked():
            return {"success": False, "message": "Identity verification is temporarily unavailable"}
        return {"success": True, "mock": True}
    try:
        resp = requests.post(_url("/identities/ng/nin"), json={"id": nin},
                             headers=_headers(), timeout=REQUEST_TIMEOUT)
        data = resp.json()
        d = data.get("data", {}) or {}
        return {"success": _ok(data) and bool(d), "raw": data,
                "first_name": d.get("first_name", ""), "last_name": d.get("last_name", ""),
                "message": data.get("message", "")}
    except requests.RequestException as exc:
        return _unreachable(exc)


def verify_vnin(vnin: str) -> dict:
    """Verify a Virtual NIN (16-char tokenised NIN) via Kora
    (POST /identities/ng/vnin {id}). Fails closed in production when unkeyed."""
    if not vnin or len(vnin) != 16:
        return {"success": False, "message": "Virtual NIN must be 16 characters"}
    if not kora_live():
        if _identity_blocked():
            return {"success": False, "message": "Identity verification is temporarily unavailable"}
        return {"success": True, "mock": True, "first_name": "", "last_name": ""}
    try:
        resp = requests.post(_url("/identities/ng/vnin"), json={"id": vnin},
                             headers=_headers(), timeout=REQUEST_TIMEOUT)
        data = resp.json()
        d = data.get("data", {}) or {}
        return {"success": _ok(data) and bool(d), "raw": data,
                "first_name": d.get("first_name", ""), "last_name": d.get("last_name", ""),
                "message": data.get("message", "")}
    except requests.RequestException as exc:
        return _unreachable(exc)


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------
def verify_webhook(body: bytes, signature: str) -> bool:
    """Validate a Kora webhook: HMAC-SHA256 of the JSON ``data`` object with the
    secret key, compared to the ``x-korapay-signature`` header.

    Kora signs only the ``data`` sub-object (compact JSON), not the whole body.
    Fails closed in production when unkeyed (an unsigned callback credits a wallet);
    dev/test (or KORA_SIMULATION) accept so local webhook testing works.
    """
    if not kora_live():
        return not _mock_blocked()
    if not signature:
        return False
    try:
        payload = json.loads(body or b"{}")
    except (ValueError, TypeError):
        return False
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    # Compact separators + preserved key order reproduce Kora's JSON.stringify(data).
    serialized = json.dumps(data, separators=(",", ":")).encode()
    digest = hmac.new(settings.KORA["SECRET_KEY"].encode(), serialized, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, str(signature or ""))


# ---------------------------------------------------------------------------
# Diagnostics — mirrors wema_probe
# ---------------------------------------------------------------------------
def kora_probe(bvn: str = "", name: str = "", email: str = "") -> dict:
    """Live self-test against the configured Kora gateway (returns NO secrets).

    Proves, in order: (1) the secret key authenticates (a balance read succeeds),
    and — optionally, when ?bvn=&name= are supplied — (2) Kora can MINT a NUBAN
    end-to-end (creates a real reserved account under a ZITCH-DIAG-* reference).
    Also restates the webhook URL funding depends on.
    """
    out = {"config": kora_diagnostics(),
           "webhook_reminder": "Funding credits arrive via the webhook — set "
                               "https://<your-api-host>/api/fund/kora/webhook/ as the "
                               "webhook URL in the Kora dashboard or deposits will NOT credit."}
    if not kora_live():
        out["hint"] = "Kora secret key not set — no live call was made."
        return out

    bal = get_balances()
    out["auth"] = {"ok": bool(bal.get("success")), "available_balance": bal.get("available_balance")}
    if not bal.get("success"):
        out["auth"]["hint"] = ("Balance read failed — check KORA_SECRET_KEY and KORA_BASE_URL "
                               "(https://api.korapay.com).")
        return out

    if bvn and name:
        ref = f"ZITCH-DIAG-{secrets.token_hex(4).upper()}"
        created = create_virtual_account(ref, name, email or "diag@zitch.ng", name, bvn=bvn)
        out["nuban_create"] = {"ok": created.get("success"),
                               "account_number": created.get("account_number", ""),
                               "bank_name": created.get("bank_name", ""),
                               "reference": ref if created.get("success") else "",
                               "message": created.get("message", "")}
    return out


def kora_diagnostics() -> dict:
    """Structured Kora connectivity self-test (no secrets)."""
    k = settings.KORA
    out = {"base_url": k["BASE_URL"], "secret_key_set": bool(k.get("SECRET_KEY")),
           "public_key_set": bool(k.get("PUBLIC_KEY")), "va_bank_code": _va_bank_code(),
           "kora_live": kora_live(), "simulation": kora_simulation()}
    if not kora_live():
        if kora_simulation():
            out["status"] = "simulation"
            out["hint"] = ("KORA_SIMULATION is ON — the mock fund-in flow is served even in "
                           "production. No real money moves. Set KORA_SECRET_KEY and turn simulation "
                           "off to go live.")
            return out
        out["status"] = "keys_incomplete"
        out["hint"] = ("Set KORA_SECRET_KEY (and KORA_PUBLIC_KEY), or set KORA_SIMULATION=true to test "
                       "the flow. Until then fund-in/payout/KYC fail closed in production.")
        return out
    out["status"] = "configured"
    out["hint"] = ("Keys present. Verify the charge/VA/disburse/identity field names and set the webhook "
                   "URL to /api/fund/kora/webhook/ in the Kora dashboard before go-live.")
    return out
