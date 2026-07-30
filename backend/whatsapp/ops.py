"""Operator-side services: audit log + broadcast sending (§9, §10, §11).

The admin/agent surfaces ride on Django admin + a few staff-only endpoints;
these are the shared services behind them.
"""
import logging

from .models import AuditLog, Broadcast, BroadcastRecipient, WhatsAppLink
from .providers import send_template


def record_audit(action, actor=None, target="", before=None, after=None, actor_type="admin"):
    """Append an audit row. `actor` is a User (or None for system events)."""
    AuditLog.objects.create(
        actor_type=actor_type,
        actor_id=str(getattr(actor, "id", "") or ""),
        action=action, target=target, before=before or {}, after=after or {},
    )


def record_webhook(source, *, outcome=None, verified=False, http_status=200,
                   remote_ip="", reference="", action="", payload=None):
    """Append a WebhookEvent row. Never raises.

    Every call site is on a provider's retry path, so an exception here would turn a
    forensic-logging bug into a failed callback the bank re-delivers — or, on the
    authentication callback, into a payout that never authorises. The log going quiet
    is always preferable to that, so this swallows everything and says so in the
    application log.
    """
    from whatsapp.models import WebhookEvent

    try:
        WebhookEvent.objects.create(
            source=source, outcome=outcome or WebhookEvent.ACCEPTED, verified=verified,
            http_status=http_status, remote_ip=remote_ip or "",
            reference=str(reference or "")[:120], action=str(action or "")[:80],
            payload=WebhookEvent.redact(payload if isinstance(payload, (dict, list)) else {}),
        )
    except Exception:  # noqa: BLE001 — forensics must never break the callback
        logging.getLogger("zitch").exception("record_webhook_failed source=%s", source)


def _segment_links(broadcast: Broadcast):
    """Active links matching the segment. Marketing requires opt-in (hard-rule #8)."""
    qs = WhatsAppLink.objects.filter(status=WhatsAppLink.ACTIVE).select_related("user")
    seg = broadcast.segment or {}
    if broadcast.category == Broadcast.MARKETING or seg.get("marketing_opt_in"):
        qs = qs.filter(marketing_opt_in=True)
    if seg.get("tier") is not None:
        qs = qs.filter(user__tier=seg["tier"])
    return qs


def send_broadcast(broadcast: Broadcast, actor=None) -> Broadcast:
    """Send a template to every segment-matched recipient, tracking outcomes.

    A provider block (e.g. Meta's per-user marketing cap, code 131049) is
    recorded on the recipient and never blindly retried (hard-rule #8 / §9).
    """
    broadcast.status = Broadcast.SENDING
    broadcast.save(update_fields=["status"])

    links = list(_segment_links(broadcast))
    sent = failed = 0
    for link in links:
        res = send_template(link.wa_msisdn, broadcast.template_name, broadcast.body_params)
        ok = bool(res.get("success"))
        BroadcastRecipient.objects.create(
            broadcast=broadcast, user=link.user, wa_msisdn=link.wa_msisdn,
            status="sent" if ok else "failed", wa_message_id=res.get("message_id", ""),
            error="" if ok else str(res.get("error_code") or res.get("message") or "send failed"),
        )
        sent += int(ok)
        failed += int(not ok)

    broadcast.count_queued = len(links)
    broadcast.count_sent = sent
    broadcast.count_failed = failed
    broadcast.status = Broadcast.DONE
    broadcast.save(update_fields=["count_queued", "count_sent", "count_failed", "status"])
    record_audit("broadcast.send", actor=actor, target=f"broadcast:{broadcast.id}",
                 after={"template": broadcast.template_name, "queued": len(links),
                        "sent": sent, "failed": failed})
    return broadcast
