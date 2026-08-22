"""The hosted QR scanner — a camera, opened from a WhatsApp button.

WhatsApp cannot launch a camera from a message. It CAN open a URL, and a web page
can open the camera, so the scanner is a page we serve: tap the button in the chat,
the camera comes up immediately, and the decoded code goes back to the server that
started the conversation. No "take a photo and send it to me", no round trip through
a chat message.

Decoding stays on the SERVER. The page ships no QR library at all — it grabs frames
and posts them, and utility.qr does the reading. That is deliberate:

  * one decoder, already tested, shared with the app and the media path, rather than
    a second implementation in JavaScript that can disagree with it about what a
    payload says;
  * it works on browsers with no BarcodeDetector, which includes every iPhone;
  * and a client that cannot decode also cannot lie about what it decoded — the
    frame is the evidence, and a page nobody can trust never gets to assert an
    account number.

The page has two paths because in-app browsers vary: a live camera preview where
getUserMedia is allowed, and a plain capture button — `<input capture>` — where it
is not. The second is not a degraded mode so much as a guarantee: it opens the
native camera on every phone, including WhatsApp's iOS browser.
"""
import logging
import secrets
from datetime import timedelta

from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from common.ratelimit import ratelimit
from utility.qr import decode_image, parse_payment

from .models import ScanSession

log = logging.getLogger("whatsapp")

SCAN_TTL_MINUTES = 15
#: A downscaled camera frame is tens of kilobytes. This bounds a hostile upload
#: without being anywhere near a real photo.
MAX_FRAME_BYTES = 3 * 1024 * 1024


def new_scan_session(user, msisdn: str) -> ScanSession:
    """Mint a scanner link for one customer. Any earlier unused one is retired so a
    stale link in the chat cannot be reused after they start again."""
    ScanSession.objects.filter(msisdn=msisdn, used_at__isnull=True).update(
        used_at=timezone.now())
    return ScanSession.objects.create(
        token=secrets.token_urlsafe(32)[:64], msisdn=msisdn, user=user,
        expires_at=timezone.now() + timedelta(minutes=SCAN_TTL_MINUTES),
    )


def scan_url(session: ScanSession) -> str:
    from django.conf import settings
    base = (settings.ZITCH_LINKS.get("API_BASE", "") or "").rstrip("/")
    return f"{base}/scan/{session.token}"


def scan_page(request, token: str):
    """The scanner itself. Served to the customer's browser from a WhatsApp button."""
    session = ScanSession.objects.filter(token=token).first()
    if session is None or not session.usable:
        return HttpResponse(_SHELL.format(body=_EXPIRED, token=""),
                            content_type="text/html; charset=utf-8", status=410)
    return HttpResponse(_SHELL.format(body=_SCANNER, token=token),
                        content_type="text/html; charset=utf-8")


@csrf_exempt
@ratelimit("qr_scan_frame", limit=120, window=120)
def scan_frame(request, token: str):
    """One camera frame -> decoded, or "keep looking".

    Answers `{"found": false}` for a frame with no code in it, which is the normal
    case many times a second — the page keeps sending until something is found or
    the customer gives up.
    """
    if request.method != "POST":
        return JsonResponse({"found": False, "error": "method"}, status=405)
    session = ScanSession.objects.filter(token=token).first()
    if session is None or timezone.now() >= session.expires_at:
        return JsonResponse({"found": False, "expired": True}, status=410)
    if session.used_at is not None:
        # Already scanned, not expired. The page fires frames continuously, so the
        # winning frame is routinely followed by one already in flight — telling
        # that customer their link "expired" a second after it worked would be a
        # confusing lie about a scan that succeeded.
        return JsonResponse({"found": True, "payable": False,
                             "message": "Already scanned — check WhatsApp."})

    data = request.body or b""
    if len(data) > MAX_FRAME_BYTES:
        return JsonResponse({"found": False, "error": "too_large"}, status=413)

    decoded = decode_image(data)
    if not decoded:
        return JsonResponse({"found": False})
    intent = parse_payment(decoded)
    if intent.get("kind") == "other":
        # Decoded cleanly, but it is a website or a WiFi code. Say so on the page
        # rather than sending the customer back to a chat that has nothing to add.
        return JsonResponse({"found": True, "payable": False,
                             "message": "That's not a payment code."})

    # One scan per link. Marked before the chat is touched, so a page that keeps
    # firing frames — or a retried request — cannot raise two confirm cards.
    # Conditional, so two frames that both decode cannot both hand off: the second
    # UPDATE matches no rows and returns without touching the chat.
    updated = ScanSession.objects.filter(pk=session.pk, used_at__isnull=True).update(
        used_at=timezone.now())
    if not updated:
        return JsonResponse({"found": True, "payable": bool(intent.get("payable")),
                             "message": "Already scanned — check WhatsApp."})

    log.info("wa_scan_decoded msisdn=%s kind=%s payable=%s",
             session.msisdn[-4:], intent.get("kind"), intent.get("payable"))
    try:
        from .router import handle_scanned_qr

        handle_scanned_qr(session.msisdn, intent)
    except Exception:  # noqa: BLE001 — the page must still tell them what happened
        log.exception("scan handoff failed for %s", session.msisdn[-4:])
        return JsonResponse({"found": True, "payable": False,
                             "message": "Scanned — but I couldn't reach your chat. "
                                        "Open WhatsApp and try again."})
    return JsonResponse({"found": True, "payable": bool(intent.get("payable")),
                         "message": "Got it — check WhatsApp to continue."})


# --------------------------------------------------------------------------- #
# The page. Inline and dependency-free: this is opened inside WhatsApp's browser
# on a Nigerian mobile connection, so every extra request is a chance to show a
# customer a half-loaded scanner.
# --------------------------------------------------------------------------- #
_SHELL = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex,nofollow">
<title>Scan a code · Zitch</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:#03304C; color:#fff; min-height:100vh;
         font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
         display:flex; flex-direction:column; align-items:center; }}
  header {{ padding:18px 20px 8px; font-weight:700; font-size:17px; }}
  p.sub {{ margin:0 20px 14px; opacity:.75; font-size:13.5px; text-align:center; line-height:1.5; }}
  #stage {{ position:relative; width:min(92vw,420px); aspect-ratio:1/1; border-radius:20px;
            overflow:hidden; background:#00243a; }}
  video, canvas {{ width:100%; height:100%; object-fit:cover; display:block; }}
  #reticle {{ position:absolute; inset:12%; border:3px solid rgba(255,255,255,.85);
              border-radius:16px; pointer-events:none; }}
  .btn {{ display:inline-flex; align-items:center; justify-content:center; gap:8px;
          margin:16px 20px 0; padding:15px 22px; border-radius:14px; border:0;
          background:#0FA295; color:#fff; font-size:16px; font-weight:700;
          text-decoration:none; width:min(92vw,420px); }}
  /* The browser's own [hidden]{{display:none}} rule and this class rule have
     equal specificity, and an author stylesheet loads after the UA one — so
     without this, `hidden` on a .btn is silently ignored and it shows
     anyway. #pickWrap starts hidden and needs this to actually stay hidden
     until offerCapture() reveals it. */
  .btn[hidden] {{ display:none; }}
  .btn.ghost {{ background:transparent; border:1.5px solid rgba(255,255,255,.35); }}
  #status {{ margin:16px 20px 32px; font-size:14.5px; text-align:center; min-height:22px;
             line-height:1.5; }}
  input[type=file] {{ display:none; }}
</style></head><body>{body}
<script>window.__TOKEN__ = "{token}";</script>
</body></html>"""

_EXPIRED = """
<header>Link expired</header>
<p class="sub">This scanner link has already been used, or it timed out.
Go back to WhatsApp and reply <b>11</b> to start a new scan.</p>
"""

_SCANNER = """
<header>Scan a payment code</header>
<p class="sub">Point your camera at the QR code. It can be from any bank.</p>
<div id="stage"><video id="v" playsinline muted autoplay></video><div id="reticle"></div></div>
<label class="btn ghost" id="pickWrap" hidden>Use my camera
  <input type="file" id="pick" accept="image/*" capture="environment">
</label>
<div id="status">Starting the camera…</div>
<script>
(function () {
  var token = window.__TOKEN__;
  var v = document.getElementById('v');
  var statusEl = document.getElementById('status');
  var pickWrap = document.getElementById('pickWrap');
  var pick = document.getElementById('pick');
  var canvas = document.createElement('canvas');
  var stopped = false;

  function say(t) { statusEl.textContent = t; }

  function finish(data) {
    stopped = true;
    try { (v.srcObject ? v.srcObject.getTracks() : []).forEach(function (t) { t.stop(); }); } catch (e) {}
    say(data.message || 'Got it — check WhatsApp to continue.');
  }

  function send(blob) {
    return fetch('/scan/' + token + '/frame', { method: 'POST', body: blob })
      .then(function (r) { return r.json().then(function (j) { j.status = r.status; return j; }); });
  }

  // The live path: grab a frame, post it, let the server read it. No QR library
  // ships with this page — the decoder that already reads codes for the app and
  // the chat is the one that reads them here too.
  function tick() {
    if (stopped) return;
    if (!v.videoWidth) { return setTimeout(tick, 300); }
    var side = Math.min(v.videoWidth, v.videoHeight);
    canvas.width = canvas.height = Math.min(side, 720);
    canvas.getContext('2d').drawImage(
      v, (v.videoWidth - side) / 2, (v.videoHeight - side) / 2, side, side,
      0, 0, canvas.width, canvas.height);
    canvas.toBlob(function (blob) {
      if (!blob || stopped) return;
      send(blob).then(function (res) {
        if (res.expired) { return finish({ message: 'This link expired. Reply 11 in WhatsApp to scan again.' }); }
        if (res.found) { return finish(res); }
        setTimeout(tick, 700);
      }).catch(function () { setTimeout(tick, 1200); });
    }, 'image/jpeg', 0.7);
  }

  // The guaranteed path. `capture` opens the native camera on every phone, which
  // matters because in-app browsers do not all allow a live preview — WhatsApp's
  // on iOS among them. It is one photo instead of a stream, and it always works.
  function offerCapture(reason) {
    pickWrap.hidden = false;
    say(reason);
  }
  pick.addEventListener('change', function () {
    var file = pick.files && pick.files[0];
    if (!file) return;
    say('Reading the code…');
    send(file).then(function (res) {
      if (res.found) return finish(res);
      say("I couldn't find a code in that photo. Try again, closer and in better light.");
    }).catch(function () { say('Network problem — try once more.'); });
  });

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    return offerCapture('Tap below to open your camera.');
  }
  navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } } })
    .then(function (stream) {
      v.srcObject = stream;
      say('Hold the code inside the box.');
      tick();
    })
    .catch(function () {
      offerCapture('Tap below to open your camera.');
    });
})();
</script>
"""
