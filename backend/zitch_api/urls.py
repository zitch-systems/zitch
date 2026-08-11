import ipaddress
import json

from django.conf import settings
from django.contrib import admin
from django.http import HttpResponse, JsonResponse
from django.urls import include, path
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from portal.diagnostics import diagnostics_view
from portal.pages import admin_portal, landing, prototype
from whatsapp.views import approve_handoff as whatsapp_approve_handoff
from whatsapp.views import flow_endpoint as whatsapp_flow_endpoint
from whatsapp.views import webhook as whatsapp_webhook


def health(_request):
    """Liveness probe + which integrations are live (True) vs MOCK (False).

    Reports booleans only — never secrets — so ops can confirm prod keys are
    wired without exposing them. Served at /healthz so the marketing landing
    page can own "/". (The platform health check points at /healthz.)
    """
    from utility.providers import (_prembly_live, kyc_provider, payment_provider,
                                    payout_live, payout_provider, sms_live,
                                    vas_provider, vtu_live)
    from utility import wema
    from whatsapp.providers import flows_live as _flows_live
    from whatsapp.providers import wa_live, wa_mode

    integrations = {
        "funding_provider": payment_provider(),   # which rail funds the wallet (wema)
        "funding_wema": wema.wema_live(),
        "funding_wema_simulation": wema.wema_simulation(),
        # securityInfo is a value WE choose that the bank echoes back to our
        # authentication callback (confirmed by Wema 2026-07-27) — NOT a signing
        # scheme the bank issues, and NOT a precondition for a payout to settle.
        # False here means one optional factor is unused, not that money is blocked.
        # wema_sandbox true => still on apiplayground, so no real money moves.
        "funding_wema_security_info": bool(settings.WEMA.get("SECURITY_INFO")),
        "wema_sandbox": "apiplayground" in (settings.WEMA.get("BASE_URL", "") or "").lower(),
        "payout_provider": payout_provider(),     # which rail sends payouts + name enquiry (wema)
        "payout_live": payout_live(),             # payout rail has live keys
        "vas_provider": vas_provider(),           # airtime/data/bills rail (vtung default)
        "vtu_vtung": vtu_live(),
        # Termii is the only SMS rail, so the name is a constant and sms_live is the
        # whole story: keyed or in mock mode. The old per-rail sms_sendchamp boolean
        # went with the rail — anything alerting on it should watch sms_live instead.
        "sms_provider": "termii",
        # Termii ACCEPTS a message (returns a message_id, so `success` is true and
        # the customer is told a code is coming) and then never delivers it when
        # the sender ID is not whitelisted for the route. That is the single most
        # common OTP failure here and it is invisible from the send result. Both
        # halves are config, not secrets: `generic` is the PROMOTIONAL route and
        # is dropped for DND-registered lines, which is most Nigerian numbers.
        "sms_sender_id": (getattr(settings, "TERMII", {}) or {}).get("SENDER_ID", ""),
        "sms_channel": (getattr(settings, "TERMII", {}) or {}).get("CHANNEL", ""),
        "sms_live": sms_live(),
        "email_resend": bool(settings.RESEND["API_KEY"]),
        "kyc_provider": kyc_provider(),  # which backend verifies BVN/NIN/vNIN (wema Full KYC)
        "kyc_wema": wema.wema_live(),
        "kyc_prembly": _prembly_live(),  # selfie/liveness + address + ID-doc stay on Prembly
        "cards_issuer": bool(settings.CARD_ISSUER["API_KEY"]),
        # The audit that found the queue backlog also found REDIS_URL
        # disconnected — and there is a reason a boot-time check couldn't have
        # been trusted to catch it: DJANGO_REQUIRE_SHARED_CACHE can be set to
        # false on a service, which makes a Redis-less boot pass silently
        # instead of refusing to start. So "the app answered /healthz" is NOT
        # proof Redis is wired — this is the only thing that is.
        "redis": _redis_status(),
        # The chat channel belongs here for the same reason as every rail above:
        # this is the only endpoint an operator can read without a login, and a
        # silent WhatsApp bot is indistinguishable from a healthy one everywhere
        # else. `disabled` is the answer most of the time — the webhook then
        # returns 404 to Meta and no message is ever seen.
        "whatsapp_mode": wa_mode(),               # disabled | sandbox | live
        "whatsapp_live": wa_live(),
        # Is the secure PIN Flow actually armed? This is the difference between
        # a PIN typed into a masked field and submitted encrypted, and the
        # channel quietly falling back to an SMS code — and nothing else
        # reports it. False here with whatsapp_live true means WHATSAPP_FLOW_ID
        # or WHATSAPP_FLOW_PRIVATE_KEY is missing: the fallback is deliberate
        # and safe, but it is not the PIN screen anyone thinks they configured.
        "whatsapp_flow_ready": _flows_live(),
        # flow_ready needs the key AND a published Flow ID, so it stays false
        # through the whole Meta setup and cannot tell you which half is
        # missing. This reports the key alone: true = present and parses, false
        # = unset or unparseable. It is the difference between the 421 Meta's
        # health check reports and a Flow that simply hasn't been published yet.
        "whatsapp_flow_key_ok": _flow_key_ok(),
        # Has Meta ever SUCCESSFULLY called the webhook? Nothing downstream can
        # explain silence when the callback never arrives, and that case is
        # invisible in every other reading: no call means no queue row, no log
        # line, nothing — which reads exactly like an idle, healthy channel.
        "whatsapp_webhook_reached": _whatsapp_webhook_reached(),
        # The other half of the same question. `reached` covers the inbound leg;
        # this covers the reply. A channel can receive perfectly and still be mute
        # — an expired token fails only the send — and /whatsapp-diagnose reports
        # that behind an admin login, which is exactly what an operator debugging
        # from a phone does not have.
        "whatsapp_outbound_failing": _whatsapp_outbound_failing(),
        # Both readings above are HISTORICAL — "reached" is true once Meta has
        # ever called, and "outbound_failing" reads the last replies we sent,
        # which stay healthy-looking forever if we stop replying at all. A
        # channel that died two hours ago therefore reads identically to a live
        # one. These two answer the question actually being asked in an outage:
        # is anything arriving, and is anything being worked?
        "whatsapp_inbound_waiting": _whatsapp_inbound_waiting(),
        "whatsapp_last_processed_at": _whatsapp_last_processed_at(),
        # Every reading above answers "is the channel configured/alive". None of
        # them answers "does the Flow behind WHATSAPP_FLOW_ID still have the
        # screens this code sends" — and that is the one that silently breaks,
        # because the Flow JSON ships in this repo but is published BY HAND in
        # WhatsApp Manager. Screens added earliest keep working while the newest
        # are rejected, which reads like "some features are broken" instead of
        # "the Flow is one publish behind". `missing_screens` names them.
        "whatsapp_flow_published": _whatsapp_flow_published(),
        # `flow_published` proves the Flow is right; it cannot explain a send
        # Meta refused for any OTHER reason. That reason is logged, but reading
        # it means shell access to the log stream while reproducing the failure
        # — so in practice it has been guessed at from symptoms. This is the
        # last rejection, in Meta's own words, on a page an operator can open.
        "whatsapp_last_flow_error": _whatsapp_last_flow_error(),
        # "Your BVN has been submitted for review" is the same sentence whether
        # the number was not found, the name did not match, the provider was
        # unreachable, or the record carried no phone to challenge — and those
        # need different actions. This is the last one, with its reason.
        "whatsapp_last_identity_review": _whatsapp_last_identity_review(),
        # The other direction of the Flow channel. `last_flow_error` is a send WE
        # made that Meta refused; this is a call META made to US — the leg behind
        # the blank panel reading "Something went wrong. Try again later.", which
        # otherwise leaves no trace at all. If `at` does NOT move when the
        # customer reproduces, Meta never reached us (cold start, signature,
        # networking). If it moves and carries an `error`, that is the reason.
        "whatsapp_last_flow_endpoint": _whatsapp_last_flow_endpoint(),
    }
    return JsonResponse({"status": True, "service": "zitch-api", "integrations": integrations})


def _whatsapp_last_flow_endpoint():
    """The last call Meta made to the Flows data-exchange endpoint."""
    try:
        from whatsapp.models import SystemSetting
        from whatsapp.views import FLOW_ENDPOINT_KEY

        raw = SystemSetting.get(FLOW_ENDPOINT_KEY, "")
        if not raw:
            return {}
        parts = (raw.split("|", 3) + ["", "", "", ""])[:4]
        return {"at": parts[0], "action": parts[1], "screen": parts[2], "error": parts[3]}
    except Exception:  # noqa: BLE001 — a health probe never raises
        return {}


def _whatsapp_last_identity_review():
    """Why the last BVN/NIN went to review instead of verifying."""
    try:
        from whatsapp.models import SystemSetting
        from whatsapp.router import IDENTITY_REVIEW_KEY

        raw = SystemSetting.get(IDENTITY_REVIEW_KEY, "")
        if not raw:
            return {}
        parts = (raw.split("|", 2) + ["", "", ""])[:3]
        return {"at": parts[0], "kind": parts[1], "reason": parts[2]}
    except Exception:  # noqa: BLE001 — a health probe never raises
        return {}


def _whatsapp_last_flow_error():
    """The most recent Flow send Meta refused. {} when none ever has."""
    from whatsapp.providers import last_flow_error

    try:
        return last_flow_error()
    except Exception:  # noqa: BLE001 — a health probe never raises
        return {}


def _whatsapp_flow_published():
    """Ask Meta what the published Flow contains. Networked, so it is skipped
    unless the channel is live and a Flow ID is set (see published_flow_report)."""
    from whatsapp.providers import published_flow_report

    try:
        return published_flow_report()
    except Exception:  # noqa: BLE001 — a health probe never raises
        return {"status": "error"}


def _redis_status():
    """Prove the shared cache is Redis AND reachable — not just configured.

    A boot-time check exists for this (validate_production_configuration), but
    it can be silenced by DJANGO_REQUIRE_SHARED_CACHE=false on a per-service
    basis, which is exactly what the earlier audit found: a Redis-less boot
    that passes silently instead of refusing to start. So "the app answered
    /healthz" was never proof Redis is wired — only a real round-trip is. A
    misconfigured REDIS_URL (wrong host, unreachable instance) would pass a
    bare settings check and still fail here, which is the case that matters.
    """
    from django.core.cache import cache

    configured = bool(settings.REDIS_URL)
    backend = str((getattr(settings, "CACHES", {}) or {}).get("default", {}).get("BACKEND", ""))
    is_redis_backend = "RedisCache" in backend
    if not (configured and is_redis_backend):
        return {"configured": configured, "backend_is_redis": is_redis_backend, "reachable": None}
    probe_key = "healthz_redis_probe"
    try:
        cache.set(probe_key, "1", timeout=10)
        reachable = cache.get(probe_key) == "1"
    except Exception:  # noqa: BLE001 — a health probe never raises
        reachable = False
    return {"configured": True, "backend_is_redis": True, "reachable": reachable}


def _flow_key_ok():
    """Does WHATSAPP_FLOW_PRIVATE_KEY exist and actually parse?

    The Flows endpoint answers Meta with a bare 421 when it cannot decrypt, and
    that is the ONLY thing Meta's health check shows — "failed to receive
    expected HTTP response", which reads like a URL problem. Nine times out of
    ten it is the key: unset, or mangled by the dashboard that stored it. This
    turns that guess into a reading.
    """
    from whatsapp.flows_crypto import FlowDecryptError, _private_key

    try:
        _private_key()
        return True
    except FlowDecryptError:
        return False
    except Exception:  # noqa: BLE001 — a health probe never raises
        return None


def _whatsapp_webhook_reached():
    """True once Meta has made one verified call. None if we cannot tell.

    Guarded because /healthz is the platform's LIVENESS probe: it answers 200
    over plain HTTP whether or not the database is reachable (that is `readyz`'s
    job). A diagnostic detail must never be the reason the platform decides the
    service is down and cycles it.
    """
    try:
        from whatsapp.models import WebhookEvent

        # ACCEPTED, not verified=True: `verified` means only that the signature
        # checked out, and a call naming a phone-number id we do not recognise is
        # signed by Meta, recorded verified, then dropped with a 400. Keyed on
        # verified this read "true" for a channel refusing every single message.
        return WebhookEvent.objects.filter(source="whatsapp",
                                           outcome=WebhookEvent.ACCEPTED).exists()
    except Exception:      # noqa: BLE001 — liveness outranks this datum
        return None


def _whatsapp_outbound_failing():
    """True when Meta refused every one of the last few replies.

    None when nothing has been sent yet — an empty outbound log is not evidence of
    health, and False would read as one. Guarded like the probe above: /healthz is
    the liveness endpoint and answers whether or not the database is reachable.
    """
    try:
        from whatsapp.models import WaMessageLog

        recent = list(WaMessageLog.objects.filter(direction=WaMessageLog.OUT)
                      .order_by("-created").values_list("processing_error", flat=True)[:20])
        return all(bool(e) for e in recent) if recent else None
    except Exception:      # noqa: BLE001 — liveness outranks this datum
        return None


def _whatsapp_inbound_waiting():
    """How many inbound messages are queued and not yet processed.

    The decisive number in a silent-channel outage. A hobby-tier host reaps the
    background drain thread on cold start, so the webhook 200s, the row is
    stored, and nothing ever works it: no reply, no send attempt, no error — and
    every other reading on this page stays green. A backlog that is not zero and
    not moving says exactly that, and points at WHATSAPP_PROCESS_INLINE.
    """
    try:
        from whatsapp.models import WaMessageLog

        return WaMessageLog.objects.filter(direction=WaMessageLog.IN,
                                           processed_at__isnull=True).count()
    except Exception:      # noqa: BLE001 — liveness outranks this datum
        return None


def _whatsapp_last_processed_at():
    """When an inbound message was last worked through to completion.

    Timestamp rather than a boolean, because "is it working" has no useful
    yes/no answer — an idle channel and a dead one both have nothing to report.
    An operator comparing this against the clock knows in one glance whether the
    silence is theirs or ours.
    """
    try:
        from whatsapp.models import WaMessageLog

        row = (WaMessageLog.objects.filter(direction=WaMessageLog.IN,
                                           processed_at__isnull=False)
               .order_by("-processed_at").values_list("processed_at", flat=True).first())
        return row.isoformat() if row else None
    except Exception:      # noqa: BLE001
        return None


def readyz(_request):
    """Readiness probe: 200 only if the database is reachable, else 503.

    Unlike /healthz (pure liveness, always 200 over plain HTTP for the platform
    probe), this round-trips the DB so orchestration/monitoring can tell a live
    process apart from one that can't serve traffic (DB down)."""
    from django.db import connection

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:  # noqa: BLE001 — any DB error means not ready
        return JsonResponse({"status": False, "db": False}, status=503)
    return JsonResponse({"status": True, "db": True})


def robots_txt(_request):
    """Keep the API / operator host out of search engines.

    The public, indexable site is the marketing landing at https://zitch.ng
    (Cloudflare Pages). This host only serves the JSON API, Django admin and the
    operator/console portals — none of which should be crawled or surface in
    search results, and indexing this host's own "/" page would duplicate the
    marketing site. So disallow everything here; SEO lives on zitch.ng.
    """
    return HttpResponse("User-agent: *\nDisallow: /\n", content_type="text/plain")


# POST only: this endpoint can send/consume an OTP. Credentials and identity data must
# never appear in URLs, browser history, proxy access logs, or Slack link unfurls.
@csrf_exempt
@never_cache
@require_http_methods(["POST"])
def wema_diagnose(request):
    """POST /wema-diagnose with Authorization: Bearer <diagnostic token>.

    Browser-accessible Wema/ALAT connectivity self-test for hosts without shell
    access (e.g. Render). Runs the real calls a deploy needs against the configured
    (test or live) keys and shows exactly what auth/connectivity error the gateway
    returns — turning "nothing works" into a precise fix. Returns NO secrets.

    Optional JSON fields drive name enquiry or wallet creation. Responses are marked
    no-store and contain no configured secrets.
    """
    import hmac
    import os

    # Whitespace is stripped on both sides: a trailing space/newline pasted into the
    # env value (or the URL) would otherwise fail the byte-exact compare with an
    # unexplainable "forbidden".
    #
    # Accepts EITHER token, like the other three probes. This one used to read
    # WEMA_DIAG_TOKEN alone, so setting only DIAG_TOKEN opened /vtu-diagnose,
    # /sms-diagnose and /wema-callbacks-diagnose while this one kept 404ing —
    # indistinguishable from "the route isn't deployed", which is the exact
    # question these endpoints exist to answer.
    diag_tokens = [t for t in (os.environ.get("WEMA_DIAG_TOKEN", "").strip(),
                               os.environ.get("DIAG_TOKEN", "").strip()) if t]
    if not diag_tokens:
        return JsonResponse(
            {"detail": "Set WEMA_DIAG_TOKEN or DIAG_TOKEN (any secret value) in the "
                       "environment to enable this."},
            status=404,
        )
    authorization = request.headers.get("Authorization", "")
    supplied = (authorization[7:] if authorization.lower().startswith("bearer ")
                else "").strip()
    if not any(hmac.compare_digest(supplied, t) for t in diag_tokens):
        # Length-only hint (no token content) — pinpoints paste truncation/typos.
        return JsonResponse(
            {"detail": "forbidden",
             "hint": f"supplied token has {len(supplied)} chars; the configured "
                     f"token(s) have {sorted({len(t) for t in diag_tokens})}. "
                     f"They must match exactly."},
            status=403,
        )
    if int(request.META.get("CONTENT_LENGTH") or 0) > 64 * 1024:
        return JsonResponse({"detail": "request too large"}, status=413)
    raw_body = request.body
    if len(raw_body) > 64 * 1024:
        return JsonResponse({"detail": "request too large"}, status=413)
    try:
        payload = json.loads(raw_body or b"{}")
    except (ValueError, TypeError, UnicodeDecodeError):
        return JsonResponse({"detail": "invalid JSON"}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"detail": "invalid JSON"}, status=400)
    from utility.wema import wema_probe

    account = "".join(c for c in str(payload.get("account") or "") if c.isdigit())[:10]
    bank = "".join(c for c in str(payload.get("bank") or "") if c.isalnum())[:6]
    phone = "".join(c for c in str(payload.get("phone") or "") if c.isdigit())[:14]
    bvn = "".join(c for c in str(payload.get("bvn") or "") if c.isdigit())[:11]
    nin = "".join(c for c in str(payload.get("nin") or "") if c.isdigit())[:11]
    otp = "".join(c for c in str(payload.get("otp") or "") if c.isdigit())[:8]
    tracking_id = str(payload.get("tracking_id") or "").strip()[:80]
    response = JsonResponse({"wema": wema_probe(
        account, bank, phone, bvn=bvn, nin=nin, otp=otp, tracking_id=tracking_id)})
    response["Cache-Control"] = "no-store"
    return response


def _diag_denied(request, *env_names):
    """Shared gate for diagnostics using a bearer token.

    Query-string credentials leak through browser history, proxy access logs and
    link unfurls, so the legacy ``?token=`` transport is intentionally rejected.
    Authorization succeeds when the bearer value matches any configured token
    (whitespace-stripped, constant-time).
    Returns None when authorized, else the error response."""
    import hmac
    import os

    tokens = [os.environ.get(n, "").strip() for n in env_names]
    tokens = [t for t in tokens if t]
    if not tokens:
        return JsonResponse(
            {"detail": f"Set {env_names[0]} (any secret value) in the environment to enable this."},
            status=404)
    authorization = request.headers.get("Authorization", "")
    supplied = (authorization[7:] if authorization.lower().startswith("bearer ")
                else "").strip()
    if not any(hmac.compare_digest(supplied, t) for t in tokens):
        return JsonResponse({"detail": "forbidden"}, status=403)
    return None


@never_cache
@require_http_methods(["GET"])
def preflight_diagnose(request):
    """GET /preflight with an Authorization bearer token.

    The go-live readiness check over HTTP. It is the same `manage.py wema_preflight`
    run verbatim — same checks, same wording, same exit status — because a second
    implementation would drift from the one the runbook cites, and a readiness gate
    that disagrees with itself is worse than one place to look.

    It exists because the command was reachable ONLY from a shell, and the deploys
    that most need a go-live gate are precisely the ones without one (Render's free
    tier has no shell at all). Read-only: every check the command runs is a read.

    `ready` is the machine-readable answer (exit 0); `report` is the operator-readable
    one. `?strict=1` also gates on the soft checks, exactly as `--strict` does.
    """
    denied = _diag_denied(request, "DIAG_TOKEN", "WEMA_DIAG_TOKEN")
    if denied:
        return denied
    import io

    from django.core.management import call_command

    buf = io.StringIO()
    ready, error = True, ""
    try:
        call_command("wema_preflight", stdout=buf, stderr=buf,
                     **({"strict": True} if request.GET.get("strict") else {}))
    except SystemExit:
        # How the command reports "not ready" — a failing gate, not a crash.
        ready = False
    except Exception as exc:                                  # noqa: BLE001
        # A preflight that cannot run is not a passing preflight.
        ready, error = False, f"{type(exc).__name__}: {exc}"
    body = {"ready": ready, "strict": bool(request.GET.get("strict")),
            "report": buf.getvalue().splitlines()}
    if error:
        body["error"] = error
    response = JsonResponse({"preflight": body}, status=200 if ready else 503)
    response["Cache-Control"] = "no-store"
    return response


@never_cache
@require_http_methods(["GET"])
def whatsapp_diagnose(request):
    """GET /whatsapp-diagnose with an Authorization bearer token.

    The WhatsApp channel's own probe: mode, the durable inbound queue, and whether
    anything is draining it. "The bot stopped replying" has no signature anywhere
    else — the webhook still answers 200 and the router is never reached — so this
    is the only view that distinguishes a dead worker from a muted conversation.
    Read-only; sends nothing.
    """
    denied = _diag_denied(request, "DIAG_TOKEN", "WEMA_DIAG_TOKEN")
    if denied:
        return denied
    from whatsapp.health import whatsapp_diagnostics

    response = JsonResponse({"whatsapp": whatsapp_diagnostics()})
    response["Cache-Control"] = "no-store"
    return response


@never_cache
@require_http_methods(["GET"])
def vtu_diagnose(request):
    """GET /vtu-diagnose with an Authorization bearer token.

    Browser self-test for the VTU.ng rail: proves the credentials authenticate
    and shows the VTU.ng wallet balance (purchases fail on an empty provider
    wallet no matter how correct the code is). Read-only; buys nothing.
    """
    denied = _diag_denied(request, "DIAG_TOKEN", "WEMA_DIAG_TOKEN")
    if denied:
        return denied
    from utility.vtung import vtu_probe

    response = JsonResponse({"vtu": vtu_probe()})
    response["Cache-Control"] = "no-store"
    return response


# POST only: this can spend provider credit and text a real person. Keeping both the
# bearer credential and phone number out of the URL prevents proxy/history leakage,
# while refusing GET/HEAD prevents prefetchers and link unfurls from triggering it.
@csrf_exempt
@never_cache
@require_http_methods(["POST"])
def sms_diagnose(request):
    """POST /sms-diagnose with bearer auth and optional JSON ``phone``.

    Remote self-test for the Termii SMS rail: proves the key authenticates and
    (with a JSON phone) sends ONE real OTP-style SMS, surfacing Termii's response so a
    non-delivery is diagnosable. Signup hides SMS failures (anti-enumeration), so
    this is the way to confirm the OTP actually drops. Returns NO secrets.
    """
    denied = _diag_denied(request, "DIAG_TOKEN", "WEMA_DIAG_TOKEN")
    if denied:
        return denied
    from utility.providers import sms_probe

    if int(request.META.get("CONTENT_LENGTH") or 0) > 16 * 1024:
        return JsonResponse({"detail": "request too large"}, status=413)
    raw_body = request.body
    if len(raw_body) > 16 * 1024:
        return JsonResponse({"detail": "request too large"}, status=413)
    try:
        payload = json.loads(raw_body or b"{}")
    except (ValueError, TypeError, UnicodeDecodeError):
        return JsonResponse({"detail": "invalid JSON"}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"detail": "invalid JSON"}, status=400)
    phone = "".join(c for c in str(payload.get("phone") or "") if c.isdigit())[:15]
    response = JsonResponse({"sms": sms_probe(phone)})
    response["Cache-Control"] = "no-store"
    return response


@never_cache
@require_http_methods(["GET"])
def wema_callbacks_diagnose(request):
    """GET /wema-callbacks-diagnose with an Authorization bearer token.

    Remote self-test for the four bank-called callbacks, for hosts with no shell
    access (e.g. Render). ALAT will not enable the rails until it has PROFILED
    these exact URLs, so this prints the strings to hand the bank and proves, in
    process, that each one resolves to its handler and that the endpoint actually
    refuses a wrong secret.

    Checked in process rather than over loopback HTTP deliberately: a self-call
    would have to escape and re-enter the platform's own routing, so a failure
    would say more about the network than about the configuration.

    URL templates deliberately do not embed the callback secret. Operators combine
    the template with the value held in the secret manager when profiling the bank;
    diagnostic responses must remain safe even if captured by an access log.
    """
    denied = _diag_denied(request, "DIAG_TOKEN", "WEMA_DIAG_TOKEN")
    if denied:
        return denied
    from django.urls import Resolver404, resolve

    from common.ratelimit import client_ip
    from wallet.wema_callbacks import DEFAULT_CALLBACK_IPS, _fingerprint, _token_ok

    conf = getattr(settings, "WEMA", None) or {}
    token = (conf.get("CALLBACK_TOKEN") or "").strip()
    base = f"{'https' if request.is_secure() else 'http'}://{request.get_host()}"
    blockers = []

    # ALAT's own names for the four, so the output can be read straight against the
    # profiling form the bank sends. `notification` is production-only.
    routes = []
    for label, segment in (("Account Creation Callback URL", "account"),
                           ("Authentication Callback URL", "authorize"),
                           ("Transaction Callback URL", "transaction"),
                           ("Transaction Notification URL (production only)", "notification")):
        probe_fragment = f"/webhooks/wema/{segment}/{token or 'SET-WEMA_CALLBACK_TOKEN'}"
        public_fragment = f"/webhooks/wema/{segment}/<WEMA_CALLBACK_TOKEN>"
        try:
            handler = resolve(probe_fragment).func.__name__
        except Resolver404:
            handler = ""
            blockers.append(f"{segment}: route does not resolve — is this deploy current?")
        routes.append({"give_the_bank_as": label, "url_template": base + public_fragment,
                       "resolves": bool(handler), "handler": handler})

    # The meaningful assertion is the negative one: that a WRONG secret is turned
    # away. Comparing the configured token against itself would pass trivially and
    # prove nothing — this catches the open-endpoint case (no token configured, and
    # simulation or DEBUG letting it through).
    secret_required = not _token_ok("zitch-diagnose-deliberately-wrong-secret")
    if not token:
        blockers.append("WEMA_CALLBACK_TOKEN is unset — the URL carries no secret.")
    if not secret_required:
        blockers.append("The callbacks accept ANY secret right now — do not profile these URLs.")

    # Source-IP detection, shown in full so the trusted-proxy hop count can be read
    # off a real request instead of guessed. If client_ip() resolves to a private
    # address, it is reporting a hop INSIDE the platform rather than the true caller,
    # and an allowlist compared against it would refuse every bank callback while
    # looking correctly configured. Surfacing the raw chain is what makes that
    # diagnosable — the alternative is a silent 403 storm the bank sees and we don't.
    enforce_ips = bool(conf.get("CALLBACK_ENFORCE_IPS", False))
    resolved = client_ip(request)
    xff = [p.strip() for p in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",") if p.strip()]
    hops = int(getattr(settings, "RATELIMIT_TRUSTED_PROXY_HOPS", 0) or 0)

    def _public(value):
        try:
            return ipaddress.ip_address(value).is_global
        except ValueError:
            return False

    # The hop count that WOULD land on the right-most public address in the chain.
    suggested = next((i for i, v in enumerate(reversed(xff), start=1) if _public(v)), None)
    resolved_public = _public(resolved)
    if enforce_ips and not resolved_public:
        blockers.append(
            f"CALLBACK_ENFORCE_IPS is on but the source IP resolves to {resolved}, "
            f"which is not a public address — every bank callback would be refused. "
            + (f"Set RATELIMIT_TRUSTED_PROXY_HOPS={suggested} (currently {hops})."
               if suggested else "Fix RATELIMIT_TRUSTED_PROXY_HOPS before enabling it.")
        )

    response = JsonResponse({"callbacks": {
        "ready_to_send_to_the_bank": not blockers,
        "blockers": blockers,
        "routes": routes,
        "secret_configured": bool(token),
        "secret_fingerprint": _fingerprint(token),   # never the secret itself
        "secret_rotation_pending": bool((conf.get("CALLBACK_TOKEN_PREV") or "").strip()),
        "wrong_secret_is_refused": secret_required,
        "enforce_source_ips": enforce_ips,
        "allowed_source_ips": list(conf.get("CALLBACK_IPS") or DEFAULT_CALLBACK_IPS),
        "this_request_came_from": resolved,
        "source_ip_detection": {
            # Read this off a request you made yourself: if `resolved_is_public` is
            # false, the allowlist cannot work yet and rate limits are bucketing on a
            # platform hop rather than per-caller.
            "resolved_is_public": resolved_public,
            "trusted_proxy_hops": hops,
            "suggested_trusted_proxy_hops": suggested,
            "x_forwarded_for": xff,
            "remote_addr": request.META.get("REMOTE_ADDR", ""),
            "safe_to_enable_ip_enforcement": resolved_public,
        },
        "authorization_max_age_seconds": int(conf.get("AUTH_MAX_AGE", 900) or 900),
        "require_security_info": bool(conf.get("AUTH_REQUIRE_SECURITY_INFO", False)),
    }})
    response["Cache-Control"] = "no-store"
    return response


urlpatterns = [
    # Canonical web surfaces: the marketing landing + operator portal (portal app).
    # The health probe keeps its JSON shape at /healthz; /readyz also round-trips
    # the DB. The parallel console/admin_api build coexists under /console/* and
    # /api/admin/ (mounted below) so both portals run side by side.
    path("", landing),
    path("prototype/", prototype),
    path("portal/", admin_portal),
    path("healthz", health),
    path("readyz", readyz),
    # Both spellings, for the same reason wema_urls.py registers both: operational
    # probes should not fail merely because a caller included a trailing slash.
    *[p for frag, view in (("wema-diagnose", wema_diagnose),
                           ("wema-callbacks-diagnose", wema_callbacks_diagnose),
                           ("vtu-diagnose", vtu_diagnose),
                           ("whatsapp-diagnose", whatsapp_diagnose),
                           ("sms-diagnose", sms_diagnose),
                           ("preflight", preflight_diagnose))
      for p in (path(frag, view), path(frag + "/", view))],
    path("robots.txt", robots_txt),
    # The diagnostics as a PAGE. Every *-diagnose endpoint needs an Authorization
    # header, which means curl, which means a terminal — so on a deploy whose operator
    # works from a browser and the hosting dashboard (no shell anywhere), all of it was
    # unreachable and only /healthz answered. Same checks, same functions, admin's own
    # staff-session auth instead of a shared bearer token. Registered BEFORE admin/ so
    # it resolves rather than being swallowed by the admin catch-all.
    path("admin/diagnostics/", admin.site.admin_view(diagnostics_view), name="diagnostics"),
    path("admin/", admin.site.urls),
    # Meta calls this exact path (no /api prefix, no trailing slash).
    path("webhooks/whatsapp", whatsapp_webhook),
    # WhatsApp Flows data-exchange endpoint (encrypted secure PIN submit).
    path("webhooks/whatsapp/flow", whatsapp_flow_endpoint),
    # The https bounce for deep-link approval — WhatsApp only renders http(s)
    # links as tappable, so the chat carries this URL and it forwards into the
    # app scheme (store links as the no-app fallback).
    path("wa/approve/<str:token>", whatsapp_approve_handoff),
    # Wema/ALAT bank-called callbacks. The bank PROFILES these exact URLs, and the
    # rails do not work until it has: account creation is refused without a profiled
    # Account Creation URL, and transactions fail authentication without the
    # Authentication URL. The trailing path segment is a shared secret; they sit
    # outside /api/ because they are bank-authenticated, not user-authenticated.
    path("webhooks/wema/", include("wallet.wema_urls")),
    path("api/admin/", include("admin_api.urls")),
    path("api/whatsapp/", include("whatsapp.urls")),
    path("api/ops/", include("portal.urls")),
    path("api/", include("accounts.urls")),
    path("api/", include("wallet.urls")),
    path("api/utility/", include("utility.urls")),
    path("api/exams/", include("exams.urls")),
    path("api/loans/", include("loans.urls")),
    path("api/savings/", include("savings.urls")),
    path("api/betting/", include("betting.urls")),
    path("api/transfers/", include("transfers.urls")),
    path("api/cards/", include("cards.urls")),
    path("api/banklink/", include("banklink.urls")),
    path("api/convert/", include("convert.urls")),
    path("api/disputes/", include("compliance.urls")),
    # Parallel "console" build (kept alongside main's portal): landing "/console/",
    # app prototype "/console/app/", operator portal "/console/portal/" — distinct
    # paths so it never shadows the canonical surfaces above.
    path("console/", include("console.urls")),
]

# Serve user-uploaded media (avatars) in development. In production this is
# handled by the object store / CDN (see MEDIA settings note).
if settings.DEBUG:
    from django.conf.urls.static import static

    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
