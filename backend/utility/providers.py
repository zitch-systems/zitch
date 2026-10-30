"""9PSB banking and bill payments, with dedicated identity,
SMS/email and other provider adapters. Production money and identity failures
never become simulated successes.
"""
import hashlib
import logging
import math
import re
import secrets

import requests
from django.conf import settings

REQUEST_TIMEOUT = 30
log = logging.getLogger("zitch")


PARTNERSHIP_ARCHIVED_MESSAGE = (
    "Banking services are temporarily unavailable. New payments are unavailable; "
    "your balance has not been debited."
)


def bank_account_provider() -> str:
    return "9psb"


def partnership_archived() -> bool:
    from banking.client import customer_enabled
    return not customer_enabled()


def partnership_new_business_allowed(user=None) -> bool:
    from banking.client import customer_enabled
    from banking.accounts import require_enabled
    if user is None:
        return customer_enabled()
    try:
        require_enabled(user)
        return True
    except ValueError:
        return False


def _archived_payment_result() -> dict:
    return {"success": False, "message": PARTNERSHIP_ARCHIVED_MESSAGE,
            "code": "partnership_archived", "not_charged": True}


def biller_enabled() -> bool:
    from banking.client import customer_enabled, config, configured
    return customer_enabled() and config().get("VAS_ENABLED") is True and configured("vas")


def _biller_source(reference, *, amount, source_account=""):
    """Resolve an approved debit source; failures never reach a provider."""
    from wallet.services import LimitExceeded, biller_source_for_transaction
    if not biller_enabled():
        return "", {"success": False, "pending": False, "not_charged": True,
                    "code": "biller_unavailable",
                    "message": "Bill payments are temporarily unavailable. You have not been charged."}
    try:
        return biller_source_for_transaction(reference, amount=amount,
            source_account=source_account), None
    except LimitExceeded as exc:
        return "", {"success": False, "pending": False, "not_charged": True,
                    "code": "biller_funding_unavailable", "message": str(exc)}


def _partnership_reference_blocked(reference: str | None = None, source_account: str = "") -> bool:
    if not partnership_new_business_allowed():
        return True
    # A retired funding account is never a Partnership debit source. Customers
    # must pass the same liability check as the locked reservation path.
    from wallet.partnership import retired_funding_destination
    if source_account and retired_funding_destination(source_account):
        return True
    if source_account:
        from wallet.models import Wallet
        wallet = Wallet.objects.select_related("user").filter(account_number=source_account).first()
        if wallet and not partnership_new_business_allowed(wallet.user):
            return True
    if reference:
        from wallet.models import Transaction
        txn = Transaction.objects.select_related("user").filter(reference=reference).first()
        if txn and not partnership_new_business_allowed(txn.user):
            return True
    return False

# ---------------------------------------------------------------------------
# VAS (airtime / data / cable / electricity / betting) - partner bank only.
# ---------------------------------------------------------------------------
# Service ids are intentionally lowercase (for example, "mtn-airtime"), while
# ALAT's Airtime/Data contract expects the operator name values in uppercase.
# Normalise at the provider boundary so every caller sends the same wire value.
_WEMA_NETWORKS = {
    "mtn": "MTN",
    "glo": "GLO",
    "airtel": "AIRTEL",
    "9mobile": "9MOBILE",
}


def _wema_network(service_network: str) -> str:
    value = str(service_network or "").strip()
    return _WEMA_NETWORKS.get(value.casefold(), value.upper())

def simulation_mode() -> bool:
    return False


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
    from banking.client import configured
    return configured("vas")


def vas_provider() -> str:
    return "9psb"


# The product whose status legend decides a purchase of this route type. The
# airtime/data status check and the bills status check answer with DIFFERENT integer
# enums, so they are configured (and gated) separately — see wema._vas_legend.
_SETTLE_PRODUCT = {"airtime": "airtime", "data": "airtime", "bill": "bills"}

#: Shown to the customer when a purchase is refused because this deploy could not
#: settle it. The shared purchase runner checks before debit; the provider boundary
#: repeats the check and refunds a reservation if access has since disappeared.
_VAS_UNAVAILABLE = ("This is temporarily unavailable on our side. You have not been "
                    "charged — please try again later.")


def vas_can_settle(product: str = "airtime") -> tuple[bool, str]:
    if product == "remita":
        return False, "This product requires a documented bank integration"
    return (True, "") if biller_enabled() else (False, "Bill payments are unavailable")


def _wema_vas_route(service_id: str, payload: dict):
    """Resolve how the partner bank would fulfil this purchase, or None to fail closed.

    Returns {"type": "airtime"|"data"|"bill", "code": <wema code>, "amount": <naira>}.
    Airtime always resolves; data/cable resolve once the plan's `wema_code` has been
    synced; electricity/betting resolve once a WemaBiller row maps their service_id.
    A missing code returns None so callers fail closed without routing to a retired provider.

    Electricity and betting take their amount from the request rather than a plan
    row: the customer types it, there is no bundle with a price to read."""
    if service_id.endswith("-airtime"):
        return {"type": "airtime", "code": "", "amount": payload.get("amount")}
    if service_id.endswith("-electric") or service_id.endswith("-betting"):
        from .models import WemaBiller
        meter_type = payload.get("variation_code") if service_id.endswith("-electric") else ""
        if service_id.endswith("-electric") and meter_type not in ("prepaid", "postpaid"):
            return None
        b = (WemaBiller.objects.filter(service_id=service_id, meter_type=meter_type, active=True)
             .only("package_id").first())
        if not (b and b.package_id):
            return None
        amount = payload.get("amount")
        if amount in (None, ""):
            # A variable-amount bill with no amount cannot be paid on either rail;
            # returning None makes the caller fail closed.
            return None
        return {"type": "bill", "code": b.package_id, "amount": amount}
    var = str(payload.get("variation_code", "") or "")
    if not var:
        return None  # no plan code -> safe partner-bank configuration error
    if service_id.endswith("-data"):
        from .models import DataPlan
        p = DataPlan.objects.filter(plan_code=var).only("wema_code", "price").first()
        return {"type": "data", "code": p.wema_code, "amount": p.price} if (p and p.wema_code) else None
    if service_id in ("dstv", "gotv", "startimes"):
        from .models import CablePlan
        p = CablePlan.objects.filter(cable_plan_code=var).only("wema_code", "price").first()
        return {"type": "bill", "code": p.wema_code, "amount": p.price} if (p and p.wema_code) else None
    return None


def vtu_purchase(service_id: str, payload: dict, reference: str | None = None) -> dict:
    return {"success": False, "not_charged": True,
            "message": "Refresh the app to use the current bank bill catalog."}

def vas_retired_rail(meta: dict | None) -> str:
    rail = str((meta or {}).get("vas_rail") or "unknown")
    return "" if rail == "9psb" else rail


# One alert per reference per day. The reconcile cron runs every two minutes, so an
# un-cooled page here would be ~720 identical events a day per stranded row; a daily
# reminder is the right cadence for a row a human has to clear by hand.
_RETIRED_RAIL_ALERT_COOLDOWN = 24 * 60 * 60


def vas_requery(reference: str, meta: dict | None = None) -> dict:
    from banking.adapter import confirm_transfer_status
    return confirm_transfer_status(reference)


def vtu_requery(reference: str) -> dict:
    """Requery a VAS purchase by reference alone, loading the row's meta to do it."""
    from wallet.models import Transaction

    txn = Transaction.objects.filter(reference=reference).only("meta").first()
    return vas_requery(reference, (txn.meta if txn else None) or {})

#: Our cable service_ids, and the CablePlan.provider choice each maps to.
_CABLE_PROVIDER_CHOICE = {"gotv": "1", "dstv": "2", "startimes": "3"}


def _cable_validation_code(service_id: str) -> str:
    """Any mapped ``packageId`` for this cable provider, for validating a smartcard.

    ALAT's ValidateCustomer takes a packageId, but a smartcard is validated BEFORE the
    customer picks a bouquet — that is the whole point of the step, and it is the order
    the app and the WhatsApp flow both use. Routing validation through the normal
    per-plan lookup therefore asked for a bouquet code that does not exist yet and
    failed every cable IUC check with "biller not configured", whatever was mapped.

    Any mapped bouquet of the same provider identifies the same biller to ALAT, which
    is all validation needs, so the cheapest mapped one is used. Still returns "" when
    NOTHING for that provider is mapped, which is a real configuration gap.
    """
    from .models import CablePlan

    choice = _CABLE_PROVIDER_CHOICE.get(service_id, "")
    if not choice:
        return ""
    row = (CablePlan.objects.filter(provider=choice, active=True)
           .exclude(wema_code="").order_by("price").only("wema_code").first())
    return row.wema_code if row else ""


def vtu_verify_customer(service_id: str, billers_code: str, variation: str = "") -> dict:
    return {"success": False, "message": "Use the current bank bill catalog to validate this customer."}


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


def _send_sms_termii(phone: str, message: str, timeout: float = REQUEST_TIMEOUT) -> dict:
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
            timeout=timeout,
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
                "message_id": str(data.get("message_id") or ""), "raw": data,
                "http_status": resp.status_code,
                "uncertain": not ok and (resp.status_code >= 500 or resp.ok),
                "retryable": not ok and 400 <= resp.status_code < 500}
    except requests.RequestException as exc:
        return {"success": False, "uncertain": True,
                "message": f"SMS provider unreachable: {exc}"}
    except ValueError as exc:                       # non-JSON body (HTML error page)
        return {"success": False, "uncertain": True,
                "message": f"SMS provider returned non-JSON: {exc}"}


def send_sms(phone: str, message: str, timeout: float = REQUEST_TIMEOUT) -> dict:
    """Send one SMS. Blank key => mock success, so this return value CANNOT tell
    "delivered to a handset" apart from "silently discarded by an unkeyed deploy".
    A caller that owes someone a real delivery must therefore check sms_live() before
    promising one — the OTP endpoints do exactly that
    (accounts.views._otp_undeliverable), because the signup reply is otherwise a
    cheerful "a code has been sent" over a rail that sent nothing."""
    if not sms_live():
        return {"success": True, "mock": True, "message": "SMS sent (mock mode)"}
    return _send_sms_termii(phone, message, timeout=timeout)


def send_email(to: str, subject: str, message: str, html: str | None = None,
               attachments: list | None = None, timeout: float = REQUEST_TIMEOUT) -> dict:
    """Send a transactional email via Resend. Mirrors send_sms's mock-mode
    contract: blank API_KEY or empty `to` returns a silent-success dict so
    callers can fire-and-forget without branching on configuration. Used as a
    channel for ACCOUNT-RECOVERY codes, which go to the address already on file —
    never for the signup OTP, whose email is caller-supplied and unverified. Pass
    `html` for a branded body (the plain `message` is
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
            timeout=timeout,
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
        return {"success": ok, "raw": data,
                "http_status": resp.status_code,
                "uncertain": not ok and (resp.status_code >= 500 or resp.ok),
                "retryable": not ok and 400 <= resp.status_code < 500}
    except requests.RequestException as exc:
        log.warning("email_unreachable error=%s", str(exc)[:200])
        return {"success": False, "uncertain": True,
                "message": f"Email provider unreachable: {exc}"}
    except (TypeError, ValueError):
        return {"success": False, "uncertain": True,
                "message": "Email provider returned an unreadable response"}


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
# Prembly handles independent BVN/NIN lookup plus supported image/biometric
# checks. Lookup still needs registered-phone ownership proof before enrollment.
# Biometric products retain their separate credential and completion gates.
# ---------------------------------------------------------------------------
def _prembly_live() -> bool:
    # A deploy-wide simulation must not leak real identity data to Prembly just
    # because credentials remain configured for the later go-live.
    return (not simulation_mode()
            and bool(settings.PREMBLY["API_KEY"] and settings.PREMBLY["APP_ID"]))


def _prembly_headers() -> dict:
    return {
        "x-api-key": settings.PREMBLY["API_KEY"],
        "app-id": settings.PREMBLY["APP_ID"],
        "Content-Type": "application/json",
    }


def _prembly_identity_live() -> bool:
    """BVN/NIN Advance documents x-api-key; app-id is optional for this rail."""
    cfg = getattr(settings, "PREMBLY", {}) or {}
    return bool(not simulation_mode() and cfg.get("API_KEY") and cfg.get("BASE_URL"))


def _prembly_identity_headers() -> dict:
    cfg = settings.PREMBLY
    headers = {"x-api-key": cfg["API_KEY"], "Content-Type": "application/json"}
    if cfg.get("APP_ID"):
        headers["app-id"] = cfg["APP_ID"]
    return headers


def _kyc_mock_or_unavailable() -> dict:
    """Return a simulated pass only on an explicitly non-live deployment.

    These checks lift KYC tiers and clear the face step-up on large payments. A
    missing provider in production must therefore be an outage, never a pass.
    ``WEMA_SIMULATION`` remains the deliberate end-to-end test exception.
    """
    if mock_disabled_in_prod():
        return {
            "success": False,
            "message": "Identity verification is temporarily unavailable. Please try again later.",
        }
    return {"success": True, "mock": True}


def kyc_verify_nin_document(*args, **kwargs):
    return {"success": False, "unavailable": True, "message": "Complete verification through the 9PSB identity flow."}


def kyc_verify_face(*args, **kwargs):
    return {"success": False, "unavailable": True, "message": "Complete verification through the 9PSB identity flow."}


def _prembly_liveness_response_passed(data, status_code: int) -> bool:
    """Validate the published Face Liveliness response, never a generic pass.

    Require a completed verification plus consistent numeric confidence in both
    published units. 99% is Zitch's conservative acceptance policy, not a
    provider-recommended threshold. Unknown/incomplete schemas fail closed.
    """
    if status_code != 200 or not _prembly_response_has_no_failure(data, status_code):
        return False
    detail, verification = data.get("data"), data.get("verification")
    if (data.get("response_code") != "00"
            or data.get("endpoint_name") != "Face Liveliness"
            or data.get("detail") != "Liveliness Detected"
            or not isinstance(detail, dict) or not isinstance(verification, dict)
            or verification.get("status") != "VERIFIED"
            or not isinstance(verification.get("reference"), str)
            or not verification["reference"].strip()):
        return False
    confidence = detail.get("confidence")
    percentage = detail.get("confidence_in_percentage")
    # bool is an int in Python; truthy strings and non-finite JSON floats must
    # never become verification. Both units must agree within rounding error.
    return bool(type(confidence) in (int, float) and type(percentage) in (int, float)
                and 0.99 <= confidence <= 1 and 99 <= percentage <= 100
                and math.isclose(confidence * 100, percentage, rel_tol=0, abs_tol=0.01))


def _prembly_response_has_no_failure(data, status_code: int) -> bool:
    """Reject failed/pending transport and envelope signals, without asserting a
    provider-specific verification schema. This predicate only screens failures;
    it cannot authorize KYC. Native liveness also requires its explicit completed
    evidence. Address/document adapters have no confirmed completion contract.
    """
    import re

    if (not 200 <= status_code < 300 or status_code == 202
            or not isinstance(data, dict) or data.get("status") is not True):
        return False
    containers = [data]
    while containers:
        container = containers.pop()
        if isinstance(container, list):
            containers.extend(item for item in container if isinstance(item, (dict, list)))
            continue
        for key, value in container.items():
            name = str(key).replace("_", "").casefold()
            if name in ("data", "result", "response", "verification"):
                if value is not None and not isinstance(value, dict):
                    return False
            if name in ("pending", "failed", "haserror") and value is not False and value is not None:
                return False
            if name in ("errors", "error", "errormessage", "errormessages") and value:
                return False
            is_status = name.endswith("status") or name in ("success", "successful", "verified", "isverified")
            if is_status and value is not True and not isinstance(value, str):
                return False
            if is_status or name in ("message", "detail"):
                if isinstance(value, str) and re.search(
                        r"\b(?:pending|processing|in[ _-]progress|fail(?:ed|ure)?|rejected|declined|"
                        r"false|error|unverified|invalid|not[ _-](?:verified|complete[d]?))\b", value, re.I):
                    return False
            if isinstance(value, (dict, list)):
                containers.append(value)
    return True


def _kyc_unconfirmed_contract() -> dict:
    """A live, unconfirmed proof adapter stays unavailable even with keys set.

    Prembly's published address initiation can return status=true and
    verification.status=VERIFIED while the address job is still unassigned.
    Generic transport/envelope success must never become a Tier 3 grant. The
    supported partner-bank address rail is selected separately by the caller.
    """
    return {"success": False, "unavailable": True,
            "code": "verification_contract_unconfirmed",
            "message": "This verification option is temporarily unavailable. Please try again later."}


def kyc_verify_address(address: str, document: str = "") -> dict:
    """Verify a residential address (Tier 3). MOCK accepts offline.

    LIVE CONTRACT UNCONFIRMED: the endpoint and completed-verification schema
    still need provider confirmation. Envelope validation is only a safety guard.
    """
    if not _prembly_live():
        return _kyc_mock_or_unavailable()
    return _kyc_unconfirmed_contract()


def kyc_verify_id_document(*args, **kwargs):
    return {"success": False, "unavailable": True, "message": "Complete verification through the 9PSB identity flow."}


# ---------------------------------------------------------------------------
# KYC — Partnership Tier 1 and explicit standalone Prembly lookup adapters
#
# Tier 1 ownership uses the bank's account-creation OTP or hosted face flow.
# The generic verify_* entry points cannot select another account integration.
# Explicit Prembly adapters remain separate from Tier 1 routing; an identity
# lookup alone cannot replace bank ownership proof or interactive Tier 2 liveness.
# ---------------------------------------------------------------------------
def kyc_provider() -> str:
    return "9psb"


def verify_bvn(*args, **kwargs):
    return {"success": False, "unavailable": True, "message": "Complete verification through the 9PSB identity flow."}


def prembly_verify_bvn(*args, **kwargs):
    return {"success": False, "unavailable": True, "message": "Complete verification through the 9PSB identity flow."}


def _prembly_identity_lookup(*args, **kwargs):
    return {"success": False, "unavailable": True, "message": "Complete verification through the 9PSB identity flow."}


#: Field names the BVN and NIN records use for the registered line. Both are
#: tried for both identities because the provider's own naming is not stable
#: across products, and a lookup that silently found no phone is indistinguishable
#: from one that found an empty string.
_PHONE_FIELDS = ("phoneNumber1", "phone_number1", "phoneNumber", "phone_number",
                 "telephoneno", "telephone_no", "phone", "msisdn")


def _record_phone(record: dict) -> str:
    """The line registered against the identity, normalised, or "".

    This is the number the OTP goes to — NOT the number on the Zitch account.
    That difference is the entire point: matching a name proves someone knows a
    name, while a code delivered to the line the bank or NIMC holds proves the
    person asking controls it.
    """
    from common.phones import normalize_nigerian_mobile

    for field in _PHONE_FIELDS:
        local = normalize_nigerian_mobile(record.get(field))
        if local:
            return _ng_msisdn(local)
    return ""


#: Field names the BVN and NIN records use for the registered email address.
#: Tried for both identities for the same reason as _PHONE_FIELDS: the provider's
#: naming is not stable across products.
_EMAIL_FIELDS = ("email", "emailAddress", "email_address", "emailaddress")


def _record_email(record: dict) -> str:
    """The email registered against the identity, or "".

    The companion to _record_phone, and it earns its place for the same reason:
    this address belongs to the BVN/NIN HOLDER, so a code sent there proves the
    same thing the SMS does. The Zitch account's own email proves nothing at all
    about who owns the identity — whoever is logged in reads that inbox — so it is
    never a substitute here.

    Often absent: NIMC records rarely carry an email and bank records carry one
    inconsistently. "" is the normal answer, not an error, and the caller falls
    back to SMS alone rather than to the account address.
    """
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email

    for field in _EMAIL_FIELDS:
        raw = record.get(field)
        if not isinstance(raw, str) or any(ord(char) < 32 for char in raw):
            continue
        value = raw.strip().lower()
        if not value or len(value) > 254 or "." not in value.rpartition("@")[2]:
            continue
        try:
            validate_email(value)
        except ValidationError:
            continue
        return value
    return ""


def identity_otp_delivery_payload(phone: str, *, email: str = "",
                                  email_status: str = "not_available") -> dict:
    """Public, masked delivery facts after a real identity SMS was accepted.

    Callers own delivery and must never call this to fabricate acceptance. Email
    is optional and must come from the identity record, not the account profile.
    """
    from common.http import mask_pii

    email = _record_email({"email": email})
    if not email:
        email_status = "not_available"
    elif email_status not in {"accepted", "failed", "unavailable"}:
        email_status = "unavailable"
    channels = ["sms"]
    delivery = f"registered phone •••••{phone[-4:]}"
    if email_status == "accepted":
        channels.append("email")
        delivery += f" and registered email {mask_pii(email)}"
    partial = bool(email and email_status != "accepted")
    out = {"delivery": delivery, "delivery_channels": channels,
           "delivery_status": {"sms": "accepted", "email": email_status},
           "delivery_partial": partial}
    if partial:
        out["delivery_notice"] = (
            "Email delivery was unavailable. Use the code sent to your registered phone.")
    return out


def verify_nin(*args, **kwargs):
    return {"success": False, "unavailable": True, "message": "Complete verification through the 9PSB identity flow."}


def prembly_verify_nin(*args, **kwargs):
    return {"success": False, "unavailable": True, "message": "Complete verification through the 9PSB identity flow."}

def verify_vnin(*args, **kwargs):
    return {"success": False, "unavailable": True, "message": "Complete verification through the 9PSB identity flow."}


# ---------------------------------------------------------------------------
# Card issuer (virtual cards) — provider TBD.  Live calls require an explicit
# feature gate as well as credentials; otherwise dev/test mock and production
# fails closed.
# ---------------------------------------------------------------------------
def card_issuer_live() -> bool:
    # Keep card creation fake while the deploy-wide simulation switch is on,
    # even if the production issuer key is already staged in the environment.
    config = settings.CARD_ISSUER
    return (not simulation_mode()
            and config.get("LIVE_ENABLED") is True
            and bool(config.get("API_KEY") and config.get("BASE_URL")))


# Backward-compatible private name for existing provider tests/callers. New
# diagnostics import the public selector so readiness and dispatch cannot drift.
_card_issuer_live = card_issuer_live


def _card_issuer_headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.CARD_ISSUER['API_KEY']}",
        "Content-Type": "application/json",
    }


def issue_card(holder: str, customer_ref: str) -> dict:
    """Create a virtual card with the issuer. MOCK fabricates presentation data.

    Issuance is non-idempotent.  Transport errors and every unrecognised/accepted
    response are therefore pending, never failed: only an explicit terminal
    rejection permits a fresh customer attempt.
    """
    if not card_issuer_live():
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
        try:
            data = resp.json() if resp.content else {}
        except ValueError:
            data = {}
        data = data if isinstance(data, dict) else {}
        d = data.get("data")
        d = d if isinstance(d, dict) else {}
        raw_status = d.get("status") or data.get("status") or ""
        status = str(raw_status).strip().lower()
        token = str(d.get("_id") or d.get("id") or "").strip()
        expiry = str(d.get("expiry") or "").strip()
        if not expiry:
            month = str(d.get("expiryMonth") or "").strip()
            year = str(d.get("expiryYear") or "").strip()
            expiry = f"{month.zfill(2)}/{year[-2:]}" if month and year else ""
        base = {
            "status": status,
            "provider_reference": str(d.get("reference") or token)[:100],
            "raw": data,
        }
        if resp.status_code in (202, 408, 409, 425, 429) or resp.status_code >= 500:
            return {**base, "success": False, "pending": True,
                    "message": "Card issuer outcome is not yet confirmed"}

        terminal_failure = (
            status in {
                "failed", "failure", "declined", "rejected", "cancelled", "canceled",
            }
            or data.get("success") is False
            or data.get("status") is False
            or d.get("success") is False
            or d.get("status") is False
        )
        if terminal_failure or not resp.ok:
            return {**base, "success": False,
                    "message": str(data.get("message") or "Card creation failed")[:300]}

        explicit_success = (
            status in {"success", "successful", "completed", "complete", "issued"}
            or data.get("success") is True
            or data.get("status") is True
        )
        if explicit_success and token:
            pan = str(d.get("maskedPan") or d.get("number") or "")
            return {
                **base,
                "success": True,
                "card_token": token,
                "brand": d.get("brand", settings.CARD_ISSUER.get("BRAND", "Verve")),
                "last4": pan[-4:],
                "expiry": expiry,
            }
        return {**base, "success": False, "pending": True,
                "message": "Card issuer accepted the request; final outcome is unconfirmed"}
    except requests.RequestException:
        return {"success": False, "pending": True,
                "message": "Card issuer outcome is not yet confirmed"}


def set_card_status(card_token: str, active: bool) -> dict:
    """Freeze/unfreeze a card with the generic issuer.

    A state-changing request can have been applied even when its response is
    lost.  Only explicit terminal evidence updates our local card projection;
    transport/gateway failures and bare 2xx acceptance remain pending.
    """
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
        try:
            data = resp.json() if resp.content else {}
        except ValueError:
            data = {}
        data = data if isinstance(data, dict) else {}
        payload = data.get("data")
        payload = payload if isinstance(payload, dict) else {}
        raw_status = payload.get("status") or data.get("status") or ""
        status = str(raw_status).strip().lower()
        base = {"status": status, "raw": data}
        if resp.status_code in (202, 408, 409, 425, 429) or resp.status_code >= 500:
            return {**base, "success": False, "pending": True,
                    "message": "Card status outcome is not yet confirmed"}

        terminal_failure = (
            status in {
                "failed", "failure", "declined", "rejected", "cancelled", "canceled",
            }
            or data.get("success") is False
            or data.get("status") is False
            or payload.get("success") is False
            or payload.get("status") is False
        )
        if terminal_failure or not resp.ok:
            return {**base, "success": False,
                    "message": str(data.get("message") or "Could not update card")[:300]}

        expected_states = ({"active", "unfrozen", "enabled"} if active else
                           {"inactive", "frozen", "blocked", "disabled", "hotlisted"})
        explicit_success = (
            data.get("success") is True
            or data.get("status") is True
            or payload.get("success") is True
            or status in expected_states
        )
        if explicit_success:
            return {**base, "success": True}
        return {**base, "success": False, "pending": True,
                "message": "Card issuer accepted the request; final status is unconfirmed"}
    except requests.RequestException as exc:
        # The issuer may have applied the state change before the response was
        # lost. Keep our local state unchanged and report the ambiguity.
        return {"success": False, "pending": True,
                "message": f"Card issuer outcome unconfirmed: {exc}"}


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
    from common.products import MESSAGES, product_available
    if not product_available("card_funding"):
        return {"success": False, "message": MESSAGES["card_funding"],
                "code": "product_unavailable"}
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
        try:
            data = resp.json() if resp.content else {}
        except ValueError:
            data = {}
        payload = data.get("data") if isinstance(data, dict) else {}
        if not isinstance(payload, dict):
            payload = {}
        raw_status = (payload.get("status") or data.get("status")
                      if isinstance(data, dict) else "")
        status = str(raw_status or "").strip().lower()
        provider_reference = str(
            payload.get("reference") or payload.get("id")
            or (data.get("reference") if isinstance(data, dict) else "") or ""
        )
        base = {"status": status, "provider_reference": provider_reference,
                "raw": data}
        if resp.status_code in (202, 408, 409, 425, 429) or resp.status_code >= 500:
            return {**base, "success": False, "pending": True,
                    "message": "Card issuer outcome is not yet confirmed"}
        if status in {"success", "successful", "completed", "complete", "funded"}:
            return {**base, "success": True}
        if status in {"failed", "failure", "declined", "rejected", "cancelled", "canceled"}:
            return {**base, "success": False,
                    "message": str(data.get("message") or "Card funding failed")[:300]}
        # This integration has no documented response contract in the repository.
        # A bare 2xx proves only HTTP acceptance, not that the non-idempotent load
        # completed. Hold every unrecognised response for checked reconciliation.
        if resp.ok:
            return {**base, "success": False, "pending": True,
                    "message": "Card issuer accepted the request; final outcome is unconfirmed"}
        return {**base, "success": False,
                "message": str(data.get("message") or "Card funding failed")[:300]}
    except requests.RequestException as exc:
        # This POST is non-idempotent and may have loaded the card before the
        # response was lost.  A definitive failure would make the caller refund
        # the wallet and create value on both sides.  Hold the debit until issuer
        # evidence resolves the outcome.
        return {"success": False, "pending": True,
                "message": f"Card issuer outcome unconfirmed: {exc}"}


# --------------------------------------------------------------------------- #
# Fincra — FX conversion rail (multi-currency). MOCK mode when no secret key:
# deterministic mid-market rates + auto-settle, so the flow is testable offline.
# --------------------------------------------------------------------------- #
def fincra_live() -> bool:
    # A simulated conversion must use the deterministic mock rate and must not
    # execute at Fincra merely because credentials are present.
    return not simulation_mode() and bool(settings.FINCRA.get("SECRET_KEY"))


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
# Money-movement rail — partner bank / ALAT (funding / virtual accounts / payouts)
#
# The funding_* / payout_* wrappers are the provider-agnostic contract the views
# and services call; they delegate to the partner bank client (utility.wema), the sole
# money-movement rail. partner bank funds by bank transfer to an OTP-provisioned NUBAN (no
# hosted checkout, no webhook — inbound deposits AND payout settlement are handled
# by the reconcile_wema poller). The *_provider() selectors are retained (returning
# "wema") so any remaining callers/diagnostics keep working.
# ---------------------------------------------------------------------------
def _wema_live() -> bool:
    from . import wema
    return wema.wema_live()


def payment_provider() -> str:
    return "9psb"


def payout_provider() -> str:
    return "9psb"


def payout_live() -> bool:
    from banking.client import customer_enabled
    return customer_enabled()


def card_provider() -> str:
    return "issuer"


def card_capabilities(provider: str = "") -> dict:
    """Return customer-facing operations supported by a card backend.

    Wema's card-management product funds only during issuance and its hotlist
    operation is permanent.  The generic issuer retains reversible status and
    incremental funding support.
    """
    backend = provider if provider in ("wema", "issuer") else card_provider()
    if backend == "wema":
        return {
            "can_fund": False,
            "can_unfreeze": False,
            "permanent_block": True,
        }
    from common.products import product_available
    return {
        "can_fund": product_available("card_funding"),
        "can_unfreeze": True,
        "permanent_block": False,
    }


def _card_backend(provider: str = "") -> str:
    return provider if provider in ("wema", "issuer") else card_provider()


# --- Funding (wallet top-up) dispatch — partner bank (OTP-provisioned NUBAN) ---
def funding_initialize(email: str, amount_naira, reference: str, *,
                       name: str = "", redirect_url: str = "") -> dict:
    """partner bank/ALAT has no hosted checkout — funding is by bank transfer to the user's
    dedicated NUBAN (credited by the reconcile_wema poller), so there is no charge to
    start. Returns a graceful message the app shows instead of a checkout URL."""
    return {"success": False,
            "message": "Top up by bank transfer to your dedicated account number."}


def funding_verify(reference: str, provider: str = "") -> dict:
    """partner bank deposits are credited by the reconcile poller, not a synchronous verify
    call, so there is nothing to confirm here."""
    # White-label: no provider name in customer-facing copy.
    return {"success": False, "message": "Deposits are credited automatically on receipt."}


def funding_account_reserve(account_reference: str, account_name: str, customer_email: str,
                            customer_name: str, bvn: str = "", nin: str = "") -> dict:
    """Provision a dedicated funding (virtual) account.

    partner bank can't mint an account synchronously — it needs a BVN/NIN + OTP round-trip
    driven by the /api/wallet/wema/* endpoints. Signal that so ensure_reserved_account
    leaves the wallet numberless (the OTP flow fills it) rather than surfacing a hard
    error.
    """
    return {"success": False, "otp_required": True,
            "message": "Verify the OTP to finish setting up your account."}


def funding_account_get(account_reference: str) -> dict:
    """Fetch an existing dedicated account (duplicate recovery).

    partner bank accounts are provisioned by the OTP endpoints, not a synchronous lookup, so
    this signals otp_required rather than performing a wrong-rail lookup."""
    return {"success": False, "otp_required": True,
            "message": "Verify the OTP to finish setting up your account."}


# --- Payout (bank transfer) dispatch — partner bank ---
def payout_resolve_account(account_number: str, bank_code: str) -> dict:
    from banking.adapter import resolve_account
    return resolve_account(account_number, bank_code)


def payout_send(amount_naira, reference: str, narration: str, bank_code: str,
                account_number: str, account_name: str, bank_name: str = "", source_account: str = "") -> dict:
    from banking.adapter import transfer
    return transfer(amount_naira, reference, narration, source_account=source_account,
                    destination_account=account_number, destination_bank_code=bank_code,
                    destination_bank_name=bank_name, destination_name=account_name)


# --- Virtual card dispatch ---
# Two card backends: the generic CARD_ISSUER (default) and ALAT Card-Management
# (NUBAN-keyed; selected when card_provider() is "wema" — see wema.card_*). When no
# backend is configured the calls mock in dev/test and fail closed in production
# (see issue_card / card_secure_details).
def card_issue(holder: str, customer_ref: str, email: str = "", *, account_number: str = "",
               phone: str = "") -> dict:
    # The new partnership's card-acquiring contract has not been supplied.
    # Do not fall through to a different issuer after the provider switch.
    return {"success": False, "pending": False, "message": "Card services are temporarily unavailable."}


def card_set_status(card_token: str, active: bool, *, provider: str = "",
                    masked_pan: str = "") -> dict:
    if _card_backend(provider) == "wema":
        from . import wema
        if masked_pan:
            return wema.card_set_status(card_token, active, masked_pan=masked_pan)
        return wema.card_set_status(card_token, active)
    return set_card_status(card_token, active)


def card_fund(card_token: str, amount, *, provider: str = "") -> dict:
    if _card_backend(provider) == "wema":
        from . import wema
        return wema.card_fund(card_token, amount)
    return fund_card(card_token, amount)


def card_reveal(card_token: str, *, provider: str = "") -> dict:
    if _card_backend(provider) == "wema":
        from . import wema
        return wema.card_reveal(card_token)
    return card_secure_details(card_token)


# ---------------------------------------------------------------------------
# NIP transfer charges / Remita / BNPL — partner bank-only rails (no alternative provider).
# The wrappers keep the views off the wema client directly and give tests one seam.
# ---------------------------------------------------------------------------
def payout_charge(amount_naira):
    from banking.fees import transfer_fee
    return transfer_fee(amount_naira)


def remita_validate(rrr: str) -> dict:
    """Validate a Remita RRR (read-only)."""
    from . import wema
    return wema.validate_rrr(rrr)


def remita_pay(amount_naira, reference: str, *, rrr: str, source_account: str = "", **kw) -> dict:
    """Pay a Remita RRR using the debit's approved biller source."""
    from . import wema
    source, source_error = _biller_source(reference, amount=amount_naira, source_account=source_account)
    if source_error:
        return source_error
    can_settle, _reason = vas_can_settle("remita")
    if not can_settle:
        return {"success": False, "pending": False, "not_charged": True,
                "code": "remita_unavailable", "message": _VAS_UNAVAILABLE}
    return wema.pay_remita(amount_naira, reference, rrr=rrr, source_account=source, **kw)


def bnpl_offers() -> dict:
    """The BNPL product offers a customer is eligible for (read-only)."""
    from . import wema
    return wema.bnpl_offers()
