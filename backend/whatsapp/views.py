"""WhatsApp webhook + account-linking endpoints.

The webhook is public (Meta calls it): GET does the verify handshake, POST takes
inbound messages — HMAC-verified, deduped on Meta's message id, acked 200 fast,
and processed inline by the deterministic router. Linking endpoints are the
app-side of the OTP-style link (a signed-in user gets a code to send from
WhatsApp).
"""
import json
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction as db_transaction
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from common.http import api, ok, require_user

from .models import WaMessageLog, WhatsAppLink
from .providers import verify_signature
from .router import handle_inbound, is_awaiting_pin, reply

LINK_CODE_TTL = timedelta(minutes=10)


@csrf_exempt
def webhook(request):
    """GET /webhooks/whatsapp  — verify handshake.
    POST /webhooks/whatsapp — inbound messages + status callbacks.
    """
    if request.method == "GET":
        p = request.GET
        if p.get("hub.mode") == "subscribe" and p.get("hub.verify_token") == settings.WHATSAPP.get("VERIFY_TOKEN"):
            return HttpResponse(p.get("hub.challenge", ""))
        return HttpResponse("forbidden", status=403)

    if request.method != "POST":
        return HttpResponse(status=405)

    if not verify_signature(request.body, request.headers.get("X-Hub-Signature-256", "")):
        return JsonResponse({"success": False, "message": "Invalid signature"}, status=401)
    try:
        event = json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        return JsonResponse({"success": False, "message": "Invalid payload"}, status=400)

    # Ack fast; process inline (no queue yet — handlers are quick).
    for message in _iter_messages(event):
        _process(message)
    return JsonResponse({"status": True})


def _iter_messages(event: dict):
    for entry in event.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            for msg in (change.get("value", {}) or {}).get("messages", []) or []:
                yield msg


def _process(msg: dict) -> None:
    mid = msg.get("id", "")
    frm = msg.get("from", "")
    if not frm:
        return
    is_text = msg.get("type") == "text"
    body = (msg.get("text") or {}).get("body", "") if is_text else ""
    # Mask a PIN before it ever touches the log/monitor.
    logged = "[PIN]" if is_awaiting_pin(frm) else (body or f"[{msg.get('type', 'non-text')}]")

    # Dedupe on Meta's message id: the unique row is the gate against a
    # re-delivered webhook (Meta retries until it gets a 200).
    try:
        with db_transaction.atomic():
            WaMessageLog.objects.create(
                msisdn=frm, direction=WaMessageLog.IN, wa_message_id=mid, text=logged,
            )
    except IntegrityError:
        return  # already processed this message

    if not is_text:
        return reply(frm, "I can only read text messages for now. Reply \"menu\" for options.")
    handle_inbound(frm, body)


# --------------------------------------------------------------------------- #
# linking (app side)
# --------------------------------------------------------------------------- #
@api
@require_user
def link_start(request):
    """POST /api/whatsapp/link/start/ {access_token}
    -> {success, code, wa_link, expires_in} — a code to send from WhatsApp.
    """
    user = request.user_obj
    WhatsAppLink.objects.filter(user=user, status=WhatsAppLink.PENDING).delete()
    code = secrets.token_hex(3).upper()  # 6 hex chars, easy to type
    WhatsAppLink.objects.create(
        user=user, status=WhatsAppLink.PENDING, link_code=code,
        expires_at=timezone.now() + LINK_CODE_TTL,
    )
    biz = settings.WHATSAPP.get("BUSINESS_NUMBER", "")
    wa_link = f"https://wa.me/{biz}?text=LINK%20{code}" if biz else ""
    return ok(success=True, code=code, wa_link=wa_link, expires_in=int(LINK_CODE_TTL.total_seconds()))


@api
@require_user
def link_status(request):
    """POST /api/whatsapp/link/status/ {access_token} -> {success, linked, masked_number?}"""
    link = request.user_obj.whatsapp_links.filter(status=WhatsAppLink.ACTIVE).first()
    if link is None:
        return ok(success=True, linked=False)
    n = link.wa_msisdn
    masked = ("•" * max(0, len(n) - 4) + n[-4:]) if n else ""
    return ok(success=True, linked=True, masked_number=masked, ai_enabled=link.ai_enabled)


@api
@require_user
def link_unlink(request):
    """POST /api/whatsapp/link/unlink/ {access_token} -> {success}"""
    request.user_obj.whatsapp_links.filter(status=WhatsAppLink.ACTIVE).delete()
    return ok(success=True, message="WhatsApp unlinked")
