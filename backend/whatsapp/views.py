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
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from common.http import api, evaluate_transaction_pin, fail, ok, require_user
from common.ratelimit import ratelimit

from .flows import resolve_approve_token
from .models import Broadcast, BroadcastRecipient, ConversationState, WhatsAppLink
from .ops import record_audit, validate_broadcast_spec
from .providers import verify_signature, wa_enabled, wa_live
from .router import is_awaiting_bvn, is_awaiting_pin, reply

LINK_CODE_TTL = timedelta(minutes=30)
WHATSAPP_WEBHOOK_BODY_MAX = 1024 * 1024


@csrf_exempt
def webhook(request):
    """GET /webhooks/whatsapp  — verify handshake.
    POST /webhooks/whatsapp — inbound messages + status callbacks.
    """
    if not wa_enabled():
        return HttpResponse(status=404)
    if request.method == "GET":
        p = request.GET
        if p.get("hub.mode") == "subscribe" and p.get("hub.verify_token") == settings.WHATSAPP.get("VERIFY_TOKEN"):
            return HttpResponse(p.get("hub.challenge", ""))
        return HttpResponse("forbidden", status=403)

    if request.method != "POST":
        return HttpResponse(status=405)

    from common.ratelimit import client_ip

    from .models import WebhookEvent
    from .ops import record_webhook

    try:
        declared_size = int(request.META.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError):
        declared_size = 0
    if declared_size > WHATSAPP_WEBHOOK_BODY_MAX:
        return JsonResponse({"success": False, "message": "Payload too large"}, status=413)
    raw_body = request.body or b"{}"
    if len(raw_body) > WHATSAPP_WEBHOOK_BODY_MAX:
        return JsonResponse({"success": False, "message": "Payload too large"}, status=413)

    if not verify_signature(raw_body, request.headers.get("X-Hub-Signature-256", "")):
        # Body deliberately not recorded: an unauthenticated caller must not be able
        # to write chosen content into a table operators read.
        record_webhook("whatsapp", outcome=WebhookEvent.REJECTED_SIGNATURE,
                       http_status=401, remote_ip=client_ip(request))
        return JsonResponse({"success": False, "message": "Invalid signature"}, status=401)
    try:
        event = json.loads(raw_body)
    except (ValueError, TypeError):
        record_webhook("whatsapp", outcome=WebhookEvent.BAD_BODY, verified=True,
                       http_status=400, remote_ip=client_ip(request))
        return JsonResponse({"success": False, "message": "Invalid payload"}, status=400)
    if not isinstance(event, dict) or not _live_metadata_valid(event):
        record_webhook("whatsapp", outcome=WebhookEvent.BAD_BODY, verified=True,
                       http_status=400, remote_ip=client_ip(request),
                       action="invalid_phone_number_id")
        return JsonResponse({"success": False, "message": "Invalid payload"}, status=400)

    # Persist commands before acknowledging. Production workers process the
    # encrypted queue; local/test mode can run that same job inline for a tight
    # development loop.
    messages = list(_iter_messages(event))
    statuses = list(_iter_statuses(event))
    # A DESCRIPTOR, not the envelope. Unlike a bank callback, a WhatsApp body carries
    # the customer's message text — and the router accepts a PIN reply in chat, so the
    # raw body can contain a transaction PIN before we refuse it. Persisting that to a
    # table operators read would create the exact exposure the production
    # raw-PIN-in-chat block exists to prevent. Ids and types answer "did Meta deliver,
    # and what"; the text is not needed and is not kept. `from` is omitted for the same
    # reason — it is the customer's phone number.
    record_webhook("whatsapp", verified=True, remote_ip=client_ip(request),
                   payload={"messages": [{"id": m.get("id", ""), "type": m.get("type", "")}
                                         for m in messages],
                            "statuses": [{"id": s.get("id", ""), "status": s.get("status", "")}
                                         for s in statuses]},
                   action=f"messages:{len(messages)} statuses:{len(statuses)}")
    for message in messages:
        _process(message)
    for status in statuses:
        _apply_status(status)
    if messages and not getattr(settings, "WHATSAPP_PROCESS_INLINE", False):
        # Safety net for a worker that isn't running. Off the request thread, so
        # the acknowledgement below is not delayed by it, and a no-op whenever the
        # worker is healthy (it will already hold the row's lease). See
        # jobs.drain_in_background. Skipped in inline mode: the message was just
        # handled in this request, and inline exists for hosts where a daemon
        # thread is reaped before it runs — spawning one there is pure noise.
        from .jobs import drain_in_background

        drain_in_background()
    return JsonResponse({"status": True})


@csrf_exempt
def flow_endpoint(request):
    """POST /webhooks/whatsapp/flow — the WhatsApp Flows data-exchange endpoint.

    Meta posts an encrypted body when the user submits the secure PIN screen (or
    for the periodic health-check ping). We decrypt with our private key, run the
    business logic (PIN verify + execute), and return the reply encrypted with the
    same AES key and the inverted IV. On a decryption failure we return 421, Meta's
    signal to refetch our public key.
    """
    if not wa_enabled():
        return HttpResponse(status=404)
    if request.method != "POST":
        return HttpResponse(status=405)

    # Meta signs Flows data-exchange requests exactly like webhook callbacks
    # (X-Hub-Signature-256 over the raw body, keyed on the app secret) — verify
    # them the same way. The envelope encryption alone doesn't authenticate the
    # sender (anyone holding our PUBLIC key can produce a decryptable body), so
    # without this check the endpoint accepts forged PIN submissions from any
    # origin. Mock mode (channel not live) accepts unsigned, as on the webhook.
    if not verify_signature(request.body, request.headers.get("X-Hub-Signature-256", "")):
        return JsonResponse({"success": False, "message": "Invalid signature"}, status=401)

    from .flows import handle_flow_request
    from .flows_crypto import FlowDecryptError, decrypt_request, encrypt_response

    try:
        body = json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        return HttpResponse(status=400)
    if not isinstance(body, dict):
        return HttpResponse(status=400)

    try:
        payload, aes_key, iv = decrypt_request(body)
    except FlowDecryptError:
        # 421 => Meta refetches the endpoint's public key and retries.
        return HttpResponse(status=421)

    response = handle_flow_request(payload)
    encrypted = encrypt_response(response, aes_key, iv)
    # Meta expects the raw base64 ciphertext as the body (not JSON-wrapped).
    return HttpResponse(encrypted, content_type="text/plain")


def _iter_messages(event: dict):
    for entry in event.get("entry", []) or []:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) if isinstance(change, dict) else {}
            if not isinstance(value, dict):
                continue
            for msg in value.get("messages", []) or []:
                if isinstance(msg, dict):
                    yield msg


def _live_metadata_valid(event: dict) -> bool:
    """In live mode, accept events only for our configured Meta phone-number id."""
    if not wa_live():
        return True
    expected = str(settings.WHATSAPP.get("PHONE_NUMBER_ID") or "")
    seen = False
    for entry in event.get("entry", []) or []:
        if not isinstance(entry, dict):
            return False
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) if isinstance(change, dict) else {}
            metadata = value.get("metadata", {}) if isinstance(value, dict) else {}
            seen = True
            if str(metadata.get("phone_number_id") or "") != expected:
                return False
    return seen


def _iter_statuses(event: dict):
    for entry in event.get("entry", []) or []:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) if isinstance(change, dict) else {}
            if not isinstance(value, dict):
                continue
            for st in value.get("statuses", []) or []:
                if isinstance(st, dict):
                    yield st


def _apply_status(st: dict) -> None:
    """Delivery callback -> update the broadcast recipient + roll up counts."""
    mid, status = st.get("id", ""), st.get("status", "")
    if not mid or status not in ("sent", "delivered", "read", "failed"):
        return
    rec = BroadcastRecipient.objects.filter(wa_message_id=mid).first()
    if rec is None:
        return
    rank = {"queued": 0, "unknown": 0, "sent": 1, "delivered": 2, "read": 3}
    current_rank = rank.get(rec.status, 0)
    if status == "failed":
        # A late/out-of-order failure cannot undo confirmed delivery/read.
        if current_rank >= rank["delivered"]:
            return
    elif rank.get(status, 0) <= current_rank:
        return
    rec.status = status
    if status == "failed":
        errors = st.get("errors") or []
        first_error = errors[0] if errors and isinstance(errors[0], dict) else {}
        rec.error = str(first_error.get("code") or "delivery_failed")[:200]
    rec.save(update_fields=["status", "error"])
    from .jobs import refresh_broadcast_counts
    refresh_broadcast_counts(rec.broadcast_id)


# A bare 4-6 digit message is almost certainly a transaction PIN — redact it
# from the log regardless of flow state (an out-of-band or mistimed PIN would
# otherwise be persisted in clear and shown in the agent monitor).
_PIN_RE = re.compile(r"^\s*\d{4,6}\s*$")
# Same separator tolerance as the model sanitizer — see whatsapp/ai.py.
_LOG_IDENTIFIER_RE = re.compile(r"(?<![\d])\d(?:[ -]?\d){6,}(?![\d])")
_LOG_EMAIL_RE = re.compile(r"(?i)\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b")
_LOG_SECRET_CONTEXT_RE = re.compile(r"(?i)\b(pin|otp|password|passcode|passwd|cvv|cvc|secret)\b")
_LOG_SHORT_CODE_RE = re.compile(r"(?<!\d)\d{3,6}(?!\d)")


def _redact_chat_log(text: str) -> str:
    """Minimise long-lived support-log PII while preserving conversation shape.

    Card numbers are collapsed first, however they are spaced — "4242 4242 4242
    4242" is four short groups, so neither the 7+-digit rule nor the bare-PIN
    mask would touch it, and a card in a support log is a card in every export
    of that log. Then, in a message that talks about a pin/otp/cvv, the short
    digit groups beside those words are masked too: "my pin is 1234" was stored
    verbatim before, and the whole-message [PIN] mask only covers bare digits."""
    from .ai import _CARD_SHAPE, _luhn_ok

    safe = str(text or "")

    def _card(m):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            return f"[card …{digits[-4:]}]"
        return m.group(0)

    safe = _CARD_SHAPE.sub(_card, safe)
    safe = _LOG_EMAIL_RE.sub("[email redacted]", safe)
    def _ident(m):
        digits = re.sub(r"\D", "", m.group(0))
        return f"[identifier …{digits[-4:]}]"

    safe = _LOG_IDENTIFIER_RE.sub(_ident, safe)
    if _LOG_SECRET_CONTEXT_RE.search(safe):
        safe = _LOG_SHORT_CODE_RE.sub("[code redacted]", safe)
    return safe


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
    is_text = msg.get("type") == "text"
    body = (msg.get("text") or {}).get("body", "") if is_text else ""
    is_flow_reply = False
    # Interactive replies (list rows / reply buttons) deliver an id we chose when
    # sending — the ids ARE the text the router understands ("1", "airtime", a
    # bank code…), so a tap is handled exactly like the user typing it.
    if msg.get("type") == "interactive":
        inter = msg.get("interactive") or {}
        if inter.get("type") == "nfm_reply" or inter.get("nfm_reply"):
            # A completed secure PIN Flow. The money already moved (and the receipt
            # was already sent) via the encrypted data-exchange endpoint — this is
            # just the "flow finished" callback, so record it and stop; never run
            # the text router or echo the flow payload.
            is_flow_reply = True
        else:
            picked = (inter.get("list_reply") or inter.get("button_reply") or {})
            if picked.get("id"):
                is_text, body = True, str(picked["id"])
    # Mask a PIN before it ever touches the log/monitor — by flow state AND by
    # shape, so a PIN typed out-of-band is never stored in clear.
    looks_like_pin = bool(is_text and _PIN_RE.match(body or ""))
    if is_flow_reply:
        logged = "[flow completed]"
    elif is_awaiting_pin(frm) or looks_like_pin:
        logged = "[PIN]"
    elif is_awaiting_bvn(frm):
        logged = "[BVN]"  # keep the BVN out of the message log, like the PIN
    else:
        logged = _redact_chat_log(body) if body else f"[{msg.get('type', 'non-text')}]"

    from .jobs import discard_inbound, enqueue_inbound, process_inbound_message

    if _inbound_throttled(frm):
        discard_inbound(
            message_id=mid, msisdn=frm, logged_text=logged, reason="throttled",
        )
        return
    try:
        row, _created = enqueue_inbound(
            message_id=mid, msisdn=frm, logged_text=logged,
            payload={"is_text": is_text, "body": body, "flow_reply": is_flow_reply},
        )
    except Exception as exc:  # noqa: BLE001 — re-raised below; this is for visibility
        # A message that cannot even be QUEUED leaves no row anywhere, so the queue
        # looks empty and every diagnostic reports a healthy, idle channel while the
        # customer gets nothing. Record the failure before re-raising: the 500 is
        # correct (Meta retries; swallowing would lose the message outright), but it
        # must not be the only trace. The message text is deliberately not recorded.
        #
        # `accepted` with a 500 is the accurate pair: the callback itself was valid
        # (signed, well-formed, for our number) — we are the ones who failed it.
        from .ops import record_webhook

        record_webhook("whatsapp", verified=True, http_status=500,
                       action=f"enqueue_failed:{type(exc).__name__}")
        raise
    if getattr(settings, "WHATSAPP_PROCESS_INLINE", False):
        # raise_errors only outside production: in dev/tests a router bug should
        # blow up the request loudly. In production inline mode the job function
        # must swallow and RECORD the failure (attempts, processing_error, retry
        # lease) instead — a raise here would 500 the webhook, Meta would redeliver
        # for a row that is already queued and counted, and enough 500s get the
        # callback throttled. The row is durable either way; nothing is lost.
        process_inbound_message(
            row.pk,
            raise_errors=bool(getattr(settings, "DEBUG", False)
                              or getattr(settings, "TESTING", False)),
        )


# --------------------------------------------------------------------------- #
# linking (app side)
# --------------------------------------------------------------------------- #
@api
@ratelimit("whatsapp_link_start", limit=5, window=300)
@require_user
def link_start(request):
    """POST /api/whatsapp/link/start/ {access_token, transaction_pin}
    -> {success, code, wa_link, expires_in} — a code to send from WhatsApp.

    The PIN is required before a code is issued. A link grants a channel that can
    move money, so it is a privileged action, not a display: without this, anyone
    holding an unlocked phone (or a live session on a lost one) could bind their
    own WhatsApp to the account. The PIN check shares the app's brute-force
    lockout, so it cannot be ground down here either.
    """
    if not wa_enabled():
        return fail("WhatsApp banking is currently unavailable", status=503)
    user = request.user_obj
    pin_ok, pin_code, pin_message = evaluate_transaction_pin(
        user, (request.data.get("transaction_pin") or "").strip())
    if not pin_ok:
        return fail(pin_message, status=429 if pin_code == "pin_locked" else 400)
    WhatsAppLink.objects.filter(user=user, status=WhatsAppLink.PENDING).delete()
    code = secrets.token_hex(16).upper()  # 128-bit, normally opened via prefilled wa.me link
    WhatsAppLink.objects.create(
        user=user, status=WhatsAppLink.PENDING, link_code=code,
        expires_at=timezone.now() + LINK_CODE_TTL,
    )
    biz = settings.WHATSAPP.get("BUSINESS_NUMBER", "")
    wa_link = f"https://wa.me/{biz}?text=LINK%20{code}" if biz else ""
    return ok(success=True, code=code, wa_link=wa_link, expires_in=int(LINK_CODE_TTL.total_seconds()))


@api
@ratelimit("whatsapp_link_status", limit=30, window=300)
@require_user
def link_status(request):
    """POST /api/whatsapp/link/status/ {access_token} -> {success, linked, masked_number?}"""
    if not wa_enabled():
        return fail("WhatsApp banking is currently unavailable", status=503)
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
    result = reply(msisdn, text)
    if not result.get("success"):
        return fail(result.get("message", "WhatsApp delivery failed"), status=502)
    record_audit("conversation.agent_reply", actor=request.user_obj, target=f"wa:{msisdn}")
    return ok(success=True, message_id=result.get("message_id", ""))


@api
@require_cap("broadcast")
def ops_broadcast(request):
    """POST /api/whatsapp/ops/broadcast/ {template_name, category?, segment?, body_params?}
    -> holds the campaign for a different operator to approve."""
    from common import approvals

    if not wa_enabled():
        return fail("WhatsApp banking is currently unavailable", status=503)
    try:
        spec = validate_broadcast_spec(request.data)
        approval = approvals.submit(
            "whatsapp.broadcast", payload=spec, requested_by=request.user_obj,
            reason="WhatsApp template campaign",
        )
    except (ValueError, approvals.ApprovalError) as exc:
        return fail(str(exc))
    response = ok(
        success=True, pending_approval=True, approval_id=approval.pk,
        message="A second broadcast operator must approve this campaign.",
    )
    response.status_code = 202
    return response


# --------------------------------------------------------------------------- #
# Deep-link approval: authorise a pending WhatsApp action in the app
# --------------------------------------------------------------------------- #
def _resolve_approvable(request):
    """(pa, error_response). Shared by preview and execute so the two cannot
    drift on what a valid hand-off is.

    The cross-user check is the load-bearing line: the token rode through a chat
    message and an OS deep link, so possession proves nothing. Only a session
    belonging to the action's owner may see or approve it. The mismatch response
    is indistinguishable from "expired" — a wrong-user probe learns nothing
    about whether the token was real.
    """
    pa = resolve_approve_token((request.data.get("token") or "").strip())
    if pa is None or pa.user_id != request.user_obj.id:
        return None, fail("This request has expired or was already completed.", status=410)
    return pa, None


@api
@ratelimit("whatsapp_approve", limit=30, window=300)
@require_user
def approve_preview(request):
    """POST /api/whatsapp/approve/preview/ {access_token, token}
    -> {success, summary, action_type} — what the customer is about to authorise.

    The summary is the SAME text the Flow PIN screen shows, so what the person
    approves in the app is verbatim what the chat armed — no re-derivation that
    could disagree with it.
    """
    if not wa_enabled():
        return fail("WhatsApp banking is currently unavailable", status=503)
    pa, err = _resolve_approvable(request)
    if err:
        return err
    from .router import _flow_summary

    return ok(success=True, action_type=pa.action_type,
              summary=pa.payload.get("flow_summary") or _flow_summary(pa))


@api
@ratelimit("whatsapp_approve", limit=30, window=300)
@require_user
def approve_execute(request):
    """POST /api/whatsapp/approve/execute/ {access_token, token, transaction_pin}
    -> {success, message} — verifies the PIN and runs the armed action.

    The biometric happens on the DEVICE: a successful scan releases the cached
    transaction PIN from the OS keychain, exactly as in-app payments work. The
    server never trusts "a biometric happened" — it verifies the same PIN, with
    the same row-locked lockout, that every other surface verifies. This
    endpoint is therefore no weaker than the Flow it stands beside.

    Execution goes through run_flow_execution, so the freeze re-check and the
    idempotent executors are shared with the Flow path, and completing the
    action clears it — a hand-off token cannot be redeemed twice.
    """
    if not wa_enabled():
        return fail("WhatsApp banking is currently unavailable", status=503)
    pa, err = _resolve_approvable(request)
    if err:
        return err
    user = request.user_obj
    pin_ok, pin_code, pin_message = evaluate_transaction_pin(
        user, (request.data.get("transaction_pin") or "").strip())
    if not pin_ok:
        return fail(pin_message, status=429 if pin_code == "pin_locked" else 400)
    from .router import run_flow_execution

    try:
        outcome = run_flow_execution(pa, user)
    except Exception:  # noqa: BLE001 — money paths are idempotent; never leak a stack
        import logging

        logging.getLogger("whatsapp").exception("approve_execute failed for pa=%s", pa.id)
        return fail("Something went wrong completing that. If you were charged it will auto-reverse.",
                    status=502)
    return ok(success=True, message=outcome)


def approve_handoff(request, token: str):
    """GET /wa/approve/<token> — the https bounce into the app.

    WhatsApp renders only http(s) links as tappable, so the chat cannot carry
    `zitch://` directly; this page immediately forwards into the app scheme and
    falls back to the store links for someone without the app.

    Deliberately unauthenticated and deliberately blank about the action: it
    renders no amount, no recipient, no state — anyone opening the link off a
    forwarded chat sees only a bounce page. The token is opaque here; every
    check (signature, expiry, session-user binding, PIN) happens in the
    authenticated API the app calls next.
    """
    import html

    if request.method != "GET":
        return HttpResponse(status=405)
    # The token is path material we reflect into a URL and into markup — keep
    # only the alphabet our tokens are made of so this page cannot be used to
    # smuggle arbitrary content into an app link.
    safe = re.sub(r"[^A-Za-z0-9._-]", "", token or "")[:128]
    app_url = f"zitch://waapprove?token={safe}"
    links = getattr(settings, "ZITCH_LINKS", {}) or {}
    store = html.escape(links.get("APP") or "https://zitch.ng/app")
    body = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>Open Zitch</title>
<style>
  body{{margin:0;font-family:-apple-system,'Helvetica Neue',Roboto,sans-serif;
       background:#0b1416;color:#e7eef0;display:flex;min-height:100vh;
       align-items:center;justify-content:center;text-align:center}}
  .card{{padding:40px 28px;max-width:340px}}
  .mark{{font-size:34px;font-weight:800;color:#0FA295;letter-spacing:-.5px}}
  p{{color:#93a5aa;font-size:15px;line-height:1.55}}
  a.btn{{display:inline-block;margin-top:14px;background:#0FA295;color:#fff;
       text-decoration:none;font-weight:700;padding:14px 26px;border-radius:14px}}
  a.store{{display:block;margin-top:18px;color:#0FA295;font-size:14px}}
</style></head><body><div class="card">
<div class="mark">zitch</div>
<p>Opening the Zitch app to approve your payment…</p>
<a class="btn" href="{app_url}">Open the app</a>
<a class="store" href="{store}">Don't have the app? Get Zitch</a>
</div>
<script>window.location.href={json.dumps(app_url)};</script>
</body></html>"""
    return HttpResponse(body, content_type="text/html; charset=utf-8")
