"""The public web surfaces: marketing landing, interactive prototype, and the
operator portal SPA. All three are the design-handoff references served from
templates/static (no build step) — the portal swaps the handoff's mock data
layer for the live /api/ops/ endpoints."""
from django.shortcuts import render
from django.views.decorators.http import require_GET


@require_GET
def landing(request):
    return render(request, "portal/landing.html")


@require_GET
def prototype(request):
    return render(request, "portal/prototype.html")


# The operator portal ships two data layers: the live one over /api/ops/, and the
# design-handoff mock that renders entirely from a fixture file. They used to be
# two separate URLs — /portal/ and /console/portal/ — with identical chrome and an
# identical twelve-item nav, and nothing on screen said which you were looking at.
# A fixture balance read exactly like a real one. One URL with an explicit mode
# makes that mistake impossible and keeps the demo bookmarkable.
DEMO_MODE = "demo"


@require_GET
def admin_portal(request):
    return render(request, "portal/admin.html",
                  {"demo": request.GET.get("mode") == DEMO_MODE})
