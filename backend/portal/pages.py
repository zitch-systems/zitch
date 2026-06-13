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


@require_GET
def admin_portal(request):
    return render(request, "portal/admin.html")
