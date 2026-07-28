from django.conf import settings
from django.contrib import admin
from django.http import HttpResponse, JsonResponse
from django.urls import include, path

from portal.pages import admin_portal, landing, prototype
from whatsapp.views import flow_endpoint as whatsapp_flow_endpoint
from whatsapp.views import webhook as whatsapp_webhook


def health(_request):
    """Liveness probe + which integrations are live (True) vs MOCK (False).

    Reports booleans only — never secrets — so ops can confirm prod keys are
    wired without exposing them. Served at /healthz so the marketing landing
    page can own "/". (The platform health check points at /healthz.)
    """
    from utility.providers import (_prembly_live, kyc_provider, payment_provider,
                                    payout_live, payout_provider, vas_provider, vtu_live)
    from utility import wema

    integrations = {
        "funding_provider": payment_provider(),   # which rail funds the wallet (wema)
        "funding_wema": wema.wema_live(),
        "funding_wema_simulation": wema.wema_simulation(),
        # Go-live gate (booleans, no secrets): a live payout only SETTLES when the
        # securityInfo signing scheme is configured AND we're pointed at the live host.
        # security_info false => live money calls are rejected and auto-refund;
        # wema_sandbox true => still on apiplayground, so no real money moves.
        "funding_wema_security_info": bool(settings.WEMA.get("SECURITY_INFO")),
        "wema_sandbox": "apiplayground" in (settings.WEMA.get("BASE_URL", "") or "").lower(),
        "payout_provider": payout_provider(),     # which rail sends payouts + name enquiry (wema)
        "payout_live": payout_live(),             # payout rail has live keys
        "vas_provider": vas_provider(),           # airtime/data/bills rail (vtung default)
        "vtu_vtung": vtu_live(),
        "sms_sendchamp": bool(settings.SENDCHAMP["API_KEY"]),
        "email_resend": bool(settings.RESEND["API_KEY"]),
        "kyc_provider": kyc_provider(),  # which backend verifies BVN/NIN/vNIN (wema Full KYC)
        "kyc_wema": wema.wema_live(),
        "kyc_prembly": _prembly_live(),  # selfie/liveness + address + ID-doc stay on Prembly
        "cards_issuer": bool(settings.CARD_ISSUER["API_KEY"]),
    }
    return JsonResponse({"status": True, "service": "zitch-api", "integrations": integrations})


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


def wema_diagnose(request):
    """GET /wema-diagnose?token=<WEMA_DIAG_TOKEN>[&account=&bank=&phone=&bvn=&nin=]

    Browser-accessible Wema/ALAT connectivity self-test for hosts without shell
    access (e.g. Render). Runs the real calls a deploy needs against the configured
    (test or live) keys and shows exactly what auth/connectivity error the gateway
    returns — turning "nothing works" into a precise fix. Returns NO secrets.

    Opt-in + protected: 404 unless WEMA_DIAG_TOKEN is set, and it must be supplied
    as ?token= (constant-time compared). Optional account+bank probe name enquiry;
    optional phone+bvn/nin probe wallet creation (sends a real OTP).
    """
    import hmac
    import os

    # Strip surrounding whitespace on both sides: a trailing space/newline pasted
    # into the env value (or the URL) would otherwise fail the byte-exact compare
    # with an unexplainable "forbidden".
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
    supplied = request.GET.get("token", "").strip()
    if not any(hmac.compare_digest(supplied, t) for t in diag_tokens):
        # Length-only hint (no token content) — pinpoints paste truncation/typos.
        return JsonResponse(
            {"detail": "forbidden",
             "hint": f"supplied token has {len(supplied)} chars; the configured "
                     f"token(s) have {sorted({len(t) for t in diag_tokens})}. "
                     f"They must match exactly."},
            status=403,
        )
    from utility.wema import wema_probe

    account = "".join(c for c in request.GET.get("account", "") if c.isdigit())[:10]
    bank = "".join(c for c in request.GET.get("bank", "") if c.isalnum())[:6]
    phone = "".join(c for c in request.GET.get("phone", "") if c.isdigit())[:14]
    bvn = "".join(c for c in request.GET.get("bvn", "") if c.isdigit())[:11]
    nin = "".join(c for c in request.GET.get("nin", "") if c.isdigit())[:11]
    otp = "".join(c for c in request.GET.get("otp", "") if c.isdigit())[:8]
    tracking_id = request.GET.get("tracking_id", "").strip()[:80]
    return JsonResponse({"wema": wema_probe(account, bank, phone, bvn=bvn, nin=nin,
                                            otp=otp, tracking_id=tracking_id)})


def _diag_denied(request, *env_names):
    """Shared gate for the browser diagnose endpoints: authorized when ?token=
    matches ANY of the given env vars (whitespace-stripped, constant-time).
    Returns None when authorized, else the error response."""
    import hmac
    import os

    tokens = [os.environ.get(n, "").strip() for n in env_names]
    tokens = [t for t in tokens if t]
    if not tokens:
        return JsonResponse(
            {"detail": f"Set {env_names[0]} (any secret value) in the environment to enable this."},
            status=404)
    supplied = request.GET.get("token", "").strip()
    if not any(hmac.compare_digest(supplied, t) for t in tokens):
        return JsonResponse({"detail": "forbidden"}, status=403)
    return None


def vtu_diagnose(request):
    """GET /vtu-diagnose?token=<DIAG_TOKEN|WEMA_DIAG_TOKEN>

    Browser self-test for the VTU.ng rail: proves the credentials authenticate
    and shows the VTU.ng wallet balance (purchases fail on an empty provider
    wallet no matter how correct the code is). Read-only; buys nothing.
    """
    denied = _diag_denied(request, "DIAG_TOKEN", "WEMA_DIAG_TOKEN")
    if denied:
        return denied
    from utility.vtung import vtu_probe

    return JsonResponse({"vtu": vtu_probe()})


def sms_diagnose(request):
    """GET /sms-diagnose?token=<DIAG_TOKEN|WEMA_DIAG_TOKEN>[&phone=<number>]

    Browser self-test for the Sendchamp SMS rail: proves the key authenticates and
    (with &phone=) sends ONE real OTP-style SMS, surfacing Sendchamp's response so a
    non-delivery is diagnosable. Signup hides SMS failures (anti-enumeration), so
    this is the way to confirm the OTP actually drops. Returns NO secrets.
    """
    denied = _diag_denied(request, "DIAG_TOKEN", "WEMA_DIAG_TOKEN")
    if denied:
        return denied
    from utility.providers import sms_probe

    phone = "".join(c for c in request.GET.get("phone", "") if c.isdigit())[:15]
    return JsonResponse({"sms": sms_probe(phone)})


def wema_callbacks_diagnose(request):
    """GET /wema-callbacks-diagnose?token=<DIAG_TOKEN|WEMA_DIAG_TOKEN>

    Browser self-test for the four bank-called callbacks, for hosts with no shell
    access (e.g. Render). ALAT will not enable the rails until it has PROFILED
    these exact URLs, so this prints the strings to hand the bank and proves, in
    process, that each one resolves to its handler and that the endpoint actually
    refuses a wrong secret.

    Checked in process rather than over loopback HTTP deliberately: a self-call
    would have to escape and re-enter the platform's own routing, so a failure
    would say more about the network than about the configuration.

    The output DOES embed the callback secret, because that secret is the URL —
    handing the bank the URL is what it's for. That is why it sits behind the
    diagnose token.
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
        fragment = f"/webhooks/wema/{segment}/{token or 'SET-WEMA_CALLBACK_TOKEN'}"
        try:
            handler = resolve(fragment).func.__name__
        except Resolver404:
            handler = ""
            blockers.append(f"{segment}: route does not resolve — is this deploy current?")
        routes.append({"give_the_bank_as": label, "url": base + fragment,
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

    return JsonResponse({"callbacks": {
        "ready_to_send_to_the_bank": not blockers,
        "blockers": blockers,
        "routes": routes,
        "secret_configured": bool(token),
        "secret_fingerprint": _fingerprint(token),   # never the secret itself
        "secret_rotation_pending": bool((conf.get("CALLBACK_TOKEN_PREV") or "").strip()),
        "wrong_secret_is_refused": secret_required,
        "enforce_source_ips": bool(conf.get("CALLBACK_ENFORCE_IPS", False)),
        "allowed_source_ips": list(conf.get("CALLBACK_IPS") or DEFAULT_CALLBACK_IPS),
        "this_request_came_from": client_ip(request),
        "authorization_max_age_seconds": int(conf.get("AUTH_MAX_AGE", 900) or 900),
        "require_security_info": bool(conf.get("AUTH_REQUIRE_SECURITY_INFO", False)),
    }})


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
    # Both spellings, for the same reason wema_urls.py registers both: these are
    # pasted into an address bar by hand, and APPEND_SLASH only ever ADDS a
    # slash — it cannot strip one. So a trailing slash on a slashless-only route
    # falls through to a bare HTML 404 that reads as "not deployed" rather than
    # "you typed one extra character".
    *[p for frag, view in (("wema-diagnose", wema_diagnose),
                           ("wema-callbacks-diagnose", wema_callbacks_diagnose),
                           ("vtu-diagnose", vtu_diagnose),
                           ("sms-diagnose", sms_diagnose))
      for p in (path(frag, view), path(frag + "/", view))],
    path("robots.txt", robots_txt),
    path("admin/", admin.site.urls),
    # Meta calls this exact path (no /api prefix, no trailing slash).
    path("webhooks/whatsapp", whatsapp_webhook),
    # WhatsApp Flows data-exchange endpoint (encrypted secure PIN submit).
    path("webhooks/whatsapp/flow", whatsapp_flow_endpoint),
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
