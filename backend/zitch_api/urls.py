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
    from utility.providers import _prembly_live, payments_live, vtu_live

    integrations = {
        "payments_monnify": payments_live(),
        "vtu_vtung": vtu_live(),
        "sms_sendchamp": bool(settings.SENDCHAMP["API_KEY"]),
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
