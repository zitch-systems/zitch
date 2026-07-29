"""Static web surfaces served by Django.

Three browser surfaces share the Zitch brand and the same origin as the API:

  /         marketing landing page (self-contained HTML/CSS/JS)
  /app/     interactive app prototype (embedded by the landing hero iframe)
  /portal/  redirects to the canonical /portal/?mode=demo (see ``portal``)

The pages are plain HTML files under ``pages/`` whose asset references were
rewritten to ``/static/console/...`` at build time, so they are returned
verbatim (no Django template rendering — the files contain ``{...}`` JSX that
must not be parsed as template syntax). Their JS/JSX and image assets live in
``static/console/`` and are served by WhiteNoise/staticfiles.
"""
from pathlib import Path

from django.http import HttpResponse
from django.shortcuts import redirect

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
    # Consolidated into the single /portal/ surface, which serves this very bundle
    # under ?mode=demo behind an explicit mode bar. Two indistinguishable portals
    # was the hazard: same chrome, same twelve-item nav, and no way to tell a
    # fixture balance from a real one. Redirect rather than delete so existing
    # bookmarks and the design-handoff links still land somewhere correct.
    # 302, not 301 — a permanent redirect is cached by the browser and would be
    # painful to walk back if the mock ever needs its own URL again.
    return redirect("/portal/?mode=demo")
