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

log = logging.getLogger("whatsapp")


def _cfg() -> dict:
    return settings.WHATSAPP


def wa_live() -> bool:
    return bool(_cfg().get("TOKEN") and _cfg().get("PHONE_NUMBER_ID"))


def flows_live() -> bool:
    """True when the secure PIN Flow is fully configured: the channel is live AND
    a published Flow ID + our decryption key are set. Until then the router falls
    back to the SMS confirmation code, so nothing breaks before Meta business
    verification is done (verify-before-live)."""
    flow = getattr(settings, "WHATSAPP_FLOW", {}) or {}
    return bool(wa_live() and flow.get("FLOW_ID") and flow.get("PRIVATE_KEY"))


def verify_signature(raw_body: bytes, header: str) -> bool:
    """Validate Meta's X-Hub-Signature-256 (HMAC-SHA256 of the raw body).

    With no APP_SECRET configured (mock mode) we accept, matching how the Kora
    webhook behaves without keys — so tests and local runs work unsigned.
    """
    secret = _cfg().get("APP_SECRET", "")
    if not secret:
        # Accept unsigned ONLY when the channel is in mock mode (no live creds) —
        # then Meta isn't actually wired and there's no real callback to forge.
        # Once the channel is LIVE we fail closed (reject) on a missing secret, and
        # settings.py raises at boot if APP_SECRET is unset while live, so a
        # production WhatsApp channel can never silently accept a forged callback
        # that would impersonate a linked user's number. (Independent of DEBUG, so
        # the test runner — which forces DEBUG=False — still exercises mock mode.)
        return not wa_live()
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1])


def send_text(msisdn: str, text: str) -> dict:
    """Send a plain-text WhatsApp message. Returns {success, message_id?, ...}."""
    if not wa_live():
        log.info("[wa-mock] -> %s: %s", msisdn, text)
        return {"success": True, "mock": True, "message_id": ""}
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
        data = r.json() if r.content else {}
        return {
            "success": r.ok,
            "message_id": (data.get("messages") or [{}])[0].get("id", ""),
            "raw": data,
        }
    except requests.RequestException as exc:
        log.warning("wa send failed -> %s: %s", msisdn, exc)
        return {"success": False, "message": str(exc)}


def _send_payload(msisdn: str, payload: dict, mock_note: str) -> dict:
    """POST an arbitrary message payload to the Cloud API (shared by the
    interactive senders). MOCK mode logs and returns success."""
    if not wa_live():
        log.info("[wa-mock] -> %s: %s", msisdn, mock_note)
        return {"success": True, "mock": True, "message_id": ""}
    url = f"{_cfg()['BASE_URL']}/{_cfg()['PHONE_NUMBER_ID']}/messages"
    headers = {"Authorization": f"Bearer {_cfg()['TOKEN']}", "Content-Type": "application/json"}
    try:
        r = requests.post(url, json={"messaging_product": "whatsapp", "to": msisdn, **payload},
                          headers=headers, timeout=15)
        data = r.json() if r.content else {}
        return {"success": r.ok, "message_id": (data.get("messages") or [{}])[0].get("id", ""),
                "raw": data}
    except requests.RequestException as exc:
        log.warning("wa send failed -> %s: %s", msisdn, exc)
        return {"success": False, "message": str(exc)}


def send_buttons(msisdn: str, body: str, buttons: list) -> dict:
    """Interactive reply-buttons message (max 3). ``buttons`` = [(id, title), ...] —
    tapping one delivers the id back to the webhook, so ids should be the exact
    text the router already understands (e.g. "airtime")."""
    payload = {"type": "interactive", "interactive": {
        "type": "button", "body": {"text": body[:1024]},
        "action": {"buttons": [{"type": "reply", "reply": {"id": str(bid)[:200], "title": str(title)[:20]}}
                               for bid, title in buttons[:3]]}}}
    return _send_payload(msisdn, payload, f"[buttons] {body} {[b[0] for b in buttons]}")


def send_list(msisdn: str, body: str, rows: list,
              button_label: str = "Choose", section_title: str = "Options") -> dict:
    """Interactive list message (max 10 rows). ``rows`` = [(id, title, description)] —
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
    """Send an interactive Flow message — the secure PIN pad. Opens directly to
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
        log.info("[wa-mock] image -> %s: %s (%s)", msisdn, image_url, caption)
        return {"success": True, "mock": True, "message_id": ""}
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
        data = r.json() if r.content else {}
        return {
            "success": r.ok,
            "message_id": (data.get("messages") or [{}])[0].get("id", ""),
            "raw": data,
        }
    except requests.RequestException as exc:
        log.warning("wa image send failed -> %s: %s", msisdn, exc)
        return {"success": False, "message": str(exc)}


def send_template(msisdn: str, template_name: str, params: list | None = None, lang: str = "en_US") -> dict:
    """Send a pre-approved template message (used for broadcasts outside the
    24-hr window). MOCK mode logs and returns success."""
    if not wa_live():
        log.info("[wa-mock] template %s -> %s %s", template_name, msisdn, params or [])
        return {"success": True, "mock": True, "message_id": f"mockt-{msisdn}-{template_name}"}
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
        data = r.json() if r.content else {}
        return {
            "success": r.ok,
            "message_id": (data.get("messages") or [{}])[0].get("id", ""),
            "error_code": (data.get("error") or {}).get("code"),
            "raw": data,
        }
    except requests.RequestException as exc:
        return {"success": False, "message": str(exc)}
