"""WhatsApp webhook + account-linking endpoints.

The webhook is public (Meta calls it): GET does the verify handshake, POST takes
inbound messages — HMAC-verified, deduped on Meta's message id, acked 200 fast,
and processed inline by the deterministic router. Linking endpoints are the
app-side of the OTP-style link (a signed-in user gets a code to send from
WhatsApp).
"""
import functools
import json
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction as db_transaction
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from common.http import api, fail, ok, require_user

from .models import Broadcast, BroadcastRecipient, ConversationState, WaMessageLog, WhatsAppLink
from .ops import record_audit, send_broadcast
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
    for status in _iter_statuses(event):
        _apply_status(status)
    return JsonResponse({"status": True})


def _iter_messages(event: dict):
    for entry in event.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            for msg in (change.get("value", {}) or {}).get("messages", []) or []:
                yield msg


def _iter_statuses(event: dict):
    for entry in event.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            for st in (change.get("value", {}) or {}).get("statuses", []) or []:
                yield st


def _apply_status(st: dict) -> None:
    """Delivery callback -> update the broadcast recipient + roll up counts."""
    mid, status = st.get("id", ""), st.get("status", "")
    if not mid or status not in ("delivered", "read", "failed"):
        return
    rec = BroadcastRecipient.objects.filter(wa_message_id=mid).first()
    if rec is None:
        return
    rec.status = status
    rec.error = (st.get("errors") or [{}])[0].get("code", "") if status == "failed" else rec.error
    rec.save(update_fields=["status", "error"])
    b = rec.broadcast
    b.count_delivered = b.recipients.filter(status="delivered").count()
    b.count_read = b.recipients.filter(status="read").count()
    b.save(update_fields=["count_delivered", "count_read"])


# A bare 4-6 digit message is almost certainly a transaction PIN — redact it
# from the log regardless of flow state (an out-of-band or mistimed PIN would
# otherwise be persisted in clear and shown in the agent monitor).
_PIN_RE = re.compile(r"^\s*\d{4,6}\s*$")


def _inbound_throttled(msisdn: str) -> bool:
    """Per-sender inbound throttle. Meta's source IP is shared, so the per-IP
    limiter can't help here — key on the sender number to bound link-code brute
    force and command/PIN flooding (30 msgs / minute / number). Honours
    RATELIMIT_ENABLE (off under tests) like the rest of the rate limiting."""
    if not getattr(settings, "RATELIMIT_ENABLE", True):
        return False
    from django.core.cache import cache

    key = f"wa:in:{msisdn}"
    cache.add(key, 0, 60)
    try:
        return cache.incr(key) > 30
    except ValueError:
        cache.set(key, 1, 60)
        return False


def _process(msg: dict) -> None:
    mid = msg.get("id", "")
    frm = msg.get("from", "")
    # Real Meta messages always carry a stable id; without one we cannot dedupe,
    # so a forged/replayed payload (empty id slips past the partial-unique index)
    # would be processed repeatedly. Drop anything missing from/id.
    if not frm or not mid:
        return
    if _inbound_throttled(frm):
        return
    is_text = msg.get("type") == "text"
    body = (msg.get("text") or {}).get("body", "") if is_text else ""
    # Mask a PIN before it ever touches the log/monitor — by flow state AND by
    # shape, so a PIN typed out-of-band is never stored in clear.
    looks_like_pin = bool(is_text and _PIN_RE.match(body or ""))
    logged = "[PIN]" if (is_awaiting_pin(frm) or looks_like_pin) else (body or f"[{msg.get('type', 'non-text')}]")

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


# --------------------------------------------------------------------------- #
# operator endpoints (staff only) — handover, agent reply, broadcast (§9-§11)
# --------------------------------------------------------------------------- #
# Role-gated like the rest of the operator surface: conversation actions need
# the `wa` capability, broadcasts the `broadcast` capability (portal.roles is
# the single role matrix both portals enforce server-side). A bare `is_staff`
# account without a role group resolves to read_only and is rejected here —
# previously any staff user could reply to chats or send broadcasts.
from portal.roles import require_cap


@api
@require_cap("wa")
def ops_handover(request):
    """POST /api/whatsapp/ops/handover/ {msisdn} — pause the bot, assign to agent."""
    msisdn = (request.data.get("msisdn") or "").strip()
    if not msisdn:
        return fail("msisdn required")
    convo = ConversationState.for_msisdn(msisdn)
    before = {"status": convo.status, "ai_enabled": convo.ai_enabled}
    convo.status = ConversationState.HUMAN
    convo.ai_enabled = False
    convo.assigned_agent = request.user_obj
    convo.save()
    record_audit("conversation.handover", actor=request.user_obj, target=f"wa:{msisdn}",
                 before=before, after={"status": convo.status, "ai_enabled": False})
    return ok(success=True, status=convo.status)


@api
@require_cap("wa")
def ops_return_to_bot(request):
    """POST /api/whatsapp/ops/return-to-bot/ {msisdn} — re-enable the bot + AI."""
    msisdn = (request.data.get("msisdn") or "").strip()
    if not msisdn:
        return fail("msisdn required")
    convo = ConversationState.for_msisdn(msisdn)
    convo.status = ConversationState.BOT
    convo.ai_enabled = True
    convo.assigned_agent = None
    convo.save()
    record_audit("conversation.return_to_bot", actor=request.user_obj, target=f"wa:{msisdn}")
    return ok(success=True, status=convo.status)


@api
@require_cap("wa")
def ops_reply(request):
    """POST /api/whatsapp/ops/reply/ {msisdn, text} — agent message to the user."""
    msisdn = (request.data.get("msisdn") or "").strip()
    text = (request.data.get("text") or "").strip()
    if not msisdn or not text:
        return fail("msisdn and text required")
    reply(msisdn, text)
    record_audit("conversation.agent_reply", actor=request.user_obj, target=f"wa:{msisdn}")
    return ok(success=True)


@api
@require_cap("broadcast")
def ops_broadcast(request):
    """POST /api/whatsapp/ops/broadcast/ {template_name, category?, segment?, body_params?}
    -> creates + sends a broadcast, returns the delivery counts."""
    d = request.data
    if not d.get("template_name"):
        return fail("template_name required")
    b = Broadcast.objects.create(
        template_name=d["template_name"], category=d.get("category", Broadcast.UTILITY),
        body_params=d.get("body_params", []), segment=d.get("segment", {}),
        created_by=request.user_obj,
    )
    send_broadcast(b, actor=request.user_obj)
    return ok(success=True, broadcast_id=b.id, queued=b.count_queued,
              sent=b.count_sent, failed=b.count_failed)
