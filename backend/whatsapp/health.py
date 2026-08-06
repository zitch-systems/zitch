"""Why the WhatsApp bot is silent.

A user typing "hello" and getting nothing back has one visible symptom and about
six possible causes, and they sit in different places: Meta's config, our channel
mode, the durable inbound queue, the worker service that drains it, and the
per-conversation handover flag that deliberately mutes the bot. None of those is
apparent from the chat, and until now none of them was apparent from anywhere an
operator without a shell could look.

This is one read-only probe that separates them, and the order of the verdict's
checks is the point.

The FIRST question is whether Meta ever reached us, because that case is invisible
in every other number: a call that never arrived produces no queue row, no log
line, nothing — a webhook subscribed to the wrong field, or an APP_SECRET that
does not match, reads exactly like an idle, healthy channel. Only `WebhookEvent`
distinguishes "nobody has messaged us" from "we are not being called".

Below the webhook, the queue is the next question. Inbound messages are persisted
and drained by the web service after each callback, with `zitch-whatsapp-worker`
as the primary consumer. An old backlog therefore means the messages are failing,
not merely unattended.
"""
from datetime import timedelta

from django.utils import timezone

from .models import ConversationState, WaMessageLog, WebhookEvent

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

    # Did Meta call us at all? Nothing below the webhook can explain silence when
    # the webhook was never reached, and that case is invisible in every other
    # number here — no call means no queue row, which reads identically to "idle
    # and healthy". Signature rejections are counted separately because a wrong
    # APP_SECRET presents as total silence with a perfectly configured callback.
    calls = WebhookEvent.objects.filter(source="whatsapp")
    last_call = calls.order_by("-created").values_list("created", flat=True).first()
    rejected = calls.filter(outcome=WebhookEvent.REJECTED_SIGNATURE).count()
    accepted_ever = calls.filter(verified=True).exists()

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
        "webhook": {
            "last_call_age_seconds": _age_seconds(last_call),
            "ever_accepted_a_call": accepted_ever,
            "rejected_signature": rejected,
        },
        "handed_to_human": muted,
        "verdict": _verdict(wa_mode(), backlog, stalled, dead, muted,
                            accepted_ever=accepted_ever, rejected=rejected),
    }


def _verdict(mode: str, backlog: int, stalled: bool, dead: int, muted: list,
             *, accepted_ever: bool = True, rejected: int = 0) -> str:
    """One sentence naming the most likely reason the bot is not replying — the
    thing an operator reads first, before any of the numbers above."""
    if mode == "disabled":
        return ("WhatsApp is switched off (no token / phone number id), so the webhook "
                "returns 404 and nothing is processed.")
    if mode == "sandbox":
        return ("Channel is in SANDBOX: inbound is routed but nothing is actually sent to "
                "Meta, so the user sees no reply.")
    # Ordered before the queue checks: a call that never arrived cannot have
    # produced a queue row, so every queue number below would read "healthy".
    if rejected and not accepted_ever:
        return (f"Meta is calling the webhook but every call is REJECTED ({rejected} so far) "
                "because the signature does not verify. WHATSAPP_APP_SECRET does not match "
                "the app secret in the Meta dashboard.")
    if not accepted_ever:
        return ("Meta has never successfully called this webhook. Nothing below the webhook "
                "can explain the silence: check that the callback URL "
                "(https://api.zitch.ng/webhooks/whatsapp) is saved and SUBSCRIBED to the "
                "`messages` field in the Meta dashboard, and that WHATSAPP_VERIFY_TOKEN "
                "matches.")
    if stalled:
        return (f"{backlog} inbound message(s) are queued and not being drained. The web "
                "service drains the queue itself after each webhook, so a backlog this old "
                "means the messages are FAILING rather than merely unattended — open them "
                "and read `processing_error`. (If it is empty, no webhook has arrived since "
                "they queued: check that zitch-whatsapp-worker is running, or use “Process "
                "now” on the rows to drain them by hand.)")
    if dead:
        return (f"{dead} inbound message(s) were dead-lettered after repeated failures. "
                "Open them and read `processing_error`.")
    if muted:
        return ("Some conversations are handed to a human agent, which mutes the bot for "
                "them by design. Use “Return to bot” on those rows to re-enable it.")
    if backlog:
        return f"{backlog} message(s) in flight — normal, the worker is draining them."
    return "Nothing is queued or muted: the channel is processing inbound messages."
