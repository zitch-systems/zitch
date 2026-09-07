"""Operator-side services: audit log + broadcast sending (§9, §10, §11).

The admin/agent surfaces ride on Django admin + a few staff-only endpoints;
these are the shared services behind them.
"""
import logging
import re

from django.db import transaction as db_transaction

from .models import AuditLog, Broadcast, BroadcastRecipient, WhatsAppLink

_TEMPLATE_NAME_RE = re.compile(r"^[a-z0-9_]{1,120}$")


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


def validate_broadcast_spec(data: dict) -> dict:
    """Return the small, JSON-safe broadcast spec that a checker can approve."""
    template = str((data or {}).get("template_name") or "").strip()
    if not _TEMPLATE_NAME_RE.fullmatch(template):
        raise ValueError("template_name must use lowercase letters, numbers and underscores")

    category = str((data or {}).get("category") or Broadcast.UTILITY).strip().lower()
    if category not in (Broadcast.UTILITY, Broadcast.MARKETING):
        raise ValueError("category must be utility or marketing")

    params = (data or {}).get("body_params", [])
    if not isinstance(params, list) or len(params) > 20:
        raise ValueError("body_params must be a list with at most 20 values")
    if any(isinstance(value, (dict, list)) for value in params):
        raise ValueError("body_params values must be scalar")
    params = [str(value)[:1024] for value in params]

    segment = (data or {}).get("segment", {})
    if not isinstance(segment, dict):
        raise ValueError("segment must be an object")
    unknown = set(segment) - {"tier", "marketing_opt_in"}
    if unknown:
        raise ValueError("unsupported segment field")
    clean_segment = {}
    if "tier" in segment:
        try:
            tier = int(segment["tier"])
        except (TypeError, ValueError) as exc:
            raise ValueError("segment tier must be 1, 2 or 3") from exc
        if tier not in (1, 2, 3):
            raise ValueError("segment tier must be 1, 2 or 3")
        clean_segment["tier"] = tier
    if segment.get("marketing_opt_in"):
        clean_segment["marketing_opt_in"] = True

    return {
        "template_name": template,
        "category": category,
        "body_params": params,
        "segment": clean_segment,
    }


def queue_broadcast(broadcast: Broadcast, actor=None) -> Broadcast:
    """Materialise recipients before any provider call and hand them to the worker."""
    with db_transaction.atomic():
        locked = Broadcast.objects.select_for_update().get(pk=broadcast.pk)
        if locked.status != Broadcast.DRAFT:
            return locked
        links = list(_segment_links(locked))
        BroadcastRecipient.objects.bulk_create([
            BroadcastRecipient(
                broadcast=locked, user=link.user, wa_msisdn=link.wa_msisdn,
            )
            for link in links
        ])
        locked.count_queued = len(links)
        # An empty audience is already complete; otherwise a worker owns delivery.
        locked.status = Broadcast.QUEUED if links else Broadcast.DONE
        locked.save(update_fields=["count_queued", "status"])

    record_audit(
        "broadcast.queued", actor=actor, target=f"broadcast:{locked.id}",
        after={"template": locked.template_name, "category": locked.category,
               "queued": locked.count_queued},
    )
    return locked


def create_queued_broadcast(spec: dict, *, actor=None, approval_request=None) -> Broadcast:
    """Idempotently create and queue the campaign approved by maker/checker."""
    clean = validate_broadcast_spec(spec)
    if approval_request is not None:
        existing = Broadcast.objects.filter(approval_request=approval_request).first()
        if existing is not None:
            return existing
    broadcast = Broadcast.objects.create(
        **clean, created_by=actor, approval_request=approval_request,
    )
    return queue_broadcast(broadcast, actor=actor)


# Compatibility for internal callers while preserving the new durable semantics.
send_broadcast = queue_broadcast
