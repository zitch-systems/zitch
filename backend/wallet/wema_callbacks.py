"""Wema / ALAT bank-called webhooks.

ALAT requires the partner to PROFILE callback URLs with the bank BEFORE the rails
work at all — its integration guide states the Account Creation Callback URL must be
configured "before initiating wallet creation requests", and that an unprofiled
Authentication Callback URL produces authentication-failed errors on transactions.

Three are profiled for dev, four for production:
  account       — the created NUBAN is delivered here (requestType 2)
  authorize     — the bank ASKS US whether a payout may proceed; we answer
                  {transactionReference, authorized}
  transaction   — debit/credit status updates (requestType 3)
  notification  — production-only real-time notifications (payload undocumented)

SECURITY MODEL. ALAT signs nothing, so these endpoints stack what is available:
a secret in the URL path and a source-IP allowlist against the bank's published
egress addresses, both applied BEFORE the body is parsed.

The allowlist compares `_callback_source_ip()`, NOT the shared `client_ip()`: a fixed
trusted-proxy hop count resolves to this platform's own internal address (every bank
callback has been observed arriving as 10.30.1.250), which can never match a bank
egress IP — so enforcement would refuse every genuine callback while looking correctly
configured. See `_callback_source_ip` for how the caller is recovered safely through
the Render and Cloudflare proxy chain.

There is deliberately NO per-IP rate limit. The shared rate limiter still buckets on
`client_ip()`, which on this deployment is that same platform-internal address for all
bank traffic — so a per-IP bucket would be shared by every callback rather than
isolating an attacker, throttling real callbacks while bounding nobody. The cost of
abuse is bounded per-reference instead (see the requery cooldown below). On top of
that, neither money-moving handler trusts its payload:

  * `authorize` answers true only when OUR OWN ledger already holds a fresh PENDING
    bank payout under that exact reference. Possessing the URL is not sufficient.
  * `transaction` treats the callback as a TRIGGER, not an oracle: it re-queries the
    status over the authenticated APIM channel and lets the existing reconcile logic
    decide. A forged callback is therefore at worst an unauthorised requery.
  * NOTHING here credits a wallet. The requestType-3 payload carries no amount and no
    account number; a credit path under a no-signature trust model would be a
    money-printing primitive.

Failure is always closed: any error answers "not authorized" / changes no state.
"""
import hashlib
import hmac
import ipaddress
import json
import logging
import re
from functools import wraps

from django.db import IntegrityError, transaction as db_transaction
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt

from common.http import mask_pii
from utility import wema as wema_provider
from utility.alerts import alert

from .models import Transaction
from .services import is_bank_payout, provision_wema_account, settle_or_refund

log = logging.getLogger("zitch.security")

# Wema's published gateway egress addresses — the source of every callback.
DEFAULT_CALLBACK_IPS = ("135.236.18.76", "74.178.162.156")

# Cloudflare publishes these networks at https://www.cloudflare.com/ips/.  The
# custom API hostname is proxied by Cloudflare and Render appends the Cloudflare
# edge to X-Forwarded-For.  CF-Connecting-IP is therefore useful only after that
# adjacent public hop has been authenticated as Cloudflare; trusting the header
# unconditionally would let a direct caller forge a bank allowlisted address.
_CLOUDFLARE_PROXY_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
    "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
    "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
    "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32",
    "2405:8100::/32", "2a06:98c0::/29", "2c0f:f248::/32",
))

_REF_MAX = 64          # Transaction.reference is max_length=64
_SECURITY_INFO_MAX = 4096
CALLBACK_BODY_MAX = 64 * 1024

# Shortest gap between two BANK requeries for the same reference. Without it this
# endpoint is a 1:1 amplifier into ALAT's own API — one inbound callback, one
# outbound confirm_transfer_status — so anyone holding the URL can drive unbounded
# traffic against the bank in our name and burn our API quota. A second callback for
# the same reference inside the window carries no information the first didn't, so
# skipping the requery costs nothing: the row is still recorded, and the reconcile
# poller remains the backstop.
REQUERY_COOLDOWN = 30  # seconds

# Authorisation denials that are NORMAL bank behaviour rather than a signal. ALAT
# retries, so a payout we already settled gets asked about again — alerting on those
# pages an operator for the protocol working correctly, and drowns the reasons that
# do matter (an unknown reference, a non-payout reference, a securityInfo mismatch).
_BENIGN_DENIALS = {"state_Successful", "state_Failed", "stale"}


def _conf(key, default=None):
    from django.conf import settings
    return (settings.WEMA or {}).get(key, default)


def _fingerprint(value: str) -> str:
    """Non-reversible marker for an opaque credential, safe to log/audit."""
    if not value:
        return ""
    return f"sha256:{hashlib.sha256(value.encode()).hexdigest()[:12]}/{len(value)}"


def _token_ok(supplied: str) -> bool:
    """Constant-time compare against the configured token (and the previous one, so a
    rotation has an overlap window). Whitespace is stripped on both sides because a
    pasted dashboard value routinely carries a trailing newline.

    With NO token configured: allowed only in local development/tests and refused
    on every deployed host, including simulation. Fake balances and KYC state are
    still customer data and cannot be left publicly attacker-mutable.
    """
    current = (_conf("CALLBACK_TOKEN", "") or "").strip()
    previous = (_conf("CALLBACK_TOKEN_PREV", "") or "").strip()
    if not current and not previous:
        from django.conf import settings

        return bool(getattr(settings, "DEBUG", False) or getattr(settings, "TESTING", False))
    supplied = (supplied or "").strip()
    return any(hmac.compare_digest(supplied, known) for known in (current, previous) if known)


def _callback_source_ip(request) -> str:
    """The caller's own address, for CALLBACK AUTHENTICATION only: the right-most
    PUBLIC entry in X-Forwarded-For, falling back to REMOTE_ADDR.

    Deliberately NOT client_ip(). That helper takes a fixed number of hops from the
    right, and on this platform the entries to the right of the caller are the host's
    own internal addresses — every bank callback has been observed arriving as
    10.30.1.250 under RATELIMIT_TRUSTED_PROXY_HOPS=1. Compared against a list of bank
    egress IPs that private address can never match, so enforcement refuses every
    genuine callback while the configuration looks perfectly correct. A hop count also
    has to be re-tuned by hand whenever the platform changes its topology, and when it
    is wrong it fails silently and closed — which on the account-creation route means
    customers simply never get a NUBAN.

    Right-most PUBLIC is normally not spoofable the way left-most is. A caller can
    only PREPEND to this header; every trusted hop APPENDS. So a forged
    ``X-Forwarded-For: 135.236.18.76`` arrives as
    ``135.236.18.76, <caller's real address>, 10.30.1.250`` and the caller's real
    address still wins the scan. Reading left-most, by contrast, would hand the
    allowlist to anyone who can set a header. On the custom domain the right-most
    public hop is Cloudflare itself, so CF-Connecting-IP is accepted only after that
    hop is verified against Cloudflare's published networks.
    """
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "") or ""
    public_chain = []
    for part in [p.strip() for p in xff.split(",") if p.strip()]:
        try:
            addr = ipaddress.ip_address(part)
        except ValueError:
            continue
        if addr.is_global:
            public_chain.append(addr)

    if public_chain:
        adjacent_proxy = public_chain[-1]
        if any(adjacent_proxy in network for network in _CLOUDFLARE_PROXY_NETWORKS):
            connecting = (request.META.get("HTTP_CF_CONNECTING_IP", "") or "").strip()
            try:
                caller = ipaddress.ip_address(connecting)
            except ValueError:
                caller = None
            if caller is not None and caller.is_global:
                return str(caller)
        return str(adjacent_proxy)
    remote = (request.META.get("REMOTE_ADDR", "") or "").strip()
    try:
        return str(ipaddress.ip_address(remote))
    except ValueError:
        return "unknown"


def _face_browser_origin(request) -> str:
    """Return an exact allowed hosted-verifier origin, or an empty string.

    Origin is only a transport signal. It is trivial to spoof outside a browser, so
    the face handler must validate the returned correlation ID with Wema's
    authenticated API before changing KYC state.
    """
    origin = (request.META.get("HTTP_ORIGIN", "") or "").strip().rstrip("/")
    allowed = {str(value).strip().rstrip("/")
               for value in (_conf("FACE_CALLBACK_ORIGINS") or ()) if str(value).strip()}
    return origin if origin and origin in allowed else ""


def _ip_ok(request, kind: str = "") -> tuple:
    """(allowed, ip). The address is resolved by _callback_source_ip — see there for
    why a fixed trusted-proxy hop count cannot work for this comparison.
    """
    from django.conf import settings
    ip = _callback_source_ip(request)
    if getattr(settings, "DEBUG", False) or getattr(settings, "TESTING", False):
        return True, ip
    if kind == "face":
        # Wema may deliver this callback server-to-server from its published IPs or
        # from the hosted verifier running in the customer's browser. Browser origin
        # permits transport only; the handler independently proves its correlation
        # against Wema before it grants identity verification.
        server_ip = ip in set(_conf("FACE_CALLBACK_IPS") or ())
        return (server_ip or bool(_face_browser_origin(request))), ip
    if wema_provider.wema_simulation() or not _conf("CALLBACK_ENFORCE_IPS", False):
        return True, ip
    allowed = set(_conf("CALLBACK_IPS") or DEFAULT_CALLBACK_IPS)
    return (ip in allowed), ip


def wema_callback(kind: str, token_required: bool = True):
    """csrf-exempt, POST-only, IP authenticated, JSON-parsed — and token
    authenticated unless `token_required` is False.

    Order is load-bearing: transport auth runs before the body is parsed or anything
    from it is logged, so an unauthenticated flood can't write attacker-controlled
    text into our logs or audit trail.
    """
    def outer(view):
        @csrf_exempt
        @wraps(view)
        def inner(request, token="", *args, **kwargs):
            from whatsapp.models import WebhookEvent
            from whatsapp.ops import record_webhook

            source = f"wema.{kind}"
            # A refused call is recorded with NO body: the point of refusing before
            # parsing is that nothing attacker-controlled gets written anywhere, and
            # persisting it to a table operators read would undo that. The IP and the
            # outcome are what an investigation needs, and they are not attacker-chosen.
            if request.method != "POST":
                record_webhook(source, outcome=WebhookEvent.REJECTED_METHOD,
                               http_status=405, remote_ip=_callback_source_ip(request))
                return JsonResponse({"message": "Method not allowed"}, status=405)
            if token_required and not _token_ok(token):
                refused_from = _callback_source_ip(request)
                log.warning("wema_cb_bad_token kind=%s ip=%s", kind, refused_from)
                record_webhook(source, outcome=WebhookEvent.REJECTED_TOKEN,
                               http_status=403, remote_ip=refused_from)
                return JsonResponse({"message": "Forbidden"}, status=403)
            allowed, ip = _ip_ok(request, kind)
            if kind == "face":
                request.wema_face_server_trusted = (
                    ip in set(_conf("FACE_CALLBACK_IPS") or ())
                )
                request.wema_face_browser_origin = _face_browser_origin(request)
            if not allowed:
                log.warning("wema_cb_bad_ip kind=%s ip=%s", kind, ip)
                alert("Wema callback from unexpected source IP", level="warning",
                      kind=kind, ip=ip)
                record_webhook(source, outcome=WebhookEvent.REJECTED_IP,
                               http_status=403, remote_ip=ip)
                return JsonResponse({"message": "Forbidden"}, status=403)
            try:
                declared_size = int(request.META.get("CONTENT_LENGTH") or 0)
            except (TypeError, ValueError):
                declared_size = 0
            if declared_size > CALLBACK_BODY_MAX:
                record_webhook(source, outcome=WebhookEvent.BAD_BODY, verified=True,
                               http_status=413, remote_ip=ip, action="body_too_large")
                return JsonResponse({"message": "Payload too large"}, status=413)
            try:
                raw_body = request.body or b"{}"
                if len(raw_body) > CALLBACK_BODY_MAX:
                    record_webhook(source, outcome=WebhookEvent.BAD_BODY, verified=True,
                                   http_status=413, remote_ip=ip, action="body_too_large")
                    return JsonResponse({"message": "Payload too large"}, status=413)
                body = json.loads(raw_body)
                if not isinstance(body, dict):
                    body = {}
                parsed = True
            except (ValueError, UnicodeDecodeError):
                log.warning("wema_cb_bad_json kind=%s ip=%s", kind, ip)
                body = {}
                parsed = False
            request.wema_body = body
            request.wema_ip = ip
            request.wema_action = ""
            # Written in `finally` so a handler that RAISES still leaves evidence the
            # call arrived — the case where evidence matters most, and the one a
            # record-on-success-only log loses. The row is immutable, so it is written
            # once, at the end, when both the status the bank saw and whatever the
            # handler decided are known.
            status = 500
            try:
                response = view(request, *args, **kwargs)
                status = getattr(response, "status_code", 200)
                return response
            except Exception:
                request.wema_action = "handler_error"
                raise
            finally:
                record_webhook(
                    source,
                    outcome=WebhookEvent.ACCEPTED if parsed else WebhookEvent.BAD_BODY,
                    verified=True, http_status=status, remote_ip=ip, payload=body,
                    reference=_callback_reference(body),
                    action=getattr(request, "wema_action", ""))
        return inner
    return outer


def _callback_reference(body: dict) -> str:
    """The correlation key from a callback envelope, for looking events up later.

    ALAT spells it differently per callback (and nests some under ``data``), so this
    tries the known spellings in order rather than assuming one shape. Empty when
    none is present — a missing reference is not worth failing a callback over.
    """
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    for key in ("transactionReference", "transactionRef", "reference",
                "customTransactionReference", "nuban", "accountNumber"):
        for holder in (body, data):
            value = holder.get(key)
            if value:
                return (_fingerprint(str(value)) if key in ("nuban", "accountNumber")
                        else str(value))
    return ""


# ---------------------------------------------------------------------------
# 1. Account Creation callback (requestType 2)
# ---------------------------------------------------------------------------
@wema_callback("account")
def wema_account_callback(request):
    """The bank delivers the freshly minted NUBAN here.

    Payload: {title, message, data: {email, nuban, nubanName, phoneNumber,
    nubanStatus, type}, requestType: 2}.

    Provisions the wallet idempotently. It deliberately does NOT lift the user's KYC
    tier: that requires the name-match control in the OTP flow, which runs when the
    user completes verification. Always answers 200 — a non-2xx invites the bank to
    retry an event we have already recorded.
    """
    body = request.wema_body
    # Treat a malformed ``data`` value as an empty object.  The callback is an
    # untrusted network boundary; calling ``.get`` on a list/string here would turn
    # a bad bank payload into a 500 and retry storm instead of quarantining it.
    raw_data = body.get("data")
    data = raw_data if isinstance(raw_data, dict) else {}
    nuban = str(data.get("nuban") or "").strip()
    phone = str(data.get("phoneNumber") or "").strip()
    email = str(data.get("email") or "").strip()
    name = str(data.get("nubanName") or "").strip()
    status = str(data.get("nubanStatus") or "").strip()

    valid_schema = (
        str(body.get("requestType") or "").strip() == "2"
        and isinstance(raw_data, dict)
        and re.fullmatch(r"\d{10}", nuban) is not None
        and str(data.get("type") or "").strip() == "1"
        and status.lower() == "active"
        and bool(phone or email)
    )
    if not valid_schema:
        request.wema_action = "quarantined:invalid_schema"
        log.warning("wema_account_cb_invalid_schema ip=%s request_type=%r data_keys=%s",
                    request.wema_ip, body.get("requestType"),
                    sorted(data) if isinstance(data, dict) else [])
        alert("Invalid Wema account callback quarantined", level="warning",
              request_type=str(body.get("requestType") or ""), has_nuban=bool(nuban),
              bank_status=status[:32])
        return JsonResponse({"status": True}, status=200)

    user = _resolve_user(phone=phone, email=email)
    if user is None:
        # Never guess. An unmatched NUBAN is a real operational event — the account
        # exists at the bank and money can land in it — so alert rather than drop.
        log.warning("wema_account_cb_no_user nuban=%s ip=%s", mask_pii(nuban), request.wema_ip)
        alert("Wema account callback for an unknown customer", level="warning",
              nuban_fingerprint=_fingerprint(nuban), has_phone=bool(phone), has_email=bool(email))
        return JsonResponse({"status": True}, status=200)

    wallet, outcome = provision_wema_account(
        user, account_number=nuban, account_name=name, bank_name="Wema Bank",
        source="callback")
    if outcome.startswith("conflict"):
        log.warning("wema_account_cb_conflict user=%s nuban=%s outcome=%s",
                    user.id, mask_pii(nuban), outcome)
        alert("Wema account callback conflicted with an existing wallet",
              level="error", user_id=user.id, nuban_fingerprint=_fingerprint(nuban),
              outcome=outcome)
        return JsonResponse({"status": True}, status=200)

    if outcome == "provisioned":
        # Best-effort PND lift: ALAT places a Post-No-Debit hold on a new Tier-1
        # NUBAN, so it can receive but not send until lifted. A failure here still
        # leaves a usable funding account; the OTP flow and poller retry it.
        #
        # "Best-effort" has to mean it too. lift_debit_restriction only catches
        # RequestException, so anything else — a gateway body in an unexpected shape,
        # a bug in this path — escaped and 500'd a callback whose real work (the
        # wallet above) had ALREADY succeeded. The bank then sees a failed delivery
        # for an account it created, and retries something that cannot get better.
        # Swallow broadly and let the poller retry the lift: acknowledging the
        # provisioning matters more than reporting a hold we can fix later.
        try:
            lifted = bool(wema_provider.lift_debit_restriction(nuban).get("success"))
        except Exception:                        # noqa: BLE001 — see above
            log.exception("wema_pnd_lift_error_cb user=%s account=%s",
                          user.id, mask_pii(nuban))
            lifted = False
        if lifted:
            wallet.pnd_lifted = True
            wallet.save(update_fields=["pnd_lifted", "updated"])
        else:
            log.warning("wema_pnd_lift_failed_cb user=%s account=%s",
                        user.id, mask_pii(nuban))

    log.info("wema_account_cb user=%s nuban=%s outcome=%s bank_status=%s",
             user.id, mask_pii(nuban), outcome, status)
    return JsonResponse({"status": True}, status=200)


def _resolve_user(*, phone: str = "", email: str = ""):
    """Find the customer a callback refers to. Returns None rather than guessing.

    Phone is authoritative (it is unique on the user) and is tried in every Nigerian
    spelling, since the bank sends local 070... form while we may hold +234...
    Email is only a fallback and only when it identifies EXACTLY ONE user: the field
    is not unique, so a `.first()` on a duplicated address could attach a bank account
    to the wrong customer.
    """
    from accounts.models import User
    from utility.providers import _ng_msisdn
    if phone:
        digits = "".join(ch for ch in phone if ch.isdigit())
        local = "0" + digits[-10:] if len(digits) >= 10 else ""
        for value in {v for v in (phone, digits, local, _ng_msisdn(phone)) if v}:
            user = User.objects.filter(phone=value).first()
            if user is not None:
                return user
    if email:
        matches = list(User.objects.filter(email__iexact=email)[:2])
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            log.warning("wema_cb_ambiguous_email matches=%d", len(matches))
    return None


# ---------------------------------------------------------------------------
# 2. Authentication callback — the payout authorisation gate
# ---------------------------------------------------------------------------
def _auth_callback_fields(body: dict) -> tuple:
    """(transactionReference, securityInfo) from an authentication callback.

    ALAT does not send the same envelope shape on every callback, and the
    transaction callback already had to grow ``_transaction_callback_data`` because
    production payloads arrived with the reference nested under ``data`` (or a
    JSON-string ``data``) rather than at the top level. The authentication callback
    is the same bank on the same rail, so it can nest the same way — and a
    reference we fail to find here denies a perfectly good payout as
    ``no_reference``, which ALAT surfaces to the customer as the generic
    "Authentication Failed". So look in the same places, and read ``securityInfo``
    from whichever object carried the reference (falling back to the top level for
    a mixed shape). Widening where we READ the fields changes nothing about the
    decision: `_authorize_payout` still gates on our own PENDING ledger row.
    """
    holder = _transaction_callback_data(body) or {}

    def pick(key: str) -> str:
        return str(holder.get(key) or body.get(key) or "")

    return pick("transactionReference").strip(), pick("securityInfo")


@wema_callback("auth")
def wema_authenticate_callback(request):
    """The bank asks whether a payout may proceed; we answer authorized true/false.

    This is the only endpoint whose response moves money, so the decision rests on
    OUR ledger, not on the request: we authorise only a payout we ourselves put in
    flight moments ago and which is still PENDING. An attacker holding the URL and
    calling from an allowed address still cannot authorise anything.

    Every refusal returns the identical body — no message, no detail — so the endpoint
    is not an oracle for "is a payout in flight right now".

    Always 200: the bank parses the body, not the status code.
    """
    body = request.wema_body
    ref_raw, security_info_raw = _auth_callback_fields(body)
    ref = ref_raw[:_REF_MAX]
    security_info = security_info_raw[:_SECURITY_INFO_MAX]

    authorized = False
    reason = "denied"
    try:
        authorized, reason = _authorize_payout(ref, security_info, request.wema_ip)
    except Exception as exc:                                  # noqa: BLE001 — fail closed
        log.exception("wema_auth_cb_error ref=%s", ref)
        alert("Wema authentication callback errored — payout denied", level="error",
              reference=ref, error=str(exc))
        authorized, reason = False, "error"

    log.info("wema_auth_cb ref=%s authorized=%s reason=%s si=%s ip=%s",
             ref, authorized, reason, _fingerprint(security_info), request.wema_ip)
    # The single most forensically valuable fact on the rail: whether we let a payout
    # proceed, and why not. Durable, unlike the log line above.
    request.wema_action = f"authorized:{reason}" if authorized else f"denied:{reason}"
    # Alert on denials that mean something. A retry against an already-settled payout
    # is the bank behaving normally; paging on it trains operators to ignore the alert
    # that matters. Always logged above either way.
    if not authorized and reason not in _BENIGN_DENIALS:
        alert("Wema payout authorisation denied", level="warning",
              reference=ref, reason=reason)
    return JsonResponse({"transactionReference": ref, "authorized": bool(authorized)},
                        status=200)


def _authorize_payout(ref: str, security_info: str, ip: str) -> tuple:
    """(authorized, reason). Every condition must hold; there is no path to True that
    skips the ledger lookup."""
    if not ref:
        return False, "no_reference"

    if _conf("AUTH_REQUIRE_SECURITY_INFO", False):
        # Recompute the per-transaction HMAC from the reference ALAT echoed. The
        # configured seed itself never crosses the wire. Missing seed/reference and
        # every mismatch deny before the ledger row can be authorized.
        expected = (wema_provider.security_info_for_reference(ref) or "").strip()
        if not expected or not hmac.compare_digest(security_info.strip(), expected):
            return False, "security_info_mismatch"

    max_age = int(_conf("AUTH_MAX_AGE", 900) or 900)
    with db_transaction.atomic():
        txn = (Transaction.objects.select_for_update()
               .filter(reference=ref, direction=Transaction.OUT).first())
        if txn is None:
            return False, "unknown_reference"
        if not is_bank_payout(txn):
            # A VTU purchase or internal transfer reference must never authorise a
            # bank payout.
            return False, "not_a_bank_payout"
        if txn.transaction_status != Transaction.PENDING:
            # SUCCESS => already treated as sent; FAILED => already refunded.
            return False, f"state_{txn.transaction_status}"
        age = (timezone.now() - txn.created).total_seconds()
        if age > max_age:
            return False, "stale"

        meta = dict(txn.meta or {})
        prior = meta.get("wema_auth") or {}
        meta["wema_auth"] = {
            "at": timezone.now().isoformat(),
            "ip": ip,
            "count": int(prior.get("count") or 0) + 1,
            "security_info": _fingerprint(security_info),
        }
        txn.meta = meta
        txn.save(update_fields=["meta"])
    return True, "authorized"


def _requery_cooled(txn) -> bool:
    """True when this reference was already requeried inside REQUERY_COOLDOWN.

    Read off the ledger row's own last-callback stamp rather than a cache, so the
    bound survives a restart, an empty cache and a worker change. A missing or
    unparseable stamp means "not cooled" — the safe direction, since the cost of a
    false negative is one extra requery, while a false positive would skip a
    settlement the bank was trying to tell us about.
    """
    stamp = ((txn.meta or {}).get("wema_callback") or {}).get("received")
    if not stamp:
        return False
    seen = parse_datetime(str(stamp))
    if seen is None:
        return False
    if timezone.is_naive(seen):
        seen = timezone.make_aware(seen)
    return (timezone.now() - seen).total_seconds() < REQUERY_COOLDOWN


def _callback_dict(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and len(value) <= 65536:
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


_TRANSACTION_REFERENCE_KEYS = (
    "transactionReference",
    "transactionReferenceId",
    "transactionRef",
    "clientTransactionReference",
    "customTransactionReference",
    "reference",
    "referenceId",
)
_TRANSACTION_ENVELOPE_KEYS = ("data", "request", "result", "transaction", "payload")


def _transaction_callback_data(body: dict) -> dict:
    """Return the nested object that carries Wema's transaction reference.

    ALAT has sent this callback in several shapes: objects and JSON strings under
    ``data`` or ``request``, sometimes with another envelope inside. Walk only
    those known envelope keys, with a small depth bound, and accept the reference
    spellings used by ALAT's other endpoints. This is deliberately not a generic
    recursive walk of attacker-controlled JSON.

    The endpoint still treats the callback as a trigger: the extracted reference
    only identifies an existing outgoing transaction to requery over APIM.
    """
    queue = [(body, 0)]
    seen = set()
    while queue:
        candidate, depth = queue.pop(0)
        data = _callback_dict(candidate)
        if not data or id(data) in seen:
            continue
        seen.add(id(data))

        # Preserve the provider's object so status/STAN metadata is taken from the
        # same envelope as the reference. Match key spelling case-insensitively:
        # .NET serializers have changed the initial capital in live integrations.
        keys = {str(key).casefold(): key for key in data}
        for name in _TRANSACTION_REFERENCE_KEYS:
            actual = keys.get(name.casefold())
            if actual is not None and str(data.get(actual) or "").strip():
                normalized = dict(data)
                normalized["transactionReference"] = str(data[actual]).strip()
                return normalized

        if depth >= 4:
            continue
        for name in _TRANSACTION_ENVELOPE_KEYS:
            actual = keys.get(name.casefold())
            if actual is not None:
                queue.append((data.get(actual), depth + 1))
    return {}


# ---------------------------------------------------------------------------
# 3. Transaction callback (requestType 3) — a trigger, never an oracle
# ---------------------------------------------------------------------------
@wema_callback("txn")
def wema_transaction_callback(request):
    """Status update for a debit/credit.

    The payload's ``status`` is NEVER acted on directly: ALAT publishes no legend for
    it, callbacks arrive out of order and duplicated, and the endpoint carries no
    signature. Instead this re-queries the transfer over the authenticated APIM
    channel and settles from that — so a forged or stale callback can at worst cause
    an unnecessary requery.

    Never credits a wallet: the payload has no amount and no account number.
    """
    body = request.wema_body
    data = _transaction_callback_data(body)
    ref = str(data.get("transactionReference") or "").strip()[:_REF_MAX]
    payload_status = str(data.get("status") or "").strip()

    if not ref:
        log.warning("wema_txn_cb_no_reference ip=%s keys=%s", request.wema_ip, sorted(body))
        return JsonResponse({"status": True}, status=200)

    txn = Transaction.objects.filter(reference=ref, direction=Transaction.OUT).first()
    if txn is None:
        txn = Transaction.objects.filter(
            direction=Transaction.OUT,
            meta__wema_transfer__platform_reference=ref,
        ).first()
    if txn is None:
        log.warning("wema_txn_cb_unknown ref=%s ip=%s", ref, request.wema_ip)
        alert("Wema transaction callback for an unknown reference", level="warning",
              reference=ref, payload_status=payload_status)
        return JsonResponse({"status": True}, status=200)

    outcome = "noop"
    if txn.transaction_status == Transaction.PENDING:
        if _requery_cooled(txn):
            # Recorded below, just not re-asked. Duplicated and out-of-order callbacks
            # are normal here, and the poller still sweeps anything left pending.
            outcome = "requery_cooled"
        elif is_bank_payout(txn):
            result = wema_provider.confirm_transfer_status(ref)
            outcome = settle_or_refund(txn, result)
        else:
            from utility import providers
            result = providers.vtu_requery(ref)
            outcome = settle_or_refund(txn, result)
    else:
        outcome = f"already_{txn.transaction_status}"

    # Bank identifiers are stamped under a namespaced key so raw provider text never
    # merges into the ledger's own meta (and never reaches a customer unfiltered).
    txn.refresh_from_db(fields=["meta"])
    meta = dict(txn.meta or {})
    meta["wema_callback"] = {
        "status": payload_status,
        "stan": str(data.get("transactionStan") or ""),
        "platform_reference": str(data.get("platformTransactionReference") or ""),
        "txn_date": str(data.get("orinalTxnTransactionDate")
                        or data.get("originalTxnTransactionDate") or ""),
        "received": timezone.now().isoformat(),
    }
    txn.meta = meta
    txn.save(update_fields=["meta"])

    log.info("wema_txn_cb ref=%s payload_status=%s outcome=%s", ref, payload_status, outcome)
    return JsonResponse({"status": True}, status=200)


# ---------------------------------------------------------------------------
# 4. Transaction Notification (production only) — record only
# ---------------------------------------------------------------------------
@wema_callback("notify")
def wema_notification_callback(request):
    """Real-time transaction notifications. ALAT does not document this payload, so
    this records it and changes no state — inventing semantics for an undocumented
    money message would be worse than dropping it."""
    body = request.wema_body
    log.info("wema_notify_cb keys=%s ip=%s", sorted(body), request.wema_ip)
    return JsonResponse({"status": True}, status=200)


# No shared token on this one. Its URL is given to the CUSTOMER — it is the cb_uri
# inside the bank page's address — so a token here would be published rather than
# kept. The per-session state carries the entropy instead, and the source-IP
# allowlist carries the authentication.
@wema_callback("face", token_required=False)
def wema_face_callback(request, state=""):
    """The bank reports the outcome of a face-biometric check.

    Payload: {success, c_id, id, id_type} — `c_id` is the correlationId proving the
    check passed, `id` the BVN/NIN it was run against.

    THE PAYLOAD DOES NOT SAY WHO THIS IS. It names an identity number, and an
    identity number is not a secret: it appears on forms, in bank branches, and in
    every breach dump. Honouring it on its own would let anyone who reaches this URL
    lift the tier of whichever customer holds that BVN. So the decision rests on the
    session we minted instead:

      * `state` must match a PENDING session we opened for one specific user;
      * the returned identity must hash to the one that session was opened with;
      * the session must not have expired, and is consumed either way.

    UNLESS the deployment runs the default "registered" callback mode, where the URL
    the bank holds is one exact whitelisted string and there is no state to carry —
    see FACE_CB_MODE. There the session is found by the identity instead, which is
    strictly weaker: a caller naming a BVN that happens to have a verification in
    flight is believed. What still has to hold is that a PENDING, unexpired session
    exists for that exact identity, that it is consumed on first use, and — carrying
    the weight now that the handle is gone — that the call came from
    FACE_CALLBACK_IPS. That allowlist is not optional in this mode: `_ip_ok` refuses
    everything while it is empty, and face_verify_live() hides the rail rather than
    offer a check nothing can authenticate.

    Only then is the session's BVN or NIN marked verified. This hosted Wema check is
    the ownership-proof alternative to Wallet Service SMS OTP; it is NOT the
    Prembly live-selfie check used for Tier 2, so it must never set ``face_verified``.
    Always answers 200 — a non-2xx invites a retry of something already recorded.
    """
    from accounts.models import (IdentityProof, User, hash_identifier,
                                 record_identity_proof)

    from .models import WemaFaceSession

    # Two shapes reach this view. `/face?s=<state>` is what we hand out now — a fixed
    # path is the only thing ALAT can whitelist once and have keep working, since the
    # state differs on every verification. `/face/<state>` is the older form, still
    # routed so a session opened before the change still lands. Neither is more
    # trusted than the other: the state is checked the same way below either way.
    if not state:
        state = str(request.GET.get("s") or "")[:64]

    body = request.wema_body
    correlation = str(body.get("c_id") or "")[:160]
    identity = str(body.get("id") or "")
    returned_kind = str(body.get("id_type") or "").strip().lower()
    claimed = bool(body.get("success"))
    # ALAT names the identity number "id", and the decorator records this body into
    # WebhookEvent — the one table we keep deliberately immutable — after we return.
    # Replace it in place with a non-reversible marker so a raw BVN is never written
    # there. Scrubbed here rather than by the global redaction list because "id" is
    # also every WhatsApp message's correlation handle, where redacting it would
    # blind the forensic trail instead of protecting anything.
    if identity:
        body["id"] = _fingerprint(identity)
    # Log key names and outcomes only — never the identity number itself. `mode` says
    # which shape the bank used, because "no state" is a legitimate registered callback
    # on one deployment and a call from nowhere on another, and the two look identical
    # in a log that omits it.
    log.info("wema_face_cb state=%s mode=%s success=%s has_cid=%s ip=%s browser=%s",
             (state or "")[:8], wema_provider.face_cb_mode(), claimed,
             bool(correlation), request.wema_ip,
             bool(getattr(request, "wema_face_browser_origin", "")))

    # Set only when a browser-delivered result has been proven by Wema's
    # authenticated API. Reused after commit so account creation is not submitted
    # twice for the same correlation.
    provider_validated_account = None

    with db_transaction.atomic():
        pending = (WemaFaceSession.objects
                   .select_for_update()
                   .filter(status=WemaFaceSession.PENDING)
                   .select_related("user"))
        if state:
            session = pending.filter(state=state).first()
        elif identity and wema_provider.face_cb_mode() == "registered":
            # Nothing per-verification can ride in a URL matched as an exact string, so
            # the identity is all there is to match on. Newest first: a customer who
            # retried has more than one session open for the same BVN, and the one
            # they are looking at is the last one we minted.
            by_identity = pending.filter(identity_hash=hash_identifier(identity))
            if returned_kind in (WemaFaceSession.BVN, WemaFaceSession.NIN):
                by_identity = by_identity.filter(identity_type=returned_kind)
            candidates = list(by_identity.order_by("-created"))
            # But "newest" is only safe while every candidate is the SAME PERSON.
            # A BVN is not a secret, so anyone can open a session against someone
            # else's — and if they open theirs last, newest-first would hand them the
            # victim's completed check. The replacement guards further down catch that
            # only once the victim is ALREADY verified, which is exactly the case
            # where the race does not matter. So refuse an ambiguous callback outright
            # and let both parties retry: a face check nobody can attribute must
            # verify nobody. Impossible in "session" mode, where the state names one.
            if len({candidate.user_id for candidate in candidates}) > 1:
                log.warning("wema_face_ambiguous_identity accounts=%s",
                            len({candidate.user_id for candidate in candidates}))
                alert("Face callback matched pending sessions on two accounts",
                      level="warning", accounts=len({c.user_id for c in candidates}))
                request.wema_action = "denied:ambiguous_identity"
                return JsonResponse({"status": True}, status=200)
            session = candidates[0] if candidates else None
            if session is not None:
                request.wema_action = "matched:by_identity"
        else:
            session = None
        if session is None:
            request.wema_action = "denied:unknown_session"
            return JsonResponse({"status": True}, status=200)
        if session.expired:
            session.status = WemaFaceSession.FAILED
            session.save(update_fields=["status", "updated"])
            request.wema_action = "denied:expired"
            return JsonResponse({"status": True}, status=200)
        if not claimed or not correlation:
            session.status = WemaFaceSession.FAILED
            session.save(update_fields=["status", "updated"])
            request.wema_action = "denied:not_verified"
            return JsonResponse({"status": True}, status=200)
        if not hmac.compare_digest(hash_identifier(identity), session.identity_hash):
            # The bank verified a face against a DIFFERENT identity than the one this
            # session was opened with. Never lift a tier on that.
            session.status = WemaFaceSession.FAILED
            session.save(update_fields=["status", "updated"])
            log.warning("wema_face_identity_mismatch state=%s user=%s",
                        (state or "")[:8], session.user_id)
            alert("Wema face callback identity mismatch", level="warning",
                  session=(state or "")[:8])
            request.wema_action = "denied:identity_mismatch"
            return JsonResponse({"status": True}, status=200)
        if returned_kind and returned_kind != session.identity_type:
            session.status = WemaFaceSession.FAILED
            session.save(update_fields=["status", "updated"])
            request.wema_action = "denied:identity_type_mismatch"
            return JsonResponse({"status": True}, status=200)

        # Lock the user as well as the session. Two face sessions for two different
        # BVNs could otherwise both observe an unverified user and both succeed,
        # leaving the last writer as the account identity while retaining two proof
        # rows. Serialising here makes the second callback hit the replacement guard.
        user = User.objects.select_for_update().get(pk=session.user_id)
        kind = session.identity_type
        flag_field = f"{kind}_verified"
        hash_field = f"{kind}_hash"
        last4_field = f"{kind}_last4"

        # A browser Origin is not proof: non-browser clients can spoof it. Validate
        # the correlation through Wema's authenticated without-OTP endpoint before
        # persisting any identity proof. Existing-account responses are deliberately
        # not accepted because they do not prove Wema evaluated this correlation.
        if (getattr(request, "wema_face_browser_origin", "")
                and not getattr(request, "wema_face_server_trusted", False)):
            from .services import attach_existing_bank_account, get_or_create_wallet
            existing_wallet = get_or_create_wallet(user)
            provider_validated_account = wema_provider.create_wallet_with_face(
                user.phone or "", user.email or f"{user.phone}@zitch.app",
                identity_type=kind, identity_value=identity,
                correlation_id=correlation,
            )
            if not provider_validated_account.get("success"):
                message = str(provider_validated_account.get("message") or "")
                # Wema returns HTTP 400 after a valid face check when the same
                # BVN/email/phone already has a partnership account on this channel.
                # Recover that account through the authenticated account-details API.
                # A generic "already exists" string is not enough: the read-back must
                # return and safely attach the NUBAN for this user's own phone.
                duplicate = ("already exist" in message.lower()
                             and "for this channel" in message.lower())
                recovered = existing_wallet if existing_wallet.account_number else None
                if duplicate and recovered is None:
                    try:
                        recovered, _detail = attach_existing_bank_account(
                            user, using_bvn=kind == "bvn")
                    except Exception:  # noqa: BLE001 — deny safely below
                        recovered = None
                        log.warning("wema_face_existing_readback_failed user=%s",
                                    user.id, exc_info=True)

                # The authenticated without-OTP endpoint returns this exact
                # channel-scoped duplicate only after receiving the same identity,
                # phone, email and Wema correlation from the completed face flow.
                # That is sufficient to attest the face result even when the older
                # NUBAN is not visible through GetPartnershipAccountDetails. Account
                # recovery remains separate and must still produce a real NUBAN
                # before funding; identity verification must not be falsely failed.
                normalized_message = message.casefold()
                expected_values = [
                    str(identity or "").casefold(),
                    str(user.phone or "").casefold(),
                    str(user.email or f"{user.phone}@zitch.app").casefold(),
                ]
                exact_existing = duplicate and all(
                    value and value in normalized_message for value in expected_values)
                if duplicate and (
                        (recovered is not None and recovered.account_number)
                        or exact_existing):
                    provider_validated_account = {
                        "success": True,
                        "existing": True,
                        "message": "Authenticated existing channel identity confirmed",
                    }
                    request.wema_action = (
                        "validated:existing_account_readback"
                        if recovered is not None and recovered.account_number
                        else "validated:existing_channel_identity")
                else:
                    session.status = WemaFaceSession.FAILED
                    session.save(update_fields=["status", "updated"])
                    request.wema_action = "denied:provider_correlation_validation"
                    log.warning("wema_face_browser_correlation_rejected user=%s kind=%s duplicate=%s",
                                user.id, kind, duplicate)
                    return JsonResponse({"status": True}, status=200)

        # A successful face check may CLAIM an unverified identity, but it may not
        # replace a different identity this account has already proven. The global
        # uniqueness check closes the race with another customer claiming the same
        # identity through OTP at the same time; the database constraint is the final
        # guard and the inner savepoint lets us turn that collision into a clean deny.
        stored_hash = getattr(user, hash_field, "") or ""
        if getattr(user, flag_field, False) and not hmac.compare_digest(
                stored_hash, session.identity_hash):
            session.status = WemaFaceSession.FAILED
            session.save(update_fields=["status", "updated"])
            request.wema_action = "denied:verified_identity_replacement"
            return JsonResponse({"status": True}, status=200)
        if User.objects.exclude(pk=user.pk).filter(
                **{hash_field: session.identity_hash}).exists():
            session.status = WemaFaceSession.FAILED
            session.save(update_fields=["status", "updated"])
            request.wema_action = "denied:identity_owned"
            return JsonResponse({"status": True}, status=200)

        setattr(user, hash_field, session.identity_hash)
        setattr(user, last4_field, identity[-4:])
        setattr(user, flag_field, True)
        user.recompute_tier()
        try:
            with db_transaction.atomic():
                user.save(update_fields=[hash_field, last4_field, flag_field, "tier"])
                record_identity_proof(
                    user, kind, session.identity_hash,
                    source=IdentityProof.WEMA_FACE,
                    provider_reference=correlation,
                    prehashed=True,
                )
        except IntegrityError:
            session.status = WemaFaceSession.FAILED
            session.save(update_fields=["status", "updated"])
            request.wema_action = "denied:identity_race"
            return JsonResponse({"status": True}, status=200)

        session.status = WemaFaceSession.VERIFIED
        session.correlation_id = correlation
        session.save(update_fields=["status", "correlation_id", "updated"])

    # A face pass can also replace OTP in Tier-1 account creation. The creation call
    # is intentionally outside the database transaction: an APIM timeout must not
    # hold locks or roll back genuine identity proof. Wema's profiled account callback
    # remains the authoritative NUBAN delivery; the immediate read-back only shortens
    # the happy path when the account is already visible.
    account_started = True
    account_failed = False
    from .services import attach_existing_bank_account, get_or_create_wallet
    wallet = get_or_create_wallet(user)
    if not wallet.account_number:
        account = provider_validated_account or wema_provider.create_wallet_with_face(
            user.phone or "", user.email or f"{user.phone}@zitch.app",
            identity_type=kind, identity_value=identity,
            correlation_id=correlation,
        )
        account_started = bool(account.get("success"))
        account_failed = not account_started
        # Read back on BOTH outcomes. "Customer already exists" is a failed create
        # response but often means the NUBAN was created by an earlier request whose
        # callback we missed; adopting it is the correct recovery, not asking the
        # customer to verify again.
        try:
            recovered, _detail = attach_existing_bank_account(
                user, using_bvn=kind == "bvn")
            if recovered and recovered.account_number:
                account_started = True
                account_failed = False
        except Exception:  # noqa: BLE001 — callback/reconcile remains authoritative
            log.warning("wema_face_account_readback_failed user=%s", user.id,
                        exc_info=True)
        if account_failed:
            log.warning("wema_face_account_start_failed user=%s kind=%s msg=%s",
                        user.id, kind, account.get("message", ""))
            alert("Wema face identity passed but account creation did not start",
                  level="error", user_id=user.id, identity_type=kind)
    wallet.refresh_from_db(fields=["account_number"])
    request.wema_action = "verified"
    _tell_whatsapp_face_passed(
        user, kind,
        account_pending=bool(account_started and not wallet.account_number),
        account_failed=account_failed,
    )
    return JsonResponse({"status": True}, status=200)


def _tell_whatsapp_face_passed(user, identity_type: str,
                               *, account_pending: bool = False,
                               account_failed: bool = False) -> None:
    """Close the loop for a customer who started this in chat.

    The result arrives on OUR server, so a chat customer is left staring at a link
    with no idea whether it worked. Sent AFTER the transaction commits and never
    allowed to fail the callback: the tier is already lifted, and a messaging hiccup
    must not turn a completed verification into a 500 the bank will retry.
    """
    from whatsapp.models import PendingAction, WhatsAppLink

    try:
        # If account creation started, this face result replaced the bank SMS code
        # and the old action must stop intercepting later messages. When account
        # creation failed, keep that OTP action alive as the customer's fallback.
        if not account_failed:
            PendingAction.objects.filter(
                user=user, action_type="add_account",
            ).delete()
        msisdn = (WhatsAppLink.objects
                  .filter(user=user, status=WhatsAppLink.ACTIVE)
                  .exclude(wa_msisdn="")
                  .values_list("wa_msisdn", flat=True).first())
        if not msisdn:
            return
        from whatsapp.router import reply
        if account_failed:
            account_note = ("\n\n⚠️ The bank did not start account creation. You can "
                            "still enter the SMS code already sent to finish setup.")
        elif account_pending:
            account_note = ("\n\n🏦 Your account is being created; we'll confirm the "
                            "account number when the bank sends it.")
        else:
            account_note = ""
        reply(msisdn, f"✅ *{identity_type.upper()} verified by face.*\n\n"
                      f"You're now Tier {user.tier} — up to "
                      f"₦{user.transaction_limit:,.0f} per transaction."
                      + account_note)
    except Exception:  # noqa: BLE001
        log.warning("wa_face_notify_failed user=%s", user.id, exc_info=True)
