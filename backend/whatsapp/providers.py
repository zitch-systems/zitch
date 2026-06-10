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


def verify_signature(raw_body: bytes, header: str) -> bool:
    """Validate Meta's X-Hub-Signature-256 (HMAC-SHA256 of the raw body).

    With no APP_SECRET configured (mock mode) we accept, matching how the Monnify
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
