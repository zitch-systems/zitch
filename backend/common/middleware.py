"""Production request-boundary middleware."""

from django.conf import settings
from django.http import JsonResponse


class RenderOriginGuardMiddleware:
    """Keep the public Render origin from bypassing the Cloudflare edge.

    Render must still reach the liveness/readiness endpoints on the service's
    ``*.onrender.com`` hostname. Every customer, webhook and operator route is
    expected on ``api.zitch.ng`` and is refused on the origin hostname.
    """

    HEALTH_PATHS = {"/healthz", "/readyz"}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = (request.META.get("HTTP_HOST") or "").split(":", 1)[0].lower()
        if (
            getattr(settings, "BLOCK_RENDER_ORIGIN", False)
            and host.endswith(".onrender.com")
            and request.path not in self.HEALTH_PATHS
        ):
            response = JsonResponse({"detail": "not found"}, status=404)
            response["Cache-Control"] = "no-store"
            return response
        return self.get_response(request)
