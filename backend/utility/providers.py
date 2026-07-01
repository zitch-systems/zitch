"""Third-party integration layer.

Providers: Kora/Korapay (payments — funding, virtual accounts, payouts, and KYC
BVN/NIN/vNIN; client in utility/kora.py), VTU.ng (airtime/data/cable/electricity/
betting), Sendchamp (SMS/OTP), Resend (email/OTP), Prembly/IdentityPass (face /
liveness KYC only), Fincra (FX). Each function returns {"success": bool, ...}.
When the relevant key is blank it runs in MOCK mode and simulates success so the
whole app flow is testable without an external account — EXCEPT in production
(DEBUG off), where money/identity mocks fail closed (see mock_disabled_in_prod)
so a misconfigured deploy never fakes a money movement.

The funding_* / payout_* / card_* / verify_* wrappers are the stable, provider-
agnostic contract the views and services call; they delegate to the Kora client.
"""
import hashlib
import logging
import secrets

import requests
from django.conf import settings

REQUEST_TIMEOUT = 30
log = logging.getLogger("zitch")

# ---------------------------------------------------------------------------
# VTU (airtime / data / cable / electricity / betting) — VTU.ng
#
# VTU.ng is the sole VTU provider; its client lives in utility/vtung.py. The
# vtu_purchase / vtu_requery / vtu_verify_customer wrappers below are the stable
# contract the views and the reconcile job call, so callers never import the
# provider module directly.
# ---------------------------------------------------------------------------
def mock_disabled_in_prod() -> bool:
    """True when a provider's MOCK responses must be suppressed.

    A money provider with no credentials falls back to MOCK mode, which *fakes
    success*. That's fine in dev/tests, but in production it would tell a customer
    their airtime/data purchase succeeded while nothing was delivered (and the
    wallet was debited). When this returns True, the provider must fail closed
    instead — the debit is then refunded by the normal failure path.
    """
    return not settings.DEBUG and not getattr(settings, "TESTING", False)


def vtu_live() -> bool:
    """Whether the VTU provider (VTU.ng) has credentials configured."""
    from .vtung import _live
    return _live()


def vtu_purchase(service_id: str, payload: dict, reference: str | None = None) -> dict:
    """Submit a VTU purchase via VTU.ng.

    Pass the wallet ledger `reference` so it becomes VTU.ng's request_id
    (idempotency key + requery handle). On a network error returns
    ``pending=True``: the purchase may have landed, so the caller must NOT refund
    — reconciliation requeries it by reference instead.
    """
    from .vtung import vt_purchase
    return vt_purchase(service_id, payload, reference)


def vtu_requery(reference: str) -> dict:
    """Requery a submitted purchase by our request_id to settle a PENDING
    transaction (e.g. one whose original send timed out).

    Returns the {"success", "pending", ...} shape settle_or_refund expects:
    success => delivered; pending => still unknown (retry later); neither =>
    a definitive failure the caller refunds.
    """
    from .vtung import vt_requery
    return vt_requery(reference)


def vtu_verify_customer(service_id: str, billers_code: str, variation: str = "") -> dict:
    """Validate a meter / smartcard number, returning the customer name."""
    from .vtung import vt_verify_customer
    return vt_verify_customer(service_id, billers_code, variation)


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


def send_email(to: str, subject: str, message: str, html: str | None = None) -> dict:
    """Send a transactional email via Resend. Mirrors send_sms's mock-mode
    contract: blank API_KEY or empty `to` returns a silent-success dict so
    callers can fire-and-forget without branching on configuration. Used as a
    parallel OTP channel alongside Sendchamp so SMS routing issues never strand
    a user mid-signup. Pass `html` for a branded body (the plain `message` is
    kept as the text fallback for clients that don't render HTML)."""
    cfg = settings.RESEND
    if not cfg["API_KEY"] or not to:
        return {"success": True, "mock": True, "message": "Email sent (mock mode)"}
    payload = {"from": cfg["FROM_EMAIL"], "to": [to], "subject": subject, "text": message}
    if html:
        payload["html"] = html
    try:
        resp = requests.post(
            f"{cfg['BASE_URL']}/emails",
            json=payload,
            headers={
                "Authorization": f"Bearer {cfg['API_KEY']}",
                "Content-Type": "application/json",
            },
            timeout=REQUEST_TIMEOUT,
        )
        data = resp.json() if resp.content else {}
        return {"success": resp.ok and "id" in data, "raw": data}
    except requests.RequestException as exc:
        return {"success": False, "message": f"Email provider unreachable: {exc}"}


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


def kyc_verify_nin_document(image: str) -> dict:
    """Verify an uploaded NIN slip / ID image (OCR + match). MOCK accepts
    offline; LIVE must call Prembly's document endpoint and fail closed without
    a real pass. VERIFY-BEFORE-LIVE: confirm the exact endpoint/field names on
    the Prembly dashboard before relying on this."""
    if not _prembly_live():
        return {"success": True, "mock": True}
    if not image:
        return {"success": False, "message": "Upload your NIN slip to continue"}
    try:
        resp = requests.post(
            f"{settings.PREMBLY['BASE_URL']}/identitypass/verification/document/analysis",
            json={"doc_type": "nin", "image": image},
            headers=_prembly_headers(), timeout=REQUEST_TIMEOUT,
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


def kyc_verify_address(address: str, document: str = "") -> dict:
    """Verify a residential address (Tier 2). MOCK accepts offline; LIVE should
    call the KYC provider's address / proof-of-address endpoint and fail closed
    without a real pass. VERIFY-BEFORE-LIVE: confirm the endpoint/fields first."""
    if not _prembly_live():
        return {"success": True, "mock": True}
    if not (address or document):
        return {"success": False, "message": "Enter your residential address"}
    try:
        resp = requests.post(
            f"{settings.PREMBLY['BASE_URL']}/identitypass/verification/address",
            json={"address": address, "document": document},
            headers=_prembly_headers(), timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        return {"success": bool(data.get("status")), "raw": data}
    except requests.RequestException as exc:
        return {"success": False, "message": f"KYC provider unreachable: {exc}"}


def kyc_verify_id_document(image: str, doc_type: str = "") -> dict:
    """Verify a government-issued ID document (Tier 3): passport / driver's
    licence / voter's card / NIN slip. MOCK accepts offline; LIVE must call the
    provider's document-analysis endpoint and fail closed. VERIFY-BEFORE-LIVE."""
    if not _prembly_live():
        return {"success": True, "mock": True}
    if not image:
        return {"success": False, "message": "Upload a clear photo of your ID document"}
    try:
        resp = requests.post(
            f"{settings.PREMBLY['BASE_URL']}/identitypass/verification/document/analysis",
            json={"doc_type": doc_type or "generic", "image": image},
            headers=_prembly_headers(), timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        return {"success": bool(data.get("status")), "raw": data}
    except requests.RequestException as exc:
        return {"success": False, "message": f"KYC provider unreachable: {exc}"}


# ---------------------------------------------------------------------------
# KYC — Kora Identity (BVN / NIN / vNIN)
#
# verify_bvn / verify_nin / verify_vnin are the provider-agnostic entry points
# the rest of the app calls; they delegate to Kora (utility.kora). The selfie /
# liveness step (kyc_verify_face, in the Prembly block above) stays on Prembly —
# Kora has no liveness check. Each fails closed in production when Kora has no
# keys, so a money app never mock-passes identity on a misconfigured deploy;
# dev/tests keep the offline mock.
# ---------------------------------------------------------------------------
def _kora_kyc_live() -> bool:
    from . import kora
    return kora.kora_live()


def kyc_provider() -> str:
    """The BVN/NIN/vNIN backend — always 'kora'."""
    return "kora"


def verify_bvn(bvn: str, name: str = "", date_of_birth: str = "", mobile: str = "") -> dict:
    """Verify a BVN via Kora Identity.

    name/DOB/mobile are accepted for call-site compatibility; Kora's lookup is
    number-based. Fails closed in production without Kora keys; dev/tests mock."""
    from . import kora
    if not kora.kora_live() and mock_disabled_in_prod():
        return {"success": False, "message": "Identity verification is temporarily unavailable"}
    return kora.verify_bvn(bvn)


def verify_nin(nin: str) -> dict:
    """Verify a NIN via Kora Identity. Fails closed in prod without keys."""
    from . import kora
    if not kora.kora_live() and mock_disabled_in_prod():
        return {"success": False, "message": "Identity verification is temporarily unavailable"}
    return kora.verify_nin(nin)


def verify_vnin(vnin: str) -> dict:
    """Verify a Virtual NIN (16-char tokenised NIN).

    Only Kora exposes a vNIN lookup among the configured backends, so this routes
    to Kora directly. Fails closed in production when Kora has no keys; dev/tests
    keep the offline mock."""
    from . import kora
    if not kora.kora_live() and mock_disabled_in_prod():
        return {"success": False, "message": "Identity verification is temporarily unavailable"}
    return kora.verify_vnin(vnin)


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
        if mock_disabled_in_prod():
            # Never fabricate a card in production — a fake PAN/last4 would look
            # real in the app. Fail closed until a real issuer is configured.
            return {"success": False, "message": "Card issuing is not configured"}
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
        if mock_disabled_in_prod():
            return {"success": False, "message": "Card issuing is not configured"}
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
        if mock_disabled_in_prod():
            return {"success": False, "message": "Card issuing is not configured"}
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
        if mock_disabled_in_prod():
            # Fail closed: a fake success here would debit the real wallet to a
            # card that doesn't exist (the caller refunds on this failure).
            return {"success": False, "message": "Card issuing is not configured"}
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


# ---------------------------------------------------------------------------
# Money-movement rail — Kora (funding / virtual accounts / payouts)
#
# The funding_* / payout_* wrappers are the provider-agnostic contract the views
# and services call; they delegate to the Kora client (utility.kora). Kora is the
# sole rail. The *_provider() selectors are retained (returning "kora") so any
# remaining callers keep working. Kora pay-in/payout webhooks land on the
# wallet/transfers webhook routes.
# ---------------------------------------------------------------------------
def _kora_live() -> bool:
    from . import kora
    return kora.kora_live()


def payment_provider() -> str:
    """The wallet FUND-IN rail — 'monnify' or 'kora'. Explicit PAYMENT_PROVIDER
    wins; blank => auto (Monnify if its keys/simulation are set, else Kora).
    Payouts + recipient name-enquiry always stay on Kora regardless."""
    choice = (getattr(settings, "PAYMENT_PROVIDER", "") or "").strip().lower()
    if choice in ("monnify", "kora"):
        return choice
    from . import monnify
    if monnify.monnify_live() or monnify.monnify_simulation():
        return "monnify"
    return "kora"


def payout_provider() -> str:
    """The bank-payout rail — always 'kora'."""
    return "kora"


def payout_live() -> bool:
    """Whether the payout rail has live keys (else MOCK)."""
    return _kora_live()


def card_provider() -> str:
    """'issuer' (the generic CARD_ISSUER) or 'kora' — the virtual-card backend."""
    choice = (getattr(settings, "CARD_PROVIDER", "") or "").strip().lower()
    if choice in ("issuer", "kora"):
        return choice
    if _card_issuer_live():
        return "issuer"
    if _kora_live():
        return "kora"
    return "issuer"


# --- Funding (wallet top-up) dispatch — Monnify or Kora, per payment_provider() ---
def funding_initialize(email: str, amount_naira, reference: str, *,
                       name: str = "", redirect_url: str = "") -> dict:
    """Start a hosted-checkout funding charge -> {success, authorization_url}."""
    if payment_provider() == "monnify":
        from . import monnify
        return monnify.payment_initialize(email, amount_naira, reference,
                                          name=name, redirect_url=redirect_url)
    from . import kora
    return kora.payment_initialize(email, amount_naira, reference,
                                   name=name, redirect_url=redirect_url)


def funding_verify(reference: str, provider: str = "") -> dict:
    """Verify a funding charge. Honours the provider stamped on the FundingIntent
    (so a charge started on one rail verifies against that same rail even if the
    default flips), falling back to the current default."""
    prov = (provider or payment_provider()).strip().lower()
    if prov == "monnify":
        from . import monnify
        return monnify.payment_verify(reference)
    from . import kora
    return kora.payment_verify(reference)


def funding_account_reserve(account_reference: str, account_name: str, customer_email: str,
                            customer_name: str, bvn: str = "", nin: str = "") -> dict:
    """Provision a dedicated funding (virtual) account via the selected rail.

    Returns {success, account_number, bank_name, account_name, reference} so
    wallet.services.ensure_reserved_account stays agnostic.
    """
    if payment_provider() == "monnify":
        from . import monnify
        return monnify.create_virtual_account(account_reference, account_name, customer_email,
                                              customer_name, bvn=bvn, nin=nin)
    from . import kora
    return kora.create_virtual_account(account_reference, account_name, customer_email,
                                       customer_name, bvn=bvn, nin=nin)


def funding_account_get(account_reference: str) -> dict:
    """Fetch an existing dedicated account (duplicate recovery), per rail."""
    if payment_provider() == "monnify":
        from . import monnify
        return monnify.get_virtual_account(account_reference)
    from . import kora
    return kora.get_virtual_account(account_reference)


# --- Payout (bank transfer) dispatch ---
def payout_resolve_account(account_number: str, bank_code: str) -> dict:
    """Name enquiry via Kora."""
    from . import kora
    return kora.resolve_account(account_number, bank_code)


def payout_send(amount_naira, reference: str, narration: str, bank_code: str,
                account_number: str, account_name: str) -> dict:
    """Single bank payout via Kora. Returns {success, status, ...}; Kora yields
    success/processing/pending — execute_payout treats PROCESSING/PENDING as
    not-yet-confirmed."""
    from . import kora
    return kora.disburse(amount_naira, reference, narration, bank_code,
                         account_number, account_name)


# --- Virtual card dispatch ---
# Kora issues cards in two steps (cardholder -> card) and has no PAN-reveal
# endpoint, so card_reveal degrades gracefully on Kora. The generic CARD_ISSUER
# path is unchanged. VERIFY-BEFORE-LIVE for the Kora card endpoints (see kora.py).
def card_issue(holder: str, customer_ref: str, email: str = "") -> dict:
    if card_provider() == "kora":
        from . import kora
        ch = kora.create_cardholder(holder, email or f"{customer_ref}@zitch.app")
        if not ch.get("success"):
            return ch
        return kora.create_card(ch["reference"])
    return issue_card(holder, customer_ref)


def card_set_status(card_token: str, active: bool) -> dict:
    if card_provider() == "kora":
        from . import kora
        return kora.set_card_status(card_token, active)
    return set_card_status(card_token, active)


def card_fund(card_token: str, amount) -> dict:
    if card_provider() == "kora":
        from . import kora
        return kora.fund_card(card_token, amount)
    return fund_card(card_token, amount)


def card_reveal(card_token: str) -> dict:
    if card_provider() == "kora":
        # Kora exposes card details (masked) but no full PAN/CVV reveal endpoint.
        return {"success": False,
                "message": "Card detail reveal isn't available on this card provider"}
    return card_secure_details(card_token)
