"""Active liveness for the Tier 2 selfie: turn left, turn right, smile.

Detection runs on the customer's device with MediaPipe Tasks Vision
FaceLandmarker (Apache-2.0), self-hosted under ``static/whatsapp/liveness/`` so
the pages keep ``script-src 'nonce-…'`` and ``connect-src 'self'``. The only CSP
addition is ``'wasm-unsafe-eval'`` (WebAssembly compilation), on these pages
only. The check guides the customer and picks the frames: the clearest straight
frames and the widest point of each head turn. It is not trusted on its own;
our server measures those frames (``wallet.selfie``) before anything reaches the
bank, and the bank's face match stays the identity decision.

Vendored files (``@mediapipe/tasks-vision`` 0.10.21 from npm, model from
storage.googleapis.com/mediapipe-models, float16 v1); ``sourceMappingURL``
comments are stripped so ``collectstatic``'s manifest does not look for maps.
Only the SIMD WebAssembly build is shipped; an engine without SIMD gets the
manual photo. ``VENDORED`` pins their SHA-256 (checked by the tests).
"""
import secrets

from django.shortcuts import render
from django.templatetags.static import static
from django.views.decorators.http import require_GET

ASSET_DIR = "whatsapp/liveness/"

VENDORED = {
    "vision_bundle.js": "de6e7d53d322ad1092d47534a68f70ac9140eee753c7f08380ca820c3320daf9",
    "vision_wasm_internal.js": "4a97e2520ba506c680ecd6ba6acfb146888afa0e2746d57f205352bc6ebb82eb",
    "vision_wasm_internal.wasm": "f00ec4731faa23b3e714d00e88d4d10e2df5c0a427d3a2b4ae6e3526fdd14ef7",
    "face_landmarker.task": "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff",
}


def liveness_assets() -> dict:
    """Static URLs the check loads (hashed names in production)."""
    return {
        "bundle": static(ASSET_DIR + "vision_bundle.js"),
        "loader": static(ASSET_DIR + "vision_wasm_internal.js"),
        "wasm": static(ASSET_DIR + "vision_wasm_internal.wasm"),
        "model": static(ASSET_DIR + "face_landmarker.task"),
    }


def liveness_context(nonce: str) -> dict:
    return {
        "nonce": nonce,
        "liveness_assets": liveness_assets(),
        "liveness_script": static(ASSET_DIR + "liveness.js"),
        "liveness_style": static(ASSET_DIR + "liveness.css"),
    }


def camera_csp(nonce: str) -> str:
    """CSP for a page that runs the camera and the on-device check.

    Nonce-only scripts and styles; every fetch (model, WASM, the form POST) is
    same-origin. ``'wasm-unsafe-eval'`` permits WebAssembly compilation only, not
    ``eval``/``Function``.
    """
    return (
        "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'; "
        f"style-src 'nonce-{nonce}'; "
        f"script-src 'nonce-{nonce}' 'wasm-unsafe-eval'; connect-src 'self'; "
        "img-src data: blob:; media-src blob: mediastream:; object-src 'none'"
    )


def secure_camera_headers(response, nonce: str):
    response["Cache-Control"] = "no-store, private, max-age=0"
    response["Referrer-Policy"] = "no-referrer"
    response["X-Content-Type-Options"] = "nosniff"
    response["X-Frame-Options"] = "DENY"
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response["Permissions-Policy"] = "camera=(self), microphone=(), geolocation=()"
    response["Content-Security-Policy"] = camera_csp(nonce)
    return response


@require_GET
def app_liveness_page(request):
    """The app's in-app check (react-native-webview). No identity, no token and
    no submission here: the frames go back to the app by ``postMessage``; the
    app has our server check them (``/api/wallet/wema/tier2-face-check/``) and
    sends the chosen photo, its proof and BVN/NIN to the bank. Any other page or
    browser that opens this only sees the check run locally."""
    nonce = secrets.token_urlsafe(24)
    response = render(request, "whatsapp/liveness_app.html", liveness_context(nonce))
    return secure_camera_headers(response, nonce)
