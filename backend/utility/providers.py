"""Third-party integration layer.

Providers: Wema / ALAT (money movement — funding via OTP-provisioned NUBANs,
payouts + name enquiry + balance, and BVN/NIN identity via the name-matched
account-creation flow; client in utility/wema.py),
VTU.ng (airtime/data/cable/electricity/betting), Sendchamp (SMS/OTP), Resend
(email/OTP), Prembly/IdentityPass (selfie / liveness + address + ID-document KYC —
the image/biometric checks the account-creation flow doesn't cover), Fincra (FX). Each
function returns {"success": bool, ...}. When the relevant key is blank it runs in
MOCK mode and simulates success so the whole app flow is testable without an external
account — EXCEPT in production (DEBUG off), where money/identity mocks fail closed
(see mock_disabled_in_prod) so a misconfigured deploy never fakes a money movement.

The funding_* / payout_* / card_* / verify_* wrappers are the stable, provider-
agnostic contract the views and services call; they delegate to the Wema client
(utility.wema), the sole money-movement + Nigeria-KYC rail.
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


def vas_provider() -> str:
    """VAS (airtime/data/bills) rail — 'wema' or 'vtung'.

    Explicit VAS_PROVIDER wins. Blank => AUTO: use Wema once its VAS keys are
    configured (or simulation is on), else VTU.ng — so airtime/data/bills never break
    on a deploy that has no Wema VAS keys yet. When Wema is selected the routing is
    still per-service: AIRTIME (network + amount, no catalogue) always goes to Wema;
    DATA and CABLE go to Wema once the plan's `wema_code` is synced
    (`manage.py seed_wema_plans`), else VTU.ng; ELECTRICITY and BETTING stay on VTU.ng
    until their Wema billers are mapped."""
    choice = (getattr(settings, "VAS_PROVIDER", "") or "").strip().lower()
    if choice in ("wema", "vtung"):
        return choice
    from . import wema
    return "wema" if (wema._vas_live("airtime") or wema.wema_simulation()) else "vtung"


def _wema_vas_route(service_id: str, payload: dict):
    """Resolve how Wema would fulfil this purchase, or None to stay on VTU.ng.

    Returns {"type": "airtime"|"data"|"bill", "code": <wema code>, "amount": <naira>}.
    Airtime always resolves; data/cable resolve only when the plan's `wema_code` has
    been synced (blank => None => VTU.ng); electricity/betting always return None."""
    if service_id.endswith("-airtime"):
        return {"type": "airtime", "code": "", "amount": payload.get("amount")}
    var = str(payload.get("variation_code", "") or "")
    if not var:
        return None  # no plan code -> nothing to map to a Wema catalogue code
    if service_id.endswith("-data"):
        from .models import DataPlan
        p = DataPlan.objects.filter(plan_code=var).only("wema_code", "price").first()
        return {"type": "data", "code": p.wema_code, "amount": p.price} if (p and p.wema_code) else None
    if service_id in ("dstv", "gotv", "startimes"):
        from .models import CablePlan
        p = CablePlan.objects.filter(cable_plan_code=var).only("wema_code", "price").first()
        return {"type": "bill", "code": p.wema_code, "amount": p.price} if (p and p.wema_code) else None
    return None


def _vas_source_account(payload: dict, reference: str | None) -> str:
    """The NUBAN a Wema VAS purchase debits (per-user-balance money-flow model).

    An explicit ``payload["source_account"]`` wins; otherwise the buyer's own
    wallet NUBAN is resolved from the ledger row the purchase is keyed on (the
    row exists by the time the provider call runs), so EVERY caller — app views
    and the WhatsApp router alike — debits the buyer's account rather than
    silently falling back to the shared WEMA_SOURCE_ACCOUNT pool, which would
    leak pool float while the buyer's NUBAN keeps its money. Blank only when the
    buyer has no Wema NUBAN yet (the Wema client then uses the pool)."""
    src = str(payload.get("source_account", "") or "")
    if src or not reference:
        return src
    from wallet.models import Transaction
    txn = Transaction.objects.filter(reference=reference).select_related("user").first()
    if txn is None:
        return ""
    return getattr(getattr(txn.user, "wallet", None), "account_number", "") or ""


def vtu_purchase(service_id: str, payload: dict, reference: str | None = None) -> dict:
    """Submit a VAS purchase via the selected rail.

    Pass the wallet ledger `reference` so it becomes the provider's request_id
    (idempotency key + requery handle). On a network error returns ``pending=True``:
    the purchase may have landed, so the caller must NOT refund — reconciliation
    requeries it by reference instead.

    With VAS_PROVIDER=wema (the default) each service routes to Wema only where a
    Wema catalogue code resolves (see `_wema_vas_route`); anything else falls through
    to VTU.ng. The chosen rail is stamped on the result (`vas_rail`/`vas_type`) so a
    PENDING purchase is requeried against the SAME rail that fulfilled it."""
    if vas_provider() == "wema":
        route = _wema_vas_route(service_id, payload)
        if route is not None:
            from . import wema
            src = _vas_source_account(payload, reference)
            phone = payload.get("phone", "")
            if route["type"] == "airtime":
                network = service_id.rsplit("-airtime", 1)[0]
                res = wema.purchase_airtime(route["amount"], reference or "", phone, network,
                                            source_account=src)
            elif route["type"] == "data":
                network = service_id.rsplit("-data", 1)[0]
                res = wema.purchase_data(route["amount"], reference or "", phone, network,
                                         route["code"], source_account=src)
            else:  # bill (cable)
                res = wema.pay_bill(route["amount"], reference or "", package_id=route["code"],
                                    identifier=payload.get("billersCode", ""), source_account=src,
                                    phone=phone)
            res.setdefault("vas_rail", "wema")
            res.setdefault("vas_type", route["type"])
            return res
    from .vtung import vt_purchase
    return vt_purchase(service_id, payload, reference)


def vtu_requery(reference: str) -> dict:
    """Requery a submitted purchase by our request_id to settle a PENDING
    transaction (e.g. one whose original send timed out).

    Returns the {"success", "pending", ...} shape settle_or_refund expects:
    success => delivered; pending => still unknown (retry later); neither =>
    a definitive failure the caller refunds. The rail is read from the ledger row's
    stamped `vas_rail`/`vas_type` (set at purchase), so a Wema purchase requeries via
    wema.vas_status and a VTU.ng one via vt_requery — even in the mixed state."""
    from wallet.models import Transaction
    txn = Transaction.objects.filter(reference=reference).only("meta").first()
    meta = (txn.meta if txn else None) or {}
    if meta.get("vas_rail") == "wema":
        from . import wema
        return wema.vas_status(reference, meta.get("vas_type", "airtime"))
    from .vtung import vt_requery
    return vt_requery(reference)


def vtu_verify_customer(service_id: str, billers_code: str, variation: str = "") -> dict:
    """Validate a meter / smartcard number, returning the customer name. Validation is
    read-only and stays on VTU.ng (the purchase rail is chosen separately in
    vtu_purchase)."""
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
# KYC — selfie/liveness + address + ID-document — Prembly (IdentityPass)
#
# Prembly is retained ONLY for the image/biometric checks the Wema account-creation
# flow can't do: selfie/liveness (kyc_verify_face — the ≥₦100k transfer gate + Tier 2),
# address (kyc_verify_address — Tier 2), and document-image OCR (kyc_verify_nin_document /
# kyc_verify_id_document — Tier 1 NIN slip / Tier 3 government ID). BVN/NIN identity is
# verified by the name-matched NUBAN account-creation flow (see verify_bvn/nin/vnin).
# ---------------------------------------------------------------------------
def _prembly_live() -> bool:
    return bool(settings.PREMBLY["API_KEY"] and settings.PREMBLY["APP_ID"])


def _prembly_headers() -> dict:
    return {
        "x-api-key": settings.PREMBLY["API_KEY"],
        "app-id": settings.PREMBLY["APP_ID"],
        "Content-Type": "application/json",
    }


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
# KYC — BVN / NIN / vNIN (Wema)
#
# verify_bvn / verify_nin / verify_vnin are the provider-agnostic entry points the
# rest of the app calls. ALAT has NO standalone identity lookup, so BVN/NIN are
# verified by the NUBAN account-creation flow (which name-matches the holder record
# ALAT returns — see wema.holder_name_mismatch + wallet.views.wema_wallet_verify_otp).
# These entry points therefore no longer call a lookup endpoint: in production they
# return an otp_required redirect to account setup; dev/tests keep the mock so the
# offline KYC flow still exercises. The image/biometric steps (selfie/liveness,
# address, ID-document OCR) stay on Prembly above. Identity never mock-passes in prod.
# ---------------------------------------------------------------------------
def kyc_provider() -> str:
    """The BVN/NIN backend — 'wema' (the sole rail). Retained so any caller/diagnostic
    that reads the selector keeps working."""
    choice = (getattr(settings, "KYC_PROVIDER", "") or "").strip().lower()
    return choice if choice == "wema" else "wema"


def verify_bvn(bvn: str, name: str = "", date_of_birth: str = "", mobile: str = "") -> dict:
    """BVN verification entry point. ALAT has no standalone lookup — in production this
    routes the user to the name-matched NUBAN account-creation flow; dev/tests mock."""
    from . import wema
    return wema.verify_bvn(bvn, name=name, date_of_birth=date_of_birth, mobile=mobile)


def verify_nin(nin: str, name: str = "") -> dict:
    """NIN verification entry point — see verify_bvn. Verification is the name-matched
    NUBAN account-creation flow; production routes the caller there, dev/tests mock."""
    from . import wema
    return wema.verify_nin(nin, name=name)


def verify_vnin(vnin: str, name: str = "") -> dict:
    """Virtual-NIN verification entry point — see verify_bvn. Verification is the
    name-matched NUBAN account-creation flow; production routes there, dev/tests mock."""
    from . import wema
    return wema.verify_vnin(vnin, name=name)


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
        # Fail closed in production (like every other money provider): a mock FX
        # quote would settle the REAL NGN ledger against a fabricated rate and
        # book phantom foreign-currency liability. Only the mock in dev/tests.
        if mock_disabled_in_prod():
            return {"success": False, "message": "FX is not configured"}
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
        if mock_disabled_in_prod():
            return {"success": False, "message": "FX is not configured"}
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
# Money-movement rail — Wema / ALAT (funding / virtual accounts / payouts)
#
# The funding_* / payout_* wrappers are the provider-agnostic contract the views
# and services call; they delegate to the Wema client (utility.wema), the sole
# money-movement rail. Wema funds by bank transfer to an OTP-provisioned NUBAN (no
# hosted checkout, no webhook — inbound deposits AND payout settlement are handled
# by the reconcile_wema poller). The *_provider() selectors are retained (returning
# "wema") so any remaining callers/diagnostics keep working.
# ---------------------------------------------------------------------------
def _wema_live() -> bool:
    from . import wema
    return wema.wema_live()


def payment_provider() -> str:
    """The wallet FUND-IN rail — 'wema' (the sole rail). Wema funds by bank transfer
    to an OTP-provisioned NUBAN (no hosted checkout, no webhook — deposits are
    reconciled by the reconcile_wema poller). Retained as a selector so callers keep
    working."""
    choice = (getattr(settings, "PAYMENT_PROVIDER", "") or "").strip().lower()
    return choice if choice == "wema" else "wema"


def payout_provider() -> str:
    """The bank-payout + recipient name-enquiry rail — 'wema' (the sole rail).
    Retained as a selector so callers keep working."""
    choice = (getattr(settings, "PAYOUT_PROVIDER", "") or "").strip().lower()
    return choice if choice == "wema" else "wema"


def payout_live() -> bool:
    """Whether the Wema payout rail has live keys (else MOCK)."""
    return _wema_live()


def card_provider() -> str:
    """Virtual-card backend — 'wema' or 'issuer'. Explicit CARD_PROVIDER wins; blank
    => AUTO: use Wema's Virtual Naira Card once its card key is configured, else the
    generic CARD_ISSUER — so cards never break on a deploy without a Wema card key."""
    choice = (getattr(settings, "CARD_PROVIDER", "") or "").strip().lower()
    if choice in ("wema", "issuer"):
        return choice
    from . import wema
    return "wema" if wema._card_live() else "issuer"


# --- Funding (wallet top-up) dispatch — Wema (OTP-provisioned NUBAN) ---
def funding_initialize(email: str, amount_naira, reference: str, *,
                       name: str = "", redirect_url: str = "") -> dict:
    """Wema/ALAT has no hosted checkout — funding is by bank transfer to the user's
    dedicated NUBAN (credited by the reconcile_wema poller), so there is no charge to
    start. Returns a graceful message the app shows instead of a checkout URL."""
    return {"success": False,
            "message": "Top up by bank transfer to your dedicated account number."}


def funding_verify(reference: str, provider: str = "") -> dict:
    """Wema deposits are credited by the reconcile poller, not a synchronous verify
    call, so there is nothing to confirm here."""
    return {"success": False, "message": "Wema funding is credited automatically on receipt."}


def funding_account_reserve(account_reference: str, account_name: str, customer_email: str,
                            customer_name: str, bvn: str = "", nin: str = "") -> dict:
    """Provision a dedicated funding (virtual) account.

    Wema can't mint an account synchronously — it needs a BVN/NIN + OTP round-trip
    driven by the /api/wallet/wema/* endpoints. Signal that so ensure_reserved_account
    leaves the wallet numberless (the OTP flow fills it) rather than surfacing a hard
    error.
    """
    return {"success": False, "otp_required": True,
            "message": "Verify the OTP to finish setting up your account."}


def funding_account_get(account_reference: str) -> dict:
    """Fetch an existing dedicated account (duplicate recovery).

    Wema accounts are provisioned by the OTP endpoints, not a synchronous lookup, so
    this signals otp_required rather than performing a wrong-rail lookup."""
    return {"success": False, "otp_required": True,
            "message": "Verify the OTP to finish setting up your account."}


# --- Payout (bank transfer) dispatch — Wema ---
def payout_resolve_account(account_number: str, bank_code: str) -> dict:
    """Recipient name enquiry via Wema.

    Returns {success, name, ...}. Wema resolves by (account_number, bank_code); no
    securityInfo is needed for enquiry."""
    from . import wema
    return wema.resolve_account(account_number, bank_code)


def payout_send(amount_naira, reference: str, narration: str, bank_code: str,
                account_number: str, account_name: str, bank_name: str = "",
                source_account: str = "") -> dict:
    """Single bank payout via Wema. Returns {success, status, ...}; Wema yields
    success/processing/pending — execute_payout treats PROCESSING/PENDING as
    not-yet-confirmed.

    `bank_name` is sent to Wema's ProcessClientTransfer (destinationBankName)
    alongside the code.

    MONEY-FLOW: Wema uses a per-user-balance model, so this debits the SENDER's own
    NUBAN — `source_account`, which execute_payout passes as the sender's
    wallet.account_number — falling back to the shared WEMA_SOURCE_ACCOUNT pool only
    for a sender who has no Wema NUBAN yet, and failing closed (refundable) on a live
    call with neither."""
    from . import wema
    src = source_account or settings.WEMA.get("SOURCE_ACCOUNT", "")
    if wema.wema_live() and not src:
        return {"success": False,
                "message": "Payouts are temporarily unavailable — please try again shortly."}
    return wema.transfer(
        amount_naira, reference, narration,
        source_account=src, destination_account=account_number,
        destination_bank_code=bank_code, destination_bank_name=bank_name,
        destination_name=account_name,
    )


# --- Virtual card dispatch ---
# Two card backends: the generic CARD_ISSUER (default) and ALAT Card-Management
# (NUBAN-keyed; selected when card_provider() is "wema" — see wema.card_*). When no
# backend is configured the calls mock in dev/test and fail closed in production
# (see issue_card / card_secure_details).
def card_issue(holder: str, customer_ref: str, email: str = "", *, account_number: str = "",
               phone: str = "") -> dict:
    if card_provider() == "wema":
        from . import wema
        # Wema keys the virtual card by the user's NUBAN — thread it through.
        return wema.card_issue(holder, customer_ref, account_number=account_number,
                               email=email, phone=phone)
    return issue_card(holder, customer_ref)


def card_set_status(card_token: str, active: bool) -> dict:
    if card_provider() == "wema":
        from . import wema
        return wema.card_set_status(card_token, active)
    return set_card_status(card_token, active)


def card_fund(card_token: str, amount) -> dict:
    if card_provider() == "wema":
        from . import wema
        return wema.card_fund(card_token, amount)
    return fund_card(card_token, amount)


def card_reveal(card_token: str) -> dict:
    if card_provider() == "wema":
        from . import wema
        return wema.card_reveal(card_token)
    return card_secure_details(card_token)
