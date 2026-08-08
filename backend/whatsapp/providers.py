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
        log.warning("wa_send_rejected recipient=%s status=%s error_code=%s",
                    mask_pii(msisdn), getattr(response, "status_code", 0),
                    result.get("error_code"))
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
              screen: str, screen_data: dict, cta: str = "") -> dict:
    """Send an interactive Flow message â€” the secure PIN pad. Opens directly to
    `screen` with `screen_data` (flow_action=navigate); the screen's submit does
    a data_exchange to our endpoint. `flow_token` ties the Flow session back to
    the pending money action. MOCK mode logs and returns success."""
    cfg = getattr(settings, "WHATSAPP_FLOW", {}) or {}
    payload = {"type": "interactive", "interactive": {
        "type": "flow",
        "header": {"type": "text", "text": header[:60]},
        "body": {"text": body[:1024]},
        "action": {
            "name": "flow",
            "parameters": {
                "flow_message_version": "3",
                "flow_token": flow_token,
                "flow_id": cfg.get("FLOW_ID", ""),
                "flow_cta": (cta or cfg.get("CTA", "Confirm with PIN"))[:30],
                "flow_action": "navigate",
                "flow_action_payload": {"screen": screen, "data": screen_data},
            },
        },
    }}
    return _send_payload(msisdn, payload, f"[flow] {header} -> {screen}")


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
        return _message_result(r)
    except requests.RequestException as exc:
        log.warning("wa_template_send_uncertain recipient=%s error_type=%s",
                    mask_pii(msisdn), type(exc).__name__)
        return {"success": False, "uncertain": True,
                "message": "WhatsApp delivery status unknown"}
