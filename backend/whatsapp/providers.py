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
        return True
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
