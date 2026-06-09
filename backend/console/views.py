"""Static web surfaces served by Django.

Three browser surfaces share the Zitch brand and the same origin as the API:

  /         marketing landing page (self-contained HTML/CSS/JS)
  /app/     interactive app prototype (embedded by the landing hero iframe)
  /portal/  operator / admin portal (React-in-browser; talks to /api/admin/)

The pages are plain HTML files under ``pages/`` whose asset references were
rewritten to ``/static/console/...`` at build time, so they are returned
verbatim (no Django template rendering — the files contain ``{...}`` JSX that
must not be parsed as template syntax). Their JS/JSX and image assets live in
``static/console/`` and are served by WhiteNoise/staticfiles.
"""
from pathlib import Path

from django.http import HttpResponse

_PAGES = Path(__file__).resolve().parent / "pages"


def _page(name: str) -> HttpResponse:
    html = (_PAGES / name).read_text(encoding="utf-8")
    resp = HttpResponse(html)
    # Marketing/portal HTML is fine to cache briefly at the edge; the heavy
    # assets are hashed under /static and cached aggressively by WhiteNoise.
    resp["Cache-Control"] = "public, max-age=300"
    return resp


def landing(_request):
    return _page("landing.html")


def app_prototype(_request):
    return _page("app.html")


def portal(_request):
    return _page("portal.html")
