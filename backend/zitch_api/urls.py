from django.conf import settings
from django.contrib import admin
from django.http import HttpResponse, JsonResponse
from django.urls import include, path

from portal.pages import admin_portal, landing, prototype
from whatsapp.views import webhook as whatsapp_webhook


def health(_request):
    """Liveness probe + which integrations are live (True) vs MOCK (False).

    Reports booleans only — never secrets — so ops can confirm prod keys are
    wired without exposing them. Served at /healthz so the marketing landing
    page can own "/". (The platform health check points at /healthz.)
    """
    from utility.providers import (_kora_live, _prembly_live, kyc_provider, payment_provider,
                                    payout_live, payout_provider, vtu_live)
    from utility import monnify, wema

    integrations = {
        "funding_provider": payment_provider(),   # which rail funds the wallet (monnify/kora)
        "funding_monnify": monnify.monnify_live(),
        "funding_monnify_simulation": monnify.monnify_simulation(),
        "payout_provider": payout_provider(),     # which rail sends payouts + name enquiry (wema/kora)
        "payout_live": payout_live(),             # selected payout rail has live keys
        "payments_kora": _kora_live(),            # Kora keys present (payout/enquiry/funding fallback)
        "payout_wema": wema.wema_live(),
        "payout_wema_simulation": wema.wema_simulation(),
        "vtu_vtung": vtu_live(),
        "sms_sendchamp": bool(settings.SENDCHAMP["API_KEY"]),
        "email_resend": bool(settings.RESEND["API_KEY"]),
        "kyc_provider": kyc_provider(),  # which backend verifies BVN/NIN
        "kyc_prembly": _prembly_live(),
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


def kora_diagnose(request):
    """GET /kora-diagnose?token=<KORA_DIAG_TOKEN>[&account=&bank=]

    A browser-accessible Kora connectivity self-test for hosts without shell
    access (e.g. Render's free tier). Mirrors the `kora_check` management command:
    reports config + auth + a sample name-enquiry with Kora's real message, so
    'nothing works' becomes a precise fix. Returns NO secrets.

    Opt-in and protected: 404 unless KORA_DIAG_TOKEN is set, and it must be
    supplied as ?token= (constant-time compared). Set the env var to any secret,
    redeploy, then visit the URL — no shell needed.
    """
    import hmac
    import os

    diag_token = os.environ.get("KORA_DIAG_TOKEN", "").strip()
    if not diag_token:
        return JsonResponse(
            {"detail": "Set KORA_DIAG_TOKEN (any secret value) in the environment to enable this."},
            status=404,
        )
    if not hmac.compare_digest(request.GET.get("token", "").strip(), diag_token):
        return JsonResponse({"detail": "forbidden"}, status=403)
    from utility.kora import kora_diagnostics

    account = "".join(c for c in request.GET.get("account", "") if c.isdigit())[:10] or "0000000000"
    bank = "".join(c for c in request.GET.get("bank", "") if c.isalnum())[:10] or "058"
    return JsonResponse({"kora": kora_diagnostics(account, bank)})


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
    diag_token = os.environ.get("WEMA_DIAG_TOKEN", "").strip()
    if not diag_token:
        return JsonResponse(
            {"detail": "Set WEMA_DIAG_TOKEN (any secret value) in the environment to enable this."},
            status=404,
        )
    supplied = request.GET.get("token", "").strip()
    if not hmac.compare_digest(supplied, diag_token):
        # Length-only hint (no token content) — pinpoints paste truncation/typos.
        return JsonResponse(
            {"detail": "forbidden",
             "hint": f"supplied token has {len(supplied)} chars; the configured "
                     f"WEMA_DIAG_TOKEN has {len(diag_token)}. They must match exactly."},
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
    path("kora-diagnose", kora_diagnose),
    path("wema-diagnose", wema_diagnose),
    path("robots.txt", robots_txt),
    path("admin/", admin.site.urls),
    # Meta calls this exact path (no /api prefix, no trailing slash).
    path("webhooks/whatsapp", whatsapp_webhook),
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
