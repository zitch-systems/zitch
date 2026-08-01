"""Durable database-backed work queues for the WhatsApp channel.

Inbound command text is encrypted at rest and erased after processing. Outbound
recipients are materialised before Meta is called, so a worker crash cannot lose
the campaign or silently resend a provider-ambiguous request.
"""

import base64
import hashlib
import json
import logging
from datetime import timedelta

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import IntegrityError, transaction as db_transaction
from django.db.models import Q
from django.utils import timezone

from .models import Broadcast, BroadcastRecipient, WaMessageLog
from .ops import record_audit
from .providers import send_template
from .router import handle_inbound, reply

log = logging.getLogger("whatsapp.worker")

INBOUND_LEASE = timedelta(minutes=5)
OUTBOUND_LEASE = timedelta(minutes=5)
MAX_ATTEMPTS = 5


def _fernet(secret: str | None = None) -> Fernet:
    secret = str(secret if secret is not None
                 else getattr(settings, "WHATSAPP_QUEUE_KEY", "") or "")
    if not secret:
        raise RuntimeError("WHATSAPP_QUEUE_KEY is not configured")
    digest = hashlib.sha256(b"zitch-whatsapp-queue-v1\0" + secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt(payload: dict) -> bytes:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    return _fernet().encrypt(raw)


def _decrypt(value: bytes) -> dict:
    secrets = [getattr(settings, "WHATSAPP_QUEUE_KEY", "")]
    previous = getattr(settings, "WHATSAPP_QUEUE_KEY_PREV", "")
    if previous and previous not in secrets:
        secrets.append(previous)
    for secret in secrets:
        if not secret:
            continue
        try:
            raw = _fernet(secret).decrypt(bytes(value or b""))
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise InvalidToken
            return payload
        except (InvalidToken, json.JSONDecodeError, UnicodeDecodeError):
            continue
    raise InvalidToken


def enqueue_inbound(*, message_id: str, msisdn: str, logged_text: str,
                    payload: dict) -> tuple[WaMessageLog, bool]:
    """Persist one Meta message idempotently; never overwrite an existing job."""
    try:
        with db_transaction.atomic():
            row, created = WaMessageLog.objects.get_or_create(
                wa_message_id=message_id,
                defaults={
                    "msisdn": msisdn,
                    "direction": WaMessageLog.IN,
                    "text": logged_text,
                    "processing_payload": _encrypt(payload),
                },
            )
    except IntegrityError:
        # A concurrent insert can win after get_or_create's read on databases
        # with weaker isolation. The unique message id remains the authority.
        row = WaMessageLog.objects.get(wa_message_id=message_id)
        created = False
    return row, created


def discard_inbound(*, message_id: str, msisdn: str, logged_text: str,
                    reason: str) -> WaMessageLog:
    """Record a deliberately dropped message as terminal (for rate-limit evidence)."""
    row, _ = WaMessageLog.objects.get_or_create(
        wa_message_id=message_id,
        defaults={
            "msisdn": msisdn,
            "direction": WaMessageLog.IN,
            "text": logged_text,
            "processed_at": timezone.now(),
            "processing_error": reason[:64],
        },
    )
    return row


def _claim_inbound(pk: int):
    now = timezone.now()
    with db_transaction.atomic():
        row = WaMessageLog.objects.select_for_update().filter(
            pk=pk, direction=WaMessageLog.IN,
        ).first()
        if row is None or row.processed_at is not None:
            return None, "done"
        if row.next_attempt_at and row.next_attempt_at > now:
            return None, "deferred"
        if row.processing_started_at and row.processing_started_at > now - INBOUND_LEASE:
            return None, "busy"
        if row.processing_attempts >= MAX_ATTEMPTS:
            row.processed_at = now
            row.processing_started_at = None
            row.processing_error = row.processing_error or "dead_letter:max_attempts"
            # A dead letter is terminal: retain the non-sensitive error and audit
            # metadata, but erase the encrypted command text/identifier payload.
            # There is no legitimate reason to retain a PIN/BVN indefinitely after
            # automatic recovery has been exhausted.
            row.processing_payload = b""
            row.save(update_fields=[
                "processed_at", "processing_started_at", "processing_error",
                "processing_payload",
            ])
            return None, "dead_letter"
        row.processing_started_at = now
        row.processing_attempts += 1
        row.next_attempt_at = None
        row.processing_error = ""
        row.save(update_fields=["processing_started_at", "processing_attempts",
                                "next_attempt_at", "processing_error"])
        return row, "claimed"


def process_inbound_message(pk: int, *, raise_errors=False) -> str:
    row, disposition = _claim_inbound(pk)
    if row is None:
        return disposition
    try:
        payload = _decrypt(row.processing_payload)
        if payload.get("flow_reply"):
            pass
        elif not payload.get("is_text"):
            reply(row.msisdn, 'I can only read text messages for now. Reply "menu" for options.')
        else:
            handle_inbound(row.msisdn, str(payload.get("body") or ""))
    except Exception as exc:  # noqa: BLE001 — durable retry/dead-letter boundary
        error = f"{type(exc).__name__}"[:48]
        terminal = isinstance(exc, (InvalidToken, json.JSONDecodeError)) or row.processing_attempts >= MAX_ATTEMPTS
        updates = {
            "processing_started_at": None,
            "processing_error": ("dead_letter:" if terminal else "") + error,
        }
        if terminal:
            updates["processed_at"] = timezone.now()
            updates["processing_payload"] = b""
        else:
            updates["next_attempt_at"] = timezone.now() + timedelta(
                seconds=min(300, 2 ** row.processing_attempts),
            )
        WaMessageLog.objects.filter(pk=row.pk).update(**updates)
        log.exception("wa_inbound_job_failed id=%s attempt=%s", row.pk, row.processing_attempts)
        if raise_errors:
            raise
        return "dead_letter" if terminal else "retry"

    WaMessageLog.objects.filter(pk=row.pk).update(
        processed_at=timezone.now(), processing_started_at=None,
        next_attempt_at=None, processing_error="", processing_payload=b"",
    )
    return "processed"


def process_inbound_batch(limit=20) -> int:
    now = timezone.now()
    stale = now - INBOUND_LEASE
    ids = list(
        WaMessageLog.objects.filter(
            direction=WaMessageLog.IN, processed_at__isnull=True,
            processing_attempts__lt=MAX_ATTEMPTS,
        ).filter(
            Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now),
        ).filter(
            Q(processing_started_at__isnull=True) | Q(processing_started_at__lte=stale),
        ).order_by("created").values_list("pk", flat=True)[:limit]
    )
    processed = 0
    for pk in ids:
        processed += int(process_inbound_message(pk) in {"processed", "dead_letter"})
    return processed


def _claim_outbound(pk: int):
    now = timezone.now()
    with db_transaction.atomic():
        row = (BroadcastRecipient.objects.select_for_update()
               .select_related("broadcast").filter(pk=pk).first())
        if row is None or row.processed_at is not None or row.status != BroadcastRecipient.QUEUED:
            return None
        if row.next_attempt_at and row.next_attempt_at > now:
            return None
        if row.processing_started_at and row.processing_started_at > now - OUTBOUND_LEASE:
            return None
        row.processing_started_at = now
        row.processing_attempts += 1
        row.next_attempt_at = None
        row.error = ""
        row.save(update_fields=["processing_started_at", "processing_attempts",
                                "next_attempt_at", "error"])
        if row.broadcast.status in {Broadcast.QUEUED, Broadcast.DRAFT}:
            Broadcast.objects.filter(pk=row.broadcast_id).update(status=Broadcast.SENDING)
        return row


def refresh_broadcast_counts(broadcast_id: int) -> None:
    completed = None
    with db_transaction.atomic():
        broadcast = Broadcast.objects.select_for_update().get(pk=broadcast_id)
        qs = broadcast.recipients
        sent = qs.filter(status__in=[BroadcastRecipient.SENT, BroadcastRecipient.DELIVERED,
                                     BroadcastRecipient.READ]).count()
        delivered = qs.filter(status=BroadcastRecipient.DELIVERED).count()
        read = qs.filter(status=BroadcastRecipient.READ).count()
        failed = qs.filter(status=BroadcastRecipient.FAILED).count()
        unknown = qs.filter(status=BroadcastRecipient.UNKNOWN).count()
        active = qs.filter(status=BroadcastRecipient.QUEUED, processed_at__isnull=True).exists()
        old_status = broadcast.status
        broadcast.count_sent = sent
        broadcast.count_delivered = delivered
        broadcast.count_read = read
        broadcast.count_failed = failed
        broadcast.count_unknown = unknown
        broadcast.status = Broadcast.SENDING if active else Broadcast.DONE
        broadcast.save(update_fields=[
            "count_sent", "count_delivered", "count_read", "count_failed",
            "count_unknown", "status",
        ])
        if old_status != Broadcast.DONE and broadcast.status == Broadcast.DONE:
            completed = {
                "queued": broadcast.count_queued, "sent": sent,
                "failed": failed, "unknown": unknown,
            }
    if completed is not None:
        record_audit(
            "broadcast.completed", actor_type="system",
            target=f"broadcast:{broadcast_id}", after=completed,
        )


def process_outbound_recipient(pk: int) -> str:
    row = _claim_outbound(pk)
    if row is None:
        return "skipped"
    try:
        result = send_template(
            row.wa_msisdn, row.broadcast.template_name, row.broadcast.body_params,
        )
    except Exception as exc:  # defensive boundary around provider adapters
        log.exception("wa_outbound_provider_crashed recipient_id=%s", row.pk)
        result = {"success": False, "uncertain": True, "message": type(exc).__name__}

    now = timezone.now()
    if result.get("success"):
        status = BroadcastRecipient.SENT
        updates = {
            "status": status, "wa_message_id": str(result.get("message_id") or "")[:128],
            "error": "", "processed_at": now, "processing_started_at": None,
            "next_attempt_at": None,
        }
    elif result.get("retryable") and row.processing_attempts < MAX_ATTEMPTS:
        updates = {
            "processing_started_at": None,
            "next_attempt_at": now + timedelta(seconds=min(300, 2 ** row.processing_attempts)),
            "error": str(result.get("error_code") or "retryable_provider_error")[:200],
        }
        status = "retry"
    else:
        status = (BroadcastRecipient.UNKNOWN if result.get("uncertain")
                  else BroadcastRecipient.FAILED)
        updates = {
            "status": status, "processed_at": now, "processing_started_at": None,
            "next_attempt_at": None,
            "error": str(result.get("error_code") or result.get("message")
                         or "provider_rejected")[:200],
        }
    BroadcastRecipient.objects.filter(pk=row.pk).update(**updates)
    refresh_broadcast_counts(row.broadcast_id)
    return status


def process_outbound_batch(limit=20) -> int:
    now = timezone.now()
    stale = now - OUTBOUND_LEASE
    ids = list(
        BroadcastRecipient.objects.filter(
            status=BroadcastRecipient.QUEUED, processed_at__isnull=True,
            processing_attempts__lt=MAX_ATTEMPTS,
        ).filter(
            Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now),
        ).filter(
            Q(processing_started_at__isnull=True) | Q(processing_started_at__lte=stale),
        ).order_by("created").values_list("pk", flat=True)[:limit]
    )
    for pk in ids:
        process_outbound_recipient(pk)
    return len(ids)


def process_once(limit=20) -> tuple[int, int]:
    return process_inbound_batch(limit), process_outbound_batch(limit)
