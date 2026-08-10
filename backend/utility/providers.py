"""Third-party integration layer.

Providers: Wema / ALAT (money movement — funding via OTP-provisioned NUBANs,
payouts + name enquiry + balance, and BVN/NIN identity via the name-matched
account-creation flow; client in utility/wema.py),
VTU.ng (airtime/data/cable/electricity/betting), Termii (SMS/OTP), Resend
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
def simulation_mode() -> bool:
    """WEMA_SIMULATION doubles as the DEPLOY-WIDE simulation switch: when on, the whole
    payment/identity stack (Wema, VTU.ng airtime/data/bills, cards, FX, Mono, KYC)
    serves its MOCK paths, so the app can be walked end-to-end with no real money or
    identity. It is a HARD go-live blocker — wema_preflight fails while it is set — so
    it can only ever be on in a test deploy, never in production."""
    return bool(settings.WEMA.get("SIMULATION"))


def mock_disabled_in_prod() -> bool:
    """True when a provider's MOCK responses must be suppressed.

    A money provider with no credentials falls back to MOCK mode, which *fakes
    success*. That's fine in dev/tests, but in production it would tell a customer
    their airtime/data purchase succeeded while nothing was delivered (and the
    wallet was debited). When this returns True, the provider must fail closed
    instead — the debit is then refunded by the normal failure path.

    A simulation deploy is the deliberate exception: WEMA_SIMULATION marks the deploy
    as fake-money end-to-end, so mocks are allowed even with DEBUG off. This is safe
    because simulation is a hard go-live gate (see wema_preflight), so it is never on
    in a real-money deploy.
    """
    if simulation_mode():
        return False
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
# SMS / OTP — Termii
# ---------------------------------------------------------------------------
def _ng_msisdn(phone: str) -> str:
    """Nigerian phone in Termii's expected international format (234XXXXXXXXXX).

    The app stores/handles numbers in local '080…' format, but Termii's DND
    route (needed to reach the DND-registered numbers most Nigerian lines are) does
    not reliably deliver to a local-format number — so normalise before send: strip
    non-digits, drop a single leading 0, and ensure the 234 country code.
    """
    digits = "".join(c for c in (phone or "") if c.isdigit())
    if digits.startswith("234"):
        return digits
    if digits.startswith("0"):
        return "234" + digits[1:]
    if len(digits) == 10:  # 803… supplied without the leading 0
        return "234" + digits
    return digits  # already international, or non-NG — pass through unchanged


def sms_live() -> bool:
    """Whether the SMS rail has a key (i.e. a real send will be attempted)."""
    return bool(settings.TERMII["API_KEY"])


def email_live() -> bool:
    """Whether the email rail has a key (i.e. a real send will be attempted).

    The mirror of sms_live(), and needed for the same reason: send_email returns
    a silent-success dict when unkeyed, so a caller that only checks `success`
    will report a code it never delivered."""
    return bool(settings.RESEND["API_KEY"])


def _send_sms_termii(phone: str, message: str) -> dict:
    """Termii `POST /api/sms/send`.

    Two details of this request shape are easy to get wrong and fail silently: the
    api_key travels in the BODY (there is no Authorization header — a bearer token
    here authenticates as nobody), and `to` is a bare string, not a list. A malformed
    request is accepted-looking but never delivers, which the signup flow hides by
    design — hence sms_probe.
    """
    cfg = settings.TERMII
    try:
        resp = requests.post(
            f"{cfg['BASE_URL']}/api/sms/send",
            json={
                "to": _ng_msisdn(phone),
                "from": cfg["SENDER_ID"],
                "sms": message,
                "type": "plain",
                "channel": cfg["CHANNEL"],
                "api_key": cfg["API_KEY"],
            },
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )
        data = resp.json() if resp.content else {}
        if not isinstance(data, dict):
            data = {"raw": str(data)[:200]}
        # Accepted == a message_id came back. Termii also returns
        # message="Successfully Sent", but the id is the load-bearing field: it is what
        # a delivery report later refers to.
        ok = bool(resp.ok and data.get("message_id"))
        if not ok:
            # Most callers deliberately drop this dict (anti-enumeration), so without
            # a log here a rejected SMS is invisible: the customer waits for a code
            # that was never accepted and the operator sees nothing at all. An
            # unapproved Sender ID is the usual cause and says so in `message`.
            log.warning("sms_rejected status=%s reason=%s",
                        resp.status_code, str(data.get("message") or data)[:200])
        return {"success": ok,
                "message_id": str(data.get("message_id") or ""), "raw": data}
    except requests.RequestException as exc:
        return {"success": False, "message": f"SMS provider unreachable: {exc}"}
    except ValueError as exc:                       # non-JSON body (HTML error page)
        return {"success": False, "message": f"SMS provider returned non-JSON: {exc}"}


def send_sms(phone: str, message: str) -> dict:
    """Send one SMS. Blank key => mock success, unchanged: the OTP flow deliberately
    ignores the result (anti-enumeration), so branching on configuration here would
    change nothing for the caller."""
    if not sms_live():
        return {"success": True, "mock": True, "message": "SMS sent (mock mode)"}
    return _send_sms_termii(phone, message)


def send_email(to: str, subject: str, message: str, html: str | None = None,
               attachments: list | None = None) -> dict:
    """Send a transactional email via Resend. Mirrors send_sms's mock-mode
    contract: blank API_KEY or empty `to` returns a silent-success dict so
    callers can fire-and-forget without branching on configuration. Used as a
    parallel OTP channel alongside Termii so SMS routing issues never strand
    a user mid-signup. Pass `html` for a branded body (the plain `message` is
    kept as the text fallback for clients that don't render HTML).

    `attachments` is [{"filename": str, "content": bytes|str}] — used by the NDPR
    data-subject export, which must arrive as a file rather than a body: it is a
    complete personal record, and a mail body is quoted, forwarded and trimmed by
    clients in ways a file is not.
    """
    cfg = settings.RESEND
    if not cfg["API_KEY"] or not to:
        return {"success": True, "mock": True, "message": "Email sent (mock mode)"}
    payload = {"from": cfg["FROM_EMAIL"], "to": [to], "subject": subject, "text": message}
    if html:
        payload["html"] = html
    if attachments:
        import base64

        payload["attachments"] = [
            {"filename": a["filename"],
             "content": base64.b64encode(
                 a["content"].encode() if isinstance(a["content"], str) else a["content"]
             ).decode()}
            for a in attachments
        ]
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
        ok = bool(resp.ok and "id" in data)
        if not ok:
            # Same reasoning as sms_rejected: the reason is thrown away by most
            # callers, so it has to reach the log here or nowhere. Resend's usual
            # rejection is FROM_EMAIL sitting on a domain that is not verified on
            # the account the API key belongs to — "configured" is not "working",
            # and that distinction is exactly what goes unnoticed otherwise. The
            # recipient is not logged; the reason concerns the sender.
            log.warning("email_rejected status=%s from=%s reason=%s",
                        resp.status_code, cfg["FROM_EMAIL"],
                        str(data.get("message") or data.get("error") or data)[:200])
        return {"success": ok, "raw": data}
    except requests.RequestException as exc:
        log.warning("email_unreachable error=%s", str(exc)[:200])
        return {"success": False, "message": f"Email provider unreachable: {exc}"}


def sms_probe(phone: str = "") -> dict:
    """Live self-test for the SMS rail — returns NO secrets.

    Proves the key authenticates and, when a phone is supplied, sends ONE real test
    SMS and returns the provider's raw response — so a non-delivery (unapproved or
    non-whitelisted sender ID, empty provider wallet, bad number) is actually
    visible. The signup flow deliberately hides send failures (anti-enumeration), so
    this is the only place OTP delivery can be observed end to end.
    """
    cfg = settings.TERMII
    config = {"provider": "termii", "base_url": cfg["BASE_URL"],
              "api_key_set": bool(cfg["API_KEY"]), "sender_id": cfg["SENDER_ID"],
              "channel": cfg["CHANNEL"]}
    unset_hint = "TERMII_API_KEY unset — no real SMS sends, so the signup OTP won't deliver."
    fail_hint = (
        f"Termii accepted the request but returned no message_id. Most common causes: "
        f"the sender ID '{cfg['SENDER_ID']}' is not whitelisted for the '{cfg['CHANNEL']}' "
        f"route (DND whitelisting is required to reach most Nigerian numbers), the Termii "
        f"wallet is empty, the number is invalid, or TERMII_BASE_URL is not the host for "
        f"this account.")

    out = {"config": config}
    if not cfg["API_KEY"]:
        out["hint"] = unset_hint
        return out
    if not phone:
        out["hint"] = ("Key present. Add &phone=<number> to send a real test SMS and see the "
                       "delivery status.")
        return out
    res = send_sms(phone, "Zitch test SMS — your OTP delivery is working.")
    out["send"] = {"ok": bool(res.get("success")), "to_normalised": _ng_msisdn(phone),
                   "raw": str(res.get("raw", res.get("message", "")))[:400]}
    if res.get("message_id"):
        out["send"]["message_id"] = res["message_id"]
    if not res.get("success"):
        out["send"]["hint"] = fail_hint
    # Accepted is not delivered. Termii answers "accepted" long before the handset
    # sees anything, and an unapproved sender ID fails at exactly that later step.
    out["note"] = ("ok=true means the provider ACCEPTED the message, not that it was "
                   "delivered. Confirm the handset actually received it.")
    return out


def sender_domain() -> str:
    """The bare domain Resend will be asked to send from.

    FROM_EMAIL is a display-name form ("Zitch <no-reply@send.zitch.ng>"), so the
    domain has to be parsed out rather than string-matched.
    """
    from email.utils import parseaddr

    _, addr = parseaddr(settings.RESEND["FROM_EMAIL"] or "")
    local, at, domain = addr.rpartition("@")
    # rpartition puts the whole string in the tail when the separator is absent,
    # so an address-less value would otherwise come back looking like a domain.
    return domain.strip().lower() if (at and local) else ""


def email_probe() -> dict:
    """Live self-test for the email rail — returns NO secrets.

    Answers the question a key check cannot: is FROM_EMAIL's domain actually
    verified on the account this key belongs to? Resend refuses a send from an
    unverified sender domain, and because every OTP caller drops the result by
    design, that refusal looks exactly like a working rail from the outside —
    the customer is told a code is on its way and simply never receives one.

    Read-only: it lists domains, it does not send.
    """
    cfg = settings.RESEND
    domain = sender_domain()
    out = {"config": {"provider": "resend", "base_url": cfg["BASE_URL"],
                      "api_key_set": bool(cfg["API_KEY"]),
                      "from_email": cfg["FROM_EMAIL"], "sender_domain": domain}}
    if not cfg["API_KEY"]:
        out["hint"] = "RESEND_API_KEY unset — no transactional email, so email OTP won't deliver."
        return out
    if not domain:
        out["ok"] = False
        out["hint"] = "RESEND_FROM_EMAIL has no parseable address — expected 'Name <user@domain>'."
        return out
    try:
        resp = requests.get(f"{cfg['BASE_URL']}/domains",
                            headers={"Authorization": f"Bearer {cfg['API_KEY']}"},
                            timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        out["ok"] = False
        out["hint"] = f"Resend unreachable: {exc}"
        return out
    if resp.status_code in (401, 403):
        out["ok"] = False
        out["hint"] = ("Resend rejected the key itself (%s). It is set but not valid for any "
                       "account — check it was copied whole and has not been revoked."
                       % resp.status_code)
        return out
    try:
        rows = (resp.json() or {}).get("data") or []
    except ValueError:
        rows = []
    verified = sorted({str(r.get("name", "")).lower() for r in rows
                       if str(r.get("status", "")).lower() == "verified"})
    out["verified_domains"] = verified
    out["ok"] = domain in verified
    if not out["ok"]:
        out["hint"] = (
            f"'{domain}' is not a verified domain on the account this API key belongs to, so "
            f"Resend will refuse every send and no email OTP can arrive. "
            + (f"Verified on this account: {', '.join(verified)}. "
               "Either add and verify the sender domain there, or point RESEND_FROM_EMAIL at "
               "one of these / RESEND_API_KEY at the account that owns it."
               if verified else
               "This account has no verified domain at all — add and verify one."))
    return out


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
    """NIN verification entry point.

    Prembly first when it is configured: it has the standalone NIN lookup the
    bank does not. That matters because the NUBAN flow name-matches exactly ONE
    identity — whichever opened the account, in practice the BVN — so without a
    second rail every NIN falls to the operator review queue, and nobody can
    spend until a human clears them.

    Falls back to the Wema behaviour when Prembly is unconfigured, so a deploy
    without those keys behaves exactly as it did before.
    """
    if _prembly_live():
        return prembly_verify_nin(nin, name=name)
    from . import wema
    return wema.verify_nin(nin, name=name)


def prembly_verify_nin(nin: str, name: str = "") -> dict:
    """Look a NIN up at Prembly and name-match the record against `name`.

    Fails CLOSED on every uncertainty — provider down, malformed response, no
    name on the record, or a name that does not match. A NIN that cannot be
    checked must land in the review queue rather than be waved through: this
    result is what lifts a tier and unlocks spending.

    VERIFY-BEFORE-LIVE: confirm the endpoint path and the response field names
    against your Prembly dashboard before trusting this in production. The
    shape below follows their documented NIN response; a mismatch surfaces as
    "could not be confirmed", which is the safe direction but still wrong.
    """
    if len(nin) != 11 or not nin.isdigit():
        return {"success": False, "message": "NIN must be 11 digits"}
    try:
        resp = requests.post(
            f"{settings.PREMBLY['BASE_URL']}/identitypass/verification/nin",
            json={"number": nin}, headers=_prembly_headers(), timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning("prembly_nin_unreachable error_type=%s", type(exc).__name__)
        return {"success": False, "message": f"Identity provider unreachable: {exc}"}

    record = data.get("data") or data.get("nin_data") or {}
    if not (data.get("status") and isinstance(record, dict)):
        return {"success": False, "message": (data.get("message")
                                              or "That NIN could not be confirmed."),
                "raw": data}
    first = str(record.get("firstname") or record.get("first_name") or "").strip()
    last = str(record.get("surname") or record.get("lastname") or record.get("last_name") or "").strip()
    resolved = " ".join(part for part in (first, record.get("middlename") or "", last) if part).strip()
    if name and resolved:
        from transfers.views import _names_match

        if not _names_match(name, resolved):
            # Deliberately does not echo the resolved name: it belongs to
            # whoever owns the NIN, who may not be the person asking.
            log.warning("prembly_nin_name_mismatch")
            return {"success": False, "message": "That NIN does not match the name on this account.",
                    "raw": data}
    elif not resolved:
        return {"success": False, "message": "That NIN could not be confirmed.", "raw": data}
    return {"success": True, "first_name": first, "last_name": last, "raw": data}


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
    => AUTO: use Wema's card rail only when a DEDICATED WEMA_CARD_KEY is set, else the
    generic CARD_ISSUER — so cards never break on a deploy without one.

    Keys off card_opted_in(), not _card_live(): the Wallet Services key authenticates
    Card-Management, but that must not by itself move cards onto the Wema rail, which
    supports neither reversible freeze nor top-up."""
    choice = (getattr(settings, "CARD_PROVIDER", "") or "").strip().lower()
    if choice in ("wema", "issuer"):
        return choice
    from . import wema
    return "wema" if wema.card_opted_in() else "issuer"


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
    # White-label: no provider name in customer-facing copy.
    return {"success": False, "message": "Deposits are credited automatically on receipt."}


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


# ---------------------------------------------------------------------------
# NIP transfer charges / Remita / BNPL — Wema-only rails (no alternative provider).
# The wrappers keep the views off the wema client directly and give tests one seam.
# ---------------------------------------------------------------------------
def payout_charge(amount_naira):
    """The NIP fee a bank transfer of `amount_naira` attracts (naira Decimal), or None
    if the schedule isn't available. Caches the near-static fee schedule for an hour.
    INFORMATIONAL — it does not change what the send flow debits."""
    from django.core.cache import cache

    from . import wema
    charges = cache.get("wema_nip_charges")
    if charges is None:
        res = wema.get_nip_charges()
        if not res.get("success"):
            return None
        charges = res.get("charges", []) or []
        cache.set("wema_nip_charges", charges, 3600)
    return wema.nip_fee_for(amount_naira, charges)


def remita_validate(rrr: str) -> dict:
    """Validate a Remita RRR (read-only)."""
    from . import wema
    return wema.validate_rrr(rrr)


def remita_pay(amount_naira, reference: str, *, rrr: str, source_account: str = "", **kw) -> dict:
    """Pay a Remita RRR debiting the user's NUBAN."""
    from . import wema
    return wema.pay_remita(amount_naira, reference, rrr=rrr, source_account=source_account, **kw)


def bnpl_offers() -> dict:
    """The BNPL product offers a customer is eligible for (read-only)."""
    from . import wema
    return wema.bnpl_offers()
