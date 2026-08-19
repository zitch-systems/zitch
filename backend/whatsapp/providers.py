"""WhatsApp Cloud API egress + inbound signature check.

Mirrors the rest of Zitch: with no WHATSAPP_TOKEN the channel runs in MOCK mode
(outbound is logged, inbound signatures are accepted) so the whole flow is
testable without a Meta app. Real Graph API calls kick in once keys are set.
"""
import hashlib
import hmac
import logging

import requests
from django.conf import settings

from common.http import mask_pii

log = logging.getLogger("whatsapp")


def _cfg() -> dict:
    return settings.WHATSAPP


def wa_mode() -> str:
    mode = str(_cfg().get("MODE") or "").strip().lower()
    if mode in {"disabled", "sandbox", "live"}:
        return mode
    if _cfg().get("TOKEN") and _cfg().get("PHONE_NUMBER_ID"):
        return "live"
    return "sandbox" if (getattr(settings, "DEBUG", False)
                          or getattr(settings, "TESTING", False)) else "disabled"


def wa_enabled() -> bool:
    return wa_mode() != "disabled"


def wa_live() -> bool:
    return (wa_mode() == "live" and bool(_cfg().get("TOKEN"))
            and bool(_cfg().get("PHONE_NUMBER_ID")))


def _offline_result(kind: str, msisdn: str = "") -> dict:
    if wa_mode() == "disabled":
        return {"success": False, "disabled": True,
                "message": "WhatsApp banking is currently unavailable"}
    log.debug("wa_sandbox_send kind=%s recipient=%s", kind, mask_pii(msisdn))
    return {"success": True, "mock": True, "message_id": ""}


def flows_live() -> bool:
    """True when the secure PIN Flow is fully configured: the channel is live AND
    a published Flow ID + our decryption key are set. Until then the router falls
    back to the SMS confirmation code, so nothing breaks before Meta business
    verification is done (verify-before-live)."""
    flow = getattr(settings, "WHATSAPP_FLOW", {}) or {}
    return bool(wa_live() and flow.get("FLOW_ID") and flow.get("PRIVATE_KEY"))


def verify_signature(raw_body: bytes, header: str) -> bool:
    """Validate Meta's X-Hub-Signature-256 (HMAC-SHA256 of the raw body).

    With no APP_SECRET configured (mock mode) we accept, matching how the money-provider mocks (which accept
    unsigned in mock mode) behave â€” so tests and local runs work unsigned.
    """
    if wa_mode() == "disabled":
        return False
    secret = _cfg().get("APP_SECRET", "")
    if wa_mode() == "sandbox" and not secret:
        return bool(getattr(settings, "DEBUG", False) or getattr(settings, "TESTING", False))
    if not secret:
        # Accept unsigned ONLY when the channel is in mock mode (no live creds) â€”
        # then Meta isn't actually wired and there's no real callback to forge.
        # Once the channel is LIVE we fail closed (reject) on a missing secret, and
        # settings.py raises at boot if APP_SECRET is unset while live, so a
        # production WhatsApp channel can never silently accept a forged callback
        # that would impersonate a linked user's number. (Independent of DEBUG, so
        # the test runner â€” which forces DEBUG=False â€” still exercises mock mode.)
        return False
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1])


def _message_result(response) -> dict:
    """Reduce a Graph response to the non-sensitive fields callers need."""
    try:
        data = response.json() if response.content else {}
    except (TypeError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    error = data.get("error") if isinstance(data.get("error"), dict) else {}
    status = int(getattr(response, "status_code", 0) or 0)
    result = {
        "success": bool(response.ok),
        "message_id": (data.get("messages") or [{}])[0].get("id", ""),
    }
    if not result["success"]:
        result.update({
            # Meta's own words. error_code alone (e.g. a bare 131009) has cost
            # DAYS across this project: the message and error_data.details name
            # the offending parameter — "screen not found", "invalid flow_id" —
            # and carry no customer data, so they are safe to log and return.
            "error_detail": "; ".join(str(x) for x in (
                error.get("message"),
                (error.get("error_data") or {}).get("details")
                if isinstance(error.get("error_data"), dict) else None,
            ) if x)[:300],
            "error_code": error.get("code") or status or "provider_error",
            "message": "WhatsApp provider rejected the request",
            # A 429 is an explicit refusal and is safe to retry. A 5xx may have
            # accepted the message before failing, so automatic retry could duplicate it.
            "retryable": status == 429,
            "uncertain": status >= 500 or status == 0,
        })
    return result


def send_text(msisdn: str, text: str) -> dict:
    """Send a plain-text WhatsApp message. Returns {success, message_id?, ...}."""
    if not wa_live():
        return _offline_result("text", msisdn)
    url = f"{_cfg()['BASE_URL']}/{_cfg()['PHONE_NUMBER_ID']}/messages"
    headers = {"Authorization": f"Bearer {_cfg()['TOKEN']}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": msisdn,
        "type": "text",
        "text": {"body": text[:4096]},
    }
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=15)
        return _log_if_rejected(r, msisdn)
    except requests.RequestException as exc:
        log.warning("wa_send_failed recipient=%s error_type=%s",
                    mask_pii(msisdn), type(exc).__name__)
        return {"success": False, "message": "WhatsApp delivery failed"}


def _log_if_rejected(response, msisdn: str) -> dict:
    """Reduce the response AND, unlike a bare `_message_result` call, make a
    non-2xx failure observable. It never raises, so `reply()` and the inbound job
    both read "no exception" as success — without this log line, Meta refusing a
    send (expired WHATSAPP_TOKEN, recipient not on the app's allowed-testers list,
    etc.) leaves no trace anywhere: not in the logs, not in WaMessageLog, not in
    any health counter."""
    result = _message_result(response)
    if not result["success"]:
        log.warning("wa_send_rejected recipient=%s status=%s error_code=%s detail=%r",
                    mask_pii(msisdn), getattr(response, "status_code", 0),
                    result.get("error_code"), result.get("error_detail", ""))
    return result


def _send_payload(msisdn: str, payload: dict, mock_note: str) -> dict:
    """POST an arbitrary message payload to the Cloud API (shared by the
    interactive senders). MOCK mode logs and returns success."""
    if not wa_live():
        return _offline_result(str(payload.get("type") or "interactive"), msisdn)
    url = f"{_cfg()['BASE_URL']}/{_cfg()['PHONE_NUMBER_ID']}/messages"
    headers = {"Authorization": f"Bearer {_cfg()['TOKEN']}", "Content-Type": "application/json"}
    try:
        r = requests.post(url, json={"messaging_product": "whatsapp", "to": msisdn, **payload},
                          headers=headers, timeout=15)
        return _log_if_rejected(r, msisdn)
    except requests.RequestException as exc:
        log.warning("wa_send_failed recipient=%s error_type=%s",
                    mask_pii(msisdn), type(exc).__name__)
        return {"success": False, "message": "WhatsApp delivery failed"}


def send_buttons(msisdn: str, body: str, buttons: list) -> dict:
    """Interactive reply-buttons message (max 3). ``buttons`` = [(id, title), ...] â€”
    tapping one delivers the id back to the webhook, so ids should be the exact
    text the router already understands (e.g. "airtime")."""
    payload = {"type": "interactive", "interactive": {
        "type": "button", "body": {"text": body[:1024]},
        "action": {"buttons": [{"type": "reply", "reply": {"id": str(bid)[:200], "title": str(title)[:20]}}
                               for bid, title in buttons[:3]]}}}
    return _send_payload(msisdn, payload, f"[buttons] {body} {[b[0] for b in buttons]}")


def send_cta_url(msisdn: str, body: str, url: str, cta: str = "Open",
                 header: str = "", footer: str = "") -> dict:
    """Interactive CTA-URL message — a labelled BUTTON that opens `url`.

    Used instead of pasting the link into the body. A bare https:// URL in a bank's
    chat is the exact shape of a phishing message, and one carrying the customer's
    own BVN in its query string is worse: WhatsApp renders it as tappable text with
    the whole query visible, and it stays in their history. The CTA button shows the
    label and hides the target, and it opens in WhatsApp's own in-app browser rather
    than handing the customer to whatever app claims https.

    Falls back to a plain text message if the Cloud API rejects the interactive type
    (older API versions do), because a link the customer can still tap beats a
    verification step that silently never arrives.
    """
    interactive = {"type": "cta_url",
                   "body": {"text": body[:1024]},
                   "action": {"name": "cta_url",
                              "parameters": {"display_text": (cta or "Open")[:20], "url": url}}}
    if header:
        interactive["header"] = {"type": "text", "text": header[:60]}
    if footer:
        interactive["footer"] = {"text": footer[:60]}
    res = _send_payload(msisdn, {"type": "interactive", "interactive": interactive},
                        f"[cta_url] {cta} -> {url}")
    if res.get("success"):
        return res
    log.warning("wa_cta_url_rejected recipient=%s — falling back to a text link",
                mask_pii(msisdn))
    return send_text(msisdn, f"{body}\n\n{url}")


def send_list(msisdn: str, body: str, rows: list,
              button_label: str = "Choose", section_title: str = "Options") -> dict:
    """Interactive list message (max 10 rows). ``rows`` = [(id, title, description)] â€”
    tapping a row delivers its id to the webhook (ids = text the router expects)."""
    payload = {"type": "interactive", "interactive": {
        "type": "list", "body": {"text": body[:1024]},
        "action": {"button": button_label[:20], "sections": [{
            "title": section_title[:24],
            "rows": [{"id": str(rid)[:200], "title": str(title)[:24],
                      "description": str(desc or "")[:72]}
                     for rid, title, desc in rows[:10]]}]}}}
    return _send_payload(msisdn, payload, f"[list] {body} {[r[0] for r in rows]}")


def send_flow(msisdn: str, flow_token: str, header: str, body: str,
              screen: str, screen_data: dict, cta: str = "",
              on_open: str = "navigate") -> dict:
    """Send an interactive Flow message - the secure PIN pad.

    `on_open` decides WHO picks the first screen when the customer taps the
    button, and the difference matters more than it looks:

    * ``navigate`` bakes `screen` and `screen_data` INTO the message. WhatsApp
      renders them on the device with no request to us, so the card opens
      instantly and opens even if our endpoint is down. It also means the card
      never stops opening. A WhatsApp message cannot be recalled, expired or
      disabled by the business that sent it, so a card from last week still
      opens with last week's amount - and the customer can type their PIN into a
      payment that has already completed before anything tells them otherwise.
    * ``data_exchange`` sends no screen at all. Tapping the button calls our
      endpoint (action ``INIT``) and OUR reply picks the screen, so server state
      decides: a card whose payment is finished answers with the terminal screen
      instead of a PIN pad.

    The money confirm therefore uses data_exchange; everything else keeps
    navigate. The trade is that opening the card now needs the endpoint, so if it
    is down the card will not open. That is not a NEW dependency - the endpoint
    has always been required to complete a payment - it just fails before a PIN
    is typed instead of after, which is the better half of the same outage.

    `screen` is still passed either way: rejection logging is keyed on it, and
    the fields the caller persisted are what INIT answers with.
    """
    cfg = getattr(settings, "WHATSAPP_FLOW", {}) or {}
    parameters = {
        "flow_message_version": "3",
        "flow_token": flow_token,
        "flow_id": cfg.get("FLOW_ID", ""),
        "flow_cta": (cta or cfg.get("CTA", "Confirm with PIN"))[:30],
        "flow_action": on_open,
    }
    if on_open == "navigate":
        # Only legal for navigate. Naming a screen alongside data_exchange is a
        # contradiction - the endpoint is the thing being asked which screen to
        # show - and Meta rejects the message for it.
        parameters["flow_action_payload"] = {"screen": screen, "data": screen_data}
    payload = {"type": "interactive", "interactive": {
        "type": "flow",
        "header": {"type": "text", "text": header[:60]},
        "body": {"text": body[:1024]},
        "action": {"name": "flow", "parameters": parameters},
    }}
    res = _send_payload(msisdn, payload, f"[flow] {header} -> {screen}")
    if not res.get("success"):
        _record_flow_rejection(screen, res)
    return res


#: Where the last Flow rejection is parked so /healthz can report it.
LAST_FLOW_ERROR_KEY = "wa_last_flow_error"


def _record_flow_rejection(screen: str, res: dict) -> None:
    """Park Meta's rejection where an operator can actually reach it.

    The reason is already logged, but reading it means shell access to the
    host's log stream while reproducing the failure — which is why diagnosing
    "the secure entry screen didn't go through" has repeatedly meant guessing
    from symptoms instead. The rejection is about OUR request (a screen name, a
    flow id, a parameter), so it carries no customer data; the recipient is
    deliberately not stored.
    """
    from django.utils import timezone

    try:
        from .models import SystemSetting

        SystemSetting.set(LAST_FLOW_ERROR_KEY, "|".join((
            timezone.now().isoformat(timespec="seconds"),
            str(screen),
            str(res.get("error_code", "")),
            str(res.get("error_detail", ""))[:150],
        ))[:255])
    except Exception:  # noqa: BLE001 — never let diagnostics break a send path
        log.debug("could not record flow rejection", exc_info=True)


def last_flow_error() -> dict:
    """The most recent Flow send Meta refused: when, which screen, and its own
    words. Empty when no Flow send has ever been rejected."""
    try:
        from .models import SystemSetting

        raw = SystemSetting.get(LAST_FLOW_ERROR_KEY, "")
    except Exception:  # noqa: BLE001
        return {}
    if not raw:
        return {}
    parts = (raw.split("|", 3) + ["", "", "", ""])[:4]
    return {"at": parts[0], "screen": parts[1], "code": parts[2], "detail": parts[3]}


def send_image(msisdn: str, image_url: str, caption: str = "") -> dict:
    """Send an image message (e.g. a biller/bank logo or the Zitch mark) with an
    optional caption. Meta fetches the image from `image_url`, so it must be a
    public URL. MOCK mode logs and returns success."""
    if not wa_live():
        return _offline_result("image", msisdn)
    url = f"{_cfg()['BASE_URL']}/{_cfg()['PHONE_NUMBER_ID']}/messages"
    headers = {"Authorization": f"Bearer {_cfg()['TOKEN']}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": msisdn,
        "type": "image",
        "image": {"link": image_url, "caption": caption[:1024]},
    }
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=15)
        return _log_if_rejected(r, msisdn)
    except requests.RequestException as exc:
        log.warning("wa_image_send_failed recipient=%s error_type=%s",
                    mask_pii(msisdn), type(exc).__name__)
        return {"success": False, "message": "WhatsApp delivery failed"}


def upload_media(data: bytes, mime: str, filename: str) -> str:
    """Upload bytes to the WhatsApp Cloud media store and return the media id
    (empty string on failure / mock). The id is single-account, short-lived, and
    referenced by a subsequent message send."""
    if not wa_live():
        if wa_mode() == "sandbox":
            log.debug("wa_sandbox_upload mime=%s bytes=%s", mime, len(data))
            return "mock-media-id"
        return ""
    url = f"{_cfg()['BASE_URL']}/{_cfg()['PHONE_NUMBER_ID']}/media"
    try:
        r = requests.post(
            url,
            headers={"Authorization": f"Bearer {_cfg()['TOKEN']}"},
            data={"messaging_product": "whatsapp", "type": mime},
            files={"file": (filename, data, mime)},
            timeout=30,
        )
        return (r.json() if r.content else {}).get("id", "") if r.ok else ""
    except requests.RequestException as exc:
        log.warning("wa_media_upload_failed error_type=%s", type(exc).__name__)
        return ""


#: The most we will pull down for one inbound voice note or photo. Meta caps
#: uploads well above this (16MB audio, 5MB images), but we are about to hold the
#: bytes in memory on a worker and then base64 them into a model request, which
#: inflates them by a third. A customer cannot say anything about a payment that
#: needs more than this, and a cap is the difference between a big message and an
#: OOM on a dyno that is also serving /healthz.
MAX_INBOUND_MEDIA = 8 * 1024 * 1024


def download_media(media_id: str) -> tuple[bytes, str]:
    """Fetch an inbound media object by id -> (bytes, mime). ("", "") on failure.

    Two hops, both authenticated: Meta gives a short-lived, single-use URL from
    the id, and the URL itself still needs the bearer token — an unauthenticated
    GET of it returns 401, which is the trap this function exists to hide.

    Never raises. A voice note we cannot fetch is answered by asking the customer
    to type it, which is a far better outcome than a webhook retry storm.
    """
    if not media_id or not wa_live():
        return b"", ""
    auth = {"Authorization": f"Bearer {_cfg()['TOKEN']}"}
    try:
        meta = requests.get(f"{_cfg()['BASE_URL']}/{media_id}", headers=auth, timeout=15)
        if not meta.ok:
            log.warning("wa_media_lookup_failed status=%s", meta.status_code)
            return b"", ""
        info = meta.json() if meta.content else {}
        url = info.get("url") or ""
        if not url:
            return b"", ""
        # Streamed and capped rather than read whole: `file_size` is Meta's claim
        # about the object, not a promise about the bytes on the wire, and this
        # runs on a shared worker.
        with requests.get(url, headers=auth, timeout=30, stream=True) as resp:
            if not resp.ok:
                log.warning("wa_media_fetch_failed status=%s", resp.status_code)
                return b"", ""
            chunks, total = [], 0
            for chunk in resp.iter_content(64 * 1024):
                total += len(chunk)
                if total > MAX_INBOUND_MEDIA:
                    log.warning("wa_media_too_large bytes_over=%s", MAX_INBOUND_MEDIA)
                    return b"", ""
                chunks.append(chunk)
        mime = (info.get("mime_type") or resp.headers.get("Content-Type") or "").split(";")[0]
        return b"".join(chunks), mime.strip()
    except requests.RequestException as exc:
        log.warning("wa_media_download_failed error_type=%s", type(exc).__name__)
        return b"", ""


def send_image_media(msisdn: str, media_id: str, caption: str = "") -> dict:
    """Send a previously-uploaded image by media id, so it renders INLINE in the
    chat rather than as a file card. `send_image` posts a public link instead;
    this one is for bytes we generated ourselves and never published."""
    image = {"id": media_id}
    if caption:
        image["caption"] = caption[:1024]
    return _send_payload(msisdn, {"type": "image", "image": image}, "[image] receipt")


def send_document(msisdn: str, media_id: str, filename: str, caption: str = "") -> dict:
    """Send a previously-uploaded document (e.g. a receipt JPEG) as a downloadable
    file with `filename` and an optional caption."""
    doc = {"id": media_id, "filename": filename[:240]}
    if caption:
        doc["caption"] = caption[:1024]
    return _send_payload(msisdn, {"type": "document", "document": doc},
                         f"[document] {filename}")


def send_template(msisdn: str, template_name: str, params: list | None = None, lang: str = "en_US") -> dict:
    """Send a pre-approved template message (used for broadcasts outside the
    24-hr window). MOCK mode logs and returns success."""
    if not wa_live():
        result = _offline_result("template", msisdn)
        if result.get("success"):
            result["message_id"] = "mock-template"
        return result
    components = (
        [{"type": "body", "parameters": [{"type": "text", "text": str(p)} for p in params]}]
        if params else []
    )
    payload = {
        "messaging_product": "whatsapp", "to": msisdn, "type": "template",
        "template": {"name": template_name, "language": {"code": lang}, "components": components},
    }
    try:
        r = requests.post(
            f"{_cfg()['BASE_URL']}/{_cfg()['PHONE_NUMBER_ID']}/messages",
            json=payload,
            headers={"Authorization": f"Bearer {_cfg()['TOKEN']}", "Content-Type": "application/json"},
            timeout=15,
        )
        # Route through _log_if_rejected like every other send path. Without it
        # an HTTP-level refusal (unapproved/paused template, parameter-count
        # mismatch, expired token) produced no log line at all — only a
        # per-recipient error_code on the broadcast row — so a campaign failing
        # for a reason Meta spelled out was diagnosable only by querying rows.
        return _log_if_rejected(r, msisdn)
    except requests.RequestException as exc:
        log.warning("wa_template_send_uncertain recipient=%s error_type=%s",
                    mask_pii(msisdn), type(exc).__name__)
        return {"success": False, "uncertain": True,
                "message": "WhatsApp delivery status unknown"}


def published_flow_report() -> dict:
    """What Meta's PUBLISHED Flow actually contains, versus what this code sends.

    This is the one reading nothing else provides. `whatsapp_flow_ready` says a
    Flow ID and a key are configured; it cannot say whether the Flow behind that
    ID has the screen a send names. When it doesn't, Meta rejects the send and
    the customer sees only "the secure entry screen didn't go through" — a
    symptom that looks identical to a bad token, an unverified business, or an
    expired session, and which has repeatedly cost days to tell apart.

    The failure is structural, not exotic: the Flow JSON lives in this repo but
    is *published by hand* in WhatsApp Manager, so every screen added here is
    missing on Meta's side until someone pastes and publishes. Screens added
    earliest (PIN_SCREEN) keep working while the newest ones (IDENTITY_SCREEN,
    TRANSFER_FORM) fail — which reads like "some features are broken" rather
    than "the Flow is one publish behind".

    Returns {status, published_screens, missing_screens, stale, ...}; never
    raises, because a health probe that raises is worse than no probe.
    """
    import json
    from pathlib import Path

    flow = getattr(settings, "WHATSAPP_FLOW", {}) or {}
    flow_id = flow.get("FLOW_ID")
    if not (wa_live() and flow_id):
        return {"status": "unconfigured"}

    asset = Path(__file__).resolve().parent / "flow_assets" / "pin_flow.json"
    expected_props: dict = {}
    try:
        local_screens = json.loads(asset.read_text())["screens"]
        expected = [s["id"] for s in local_screens]
        expected_props = {s["id"]: set((s.get("data") or {}).keys()) for s in local_screens}
    except Exception:  # noqa: BLE001
        expected = []

    base = _cfg().get("BASE_URL", "https://graph.facebook.com/v21.0")
    headers = {"Authorization": f"Bearer {_cfg()['TOKEN']}"}
    try:
        meta = requests.get(f"{base}/{flow_id}",
                            params={"fields": "id,name,status,validation_errors"},
                            headers=headers, timeout=15)
        info = meta.json() if meta.content else {}
        if meta.status_code >= 400:
            err = (info.get("error") or {})
            return {"status": "error", "expected_screens": expected,
                    "detail": str(err.get("message") or meta.status_code)[:300]}

        # The published screens live in the FLOW_JSON asset, not on the node.
        assets = requests.get(f"{base}/{flow_id}/assets", headers=headers, timeout=15)
        published, published_props = [], {}
        for item in (assets.json().get("data") or []) if assets.content else []:
            if item.get("asset_type") != "FLOW_JSON" or not item.get("download_url"):
                continue
            body = requests.get(item["download_url"], timeout=15)
            screens = body.json().get("screens") or []
            published = [s["id"] for s in screens]
            published_props = {s["id"]: set((s.get("data") or {}).keys()) for s in screens}
            break
    except Exception as exc:  # noqa: BLE001 — never raise from a probe
        return {"status": "unreachable", "expected_screens": expected,
                "detail": type(exc).__name__}

    missing = [s for s in expected if s not in published]
    # A screen can be present on BOTH sides and still be one publish behind: the
    # endpoint must supply exactly the properties the published screen declares,
    # so adding one here (SUCCESS gaining `status`) makes Meta reject the answer
    # with the same "Couldn't load content" the customer sees for a timeout.
    # Comparing ids alone reported a perfectly healthy Flow through exactly that
    # outage, which is the whole reason this probe exists — so it compares the
    # declared property sets too. Both directions matter: a property the code
    # sends and Meta does not declare fails, and so does the reverse.
    drifted = sorted(
        sid for sid in expected
        if sid in published_props and expected_props.get(sid, set()) != published_props[sid]
    )
    return {
        "status": str(info.get("status") or "unknown").lower(),   # published | draft | ...
        "name": info.get("name"),
        "published_screens": published,
        "expected_screens": expected,
        # The actionable line: these screens exist in the code and NOT on Meta,
        # so every send naming one of them is rejected until a re-publish.
        "missing_screens": missing,
        # Present on both sides, but their `data` contracts disagree.
        "drifted_screens": drifted,
        "stale": bool(missing or drifted),
        "validation_errors": [e.get("error_type") or e.get("message")
                              for e in (info.get("validation_errors") or [])][:5],
    }
