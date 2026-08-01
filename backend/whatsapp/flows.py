"""WhatsApp Flows business logic — the secure PIN pad handler.

`flows_crypto` handles the envelope; this module handles the (already decrypted)
data-exchange request: it maps a signed `flow_token` back to the pending money
action, verifies the PIN server-side (same brute-force lockout the chat/app use),
and — only on a correct PIN — executes the transaction and returns a terminal
success screen. The PIN is validated here, never echoed anywhere.

Screen ids mirror the published Flow JSON (`flow_assets/pin_flow.json`):
  PIN_SCREEN — masked PIN input; its submit does the data_exchange.
  SUCCESS    — terminal screen showing the outcome.
"""
import base64
import hashlib
import hmac
import logging

from django.conf import settings

log = logging.getLogger("whatsapp")

PIN_SCREEN = "PIN_SCREEN"
SUCCESS_SCREEN = "SUCCESS"
FLOW_PIN_STATE = "flow_pin"   # PendingAction.state while a secure Flow is armed


# --------------------------------------------------------------------------- #
# flow_token: a signed handle to the pending action
# --------------------------------------------------------------------------- #
def _sig(payload: str) -> str:
    mac = hmac.new(settings.SECRET_KEY.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")[:22]


def sign_flow_token(pa) -> str:
    """Opaque, tamper-evident handle for a pending action: ``<id>.<sig>`` where
    the signature binds the id to the number, so a token can't be edited to point
    at another user's action even if the encrypted channel were somehow bypassed."""
    return f"{pa.id}.{_sig(f'{pa.id}:{pa.msisdn}')}"


def resolve_flow_token(token: str):
    """Return the live PendingAction for a signed flow_token, or None if the token
    is malformed, forged, expired, or no longer in the Flow-PIN state."""
    from .models import PendingAction

    raw = (token or "").strip()
    if "." not in raw:
        return None
    pid, _, sig = raw.partition(".")
    if not pid.isdigit():
        return None
    pa = PendingAction.objects.filter(id=int(pid)).first()
    if pa is None or pa.state != FLOW_PIN_STATE or pa.expired:
        return None
    if not hmac.compare_digest(sig, _sig(f"{pa.id}:{pa.msisdn}")):
        return None
    return pa


# --------------------------------------------------------------------------- #
# screen builders
# --------------------------------------------------------------------------- #
def _pin_screen(summary: str, error: str = "") -> dict:
    return {"screen": PIN_SCREEN, "data": {"summary": summary or "Confirm your payment", "error": error or ""}}


def _success_screen(message: str) -> dict:
    return {"screen": SUCCESS_SCREEN, "data": {"message": message or "Done ✅"}}


# --------------------------------------------------------------------------- #
# request handler (decrypted payload -> response dict)
# --------------------------------------------------------------------------- #
def handle_flow_request(payload: dict) -> dict:
    """Route a decrypted Flows request to its response. Never raises — any
    unexpected shape resolves to a safe terminal screen so the endpoint always
    returns a well-formed (encryptable) reply."""
    if not isinstance(payload, dict):
        return _success_screen("Invalid request. Please start again in the chat.")
    action = payload.get("action", "")

    # Health check Meta fires against the endpoint.
    if action == "ping":
        return {"data": {"status": "active"}}

    data = payload.get("data", {}) or {}
    if not isinstance(data, dict):
        data = {}
    # Client-side error report (Meta convention) — just acknowledge.
    if data.get("error_message"):
        return {"data": {"acknowledged": True}}

    token = payload.get("flow_token", "")

    if action == "INIT":
        pa = resolve_flow_token(token)
        if pa is None:
            return _success_screen("This request has expired. Please start again in the chat.")
        return _pin_screen(pa.payload.get("flow_summary", ""))

    if action == "data_exchange":
        return _submit_pin(token, data)

    # BACK / unknown actions: re-render the PIN screen if we can, else a terminal.
    pa = resolve_flow_token(token)
    return _pin_screen(pa.payload.get("flow_summary", "")) if pa else _success_screen("Session ended.")


def _submit_pin(token: str, data: dict) -> dict:
    from common.http import evaluate_transaction_pin

    from .router import _clear_actions, run_flow_execution

    pa = resolve_flow_token(token)
    if pa is None:
        return _success_screen("This request expired or was already completed. Start again in the chat.")

    user = pa.user
    pin = str(data.get("pin", "")).strip()
    summary = pa.payload.get("flow_summary", "")

    ok, code, message = evaluate_transaction_pin(user, pin)
    if not ok:
        # Lockout is enforced inside evaluate_transaction_pin (row-locked, shared
        # with the app/chat), so the PIN can't be brute-forced through the Flow.
        if code == "pin_locked":
            _clear_actions(pa.msisdn)
            return _success_screen(message)
        return _pin_screen(summary, error=message)

    try:
        outcome = run_flow_execution(pa, user)
    except Exception:  # never leak a stack to the Flow; the money paths are idempotent
        log.exception("flow execution failed for pa=%s", pa.id)
        _clear_actions(pa.msisdn)
        return _success_screen("Something went wrong completing that. If you were charged it will auto-reverse.")
    return _success_screen(outcome)
