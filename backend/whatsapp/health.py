"""Why the WhatsApp bot is silent.

A user typing "hello" and getting nothing back has one visible symptom and about
six possible causes, and they sit in different places: Meta's config, our channel
mode, the durable inbound queue, the worker service that drains it, and the
per-conversation handover flag that deliberately mutes the bot. None of those is
apparent from the chat, and until now none of them was apparent from anywhere an
operator without a shell could look.

This is one read-only probe that separates them. The important one is the queue:
inbound messages are persisted by the webhook and processed by a SEPARATE Render
service (`zitch-whatsapp-worker`). If that service is not running, the webhook
keeps returning 200 to Meta — everything looks healthy from outside — while every
message piles up unanswered. A backlog whose oldest entry is minutes old means the
worker is down, and nothing else does.
"""
from datetime import timedelta

from django.utils import timezone

from .models import ConversationState, WaMessageLog

# Under this, a backlog is just the worker's normal poll interval; over it, the
# queue is not being drained. The worker polls twice a second and leases for five
# minutes, so a minute of untouched backlog is already well outside normal.
STALL_AFTER = timedelta(minutes=1)


def _age_seconds(when) -> int | None:
    return None if when is None else max(0, int((timezone.now() - when).total_seconds()))


def whatsapp_diagnostics() -> dict:
    """A snapshot of the channel: config, queue, and anything deliberately muted."""
    from .providers import flows_live, wa_live, wa_mode

    inbound = WaMessageLog.objects.filter(direction=WaMessageLog.IN)
    unprocessed = inbound.filter(processed_at__isnull=True)
    oldest = unprocessed.order_by("created").values_list("created", flat=True).first()
    oldest_age = _age_seconds(oldest)

    last_in = inbound.order_by("-created").values_list("created", flat=True).first()
    last_out = (WaMessageLog.objects.filter(direction=WaMessageLog.OUT)
                .order_by("-created").values_list("created", flat=True).first())

    # Dead letters are messages the worker gave up on. They are the difference
    # between "nobody is processing" and "processing, and failing".
    dead = inbound.filter(processing_error__startswith="dead_letter").count()
    throttled = inbound.filter(processing_error="throttled").count()
    muted = list(ConversationState.objects
                 .filter(status=ConversationState.HUMAN)
                 .values_list("msisdn", flat=True)[:20])

    backlog = unprocessed.count()
    stalled = bool(backlog and oldest_age is not None
                   and oldest_age > STALL_AFTER.total_seconds())

    return {
        "mode": wa_mode(),
        "live": wa_live(),
        "secure_pin_flow_live": flows_live(),
        "queue": {
            "unprocessed": backlog,
            "oldest_unprocessed_age_seconds": oldest_age,
            "worker_appears_stalled": stalled,
            "dead_lettered": dead,
            "throttled": throttled,
        },
        "last_inbound_age_seconds": _age_seconds(last_in),
        "last_outbound_age_seconds": _age_seconds(last_out),
        "handed_to_human": muted,
        "verdict": _verdict(wa_mode(), backlog, stalled, dead, muted),
    }


def _verdict(mode: str, backlog: int, stalled: bool, dead: int, muted: list) -> str:
    """One sentence naming the most likely reason the bot is not replying — the
    thing an operator reads first, before any of the numbers above."""
    if mode == "disabled":
        return ("WhatsApp is switched off (no token / phone number id), so the webhook "
                "returns 404 and nothing is processed.")
    if mode == "sandbox":
        return ("Channel is in SANDBOX: inbound is routed but nothing is actually sent to "
                "Meta, so the user sees no reply.")
    if stalled:
        return (f"{backlog} inbound message(s) are queued and not being drained — the "
                "zitch-whatsapp-worker service is almost certainly not running. Start it "
                "in Render, or use “Process now” on the queued rows to drain by hand.")
    if dead:
        return (f"{dead} inbound message(s) were dead-lettered after repeated failures. "
                "Open them and read `processing_error`.")
    if muted:
        return ("Some conversations are handed to a human agent, which mutes the bot for "
                "them by design. Use “Return to bot” on those rows to re-enable it.")
    if backlog:
        return f"{backlog} message(s) in flight — normal, the worker is draining them."
    return "Nothing is queued or muted: the channel is processing inbound messages."
