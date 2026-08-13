"""Durable database-backed work queues for the WhatsApp channel.

Inbound command text is encrypted at rest and erased after processing. Outbound
recipients are materialised before Meta is called, so a worker crash cannot lose
the campaign or silently resend a provider-ambiguous request.
"""

import base64
import hashlib
import json
import logging
import threading
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


def enqueue_flow_execution(pa) -> tuple[WaMessageLog, bool]:
    """Queue an authorised payment for the worker.

    Rides the inbound queue rather than inventing a second one: the lease, the
    backoff, the attempt cap and the dead-letter are exactly what money movement
    needs, and they are already written and tested here.

    Keyed on the pending action, so a Flow submitted twice — a customer tapping
    Confirm again after Meta showed them an error — enqueues ONE job. (The payout
    is separately idempotent on the same id; this stops the second job existing
    at all rather than relying on that.)
    """
    return enqueue_inbound(
        message_id=f"flowexec-{pa.pk}",
        msisdn=pa.msisdn,
        # No customer text: this row is an instruction to ourselves, and the log
        # is read by operators.
        logged_text=f"[flow] execute {pa.action_type}",
        payload={"execute_action": pa.pk, "user_id": pa.user_id},
    )


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
        # Preserve the state machine's per-sender order even when several worker
        # processes claim the global queue concurrently. A later reply must not
        # overtake an earlier command and mutate the same PendingAction first.
        earlier_pending = WaMessageLog.objects.filter(
            direction=WaMessageLog.IN,
            msisdn=row.msisdn,
            processed_at__isnull=True,
        ).filter(
            Q(created__lt=row.created) | Q(created=row.created, pk__lt=row.pk),
        ).exists()
        if earlier_pending:
            return None, "sender_busy"
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


def _execute_authorised_action(action_id: int, user_id) -> None:
    """Run a payment whose PIN already passed in the secure Flow.

    A missing action is success, not failure: it means the payment already ran
    (this job replayed after its lease, or the customer's Confirm reached us
    twice). Raising there would retry a completed payment, and the whole point of
    doing this in the queue is that a retry is cheap and safe.
    """
    from django.contrib.auth import get_user_model

    from .models import PendingAction
    from .router import run_flow_execution

    pa = PendingAction.objects.filter(pk=action_id).first()
    if pa is None:
        log.info("wa_flow_execution_already_done action=%s", action_id)
        return
    user = get_user_model().objects.filter(pk=user_id or pa.user_id).first()
    if user is None:
        log.warning("wa_flow_execution_no_user action=%s", action_id)
        return
    run_flow_execution(pa, user)


def process_inbound_message(pk: int, *, raise_errors=False) -> str:
    row, disposition = _claim_inbound(pk)
    if row is None:
        return disposition
    try:
        payload = _decrypt(row.processing_payload)
        if payload.get("execute_action"):
            _execute_authorised_action(int(payload["execute_action"]), payload.get("user_id"))
        elif payload.get("flow_reply"):
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
        ).filter(
            # Rows still under the attempt budget, OR a row that was claimed for
            # its FINAL attempt and never finished. _claim_inbound increments the
            # counter and commits BEFORE the work runs, so a hard kill (deploy,
            # OOM, SIGKILL — not a Python exception, which is handled) leaves
            # attempts == MAX with a stale lease. Filtering on attempts < MAX
            # alone excluded exactly those rows forever: never retried, never
            # dead-lettered, so the customer got no reply and the encrypted
            # payload (which can hold a PIN/BVN) was never erased — defeating the
            # erasure guarantee the dead-letter branch exists to provide. Letting
            # them back in hands them to that branch, which finalises and wipes.
            Q(processing_attempts__lt=MAX_ATTEMPTS)
            | Q(processing_attempts__gte=MAX_ATTEMPTS, processing_started_at__lte=stale),
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
    """Returns ``(row, reason)`` — mirroring _claim_inbound — so the caller can
    tell a dead-letter apart from an ordinary skip. That distinction matters:
    a dead-letter changes the recipient's terminal state and therefore has to
    trigger a broadcast count refresh, which a plain skip must not."""
    now = timezone.now()
    with db_transaction.atomic():
        row = (BroadcastRecipient.objects.select_for_update()
               .select_related("broadcast").filter(pk=pk).first())
        if row is None or row.processed_at is not None or row.status != BroadcastRecipient.QUEUED:
            return None, "skipped"
        if row.next_attempt_at and row.next_attempt_at > now:
            return None, "skipped"
        if row.processing_started_at and row.processing_started_at > now - OUTBOUND_LEASE:
            return None, "skipped"
        if row.processing_attempts >= MAX_ATTEMPTS:
            # Dead-letter on claim, mirroring _claim_inbound. Without this a
            # recipient whose FINAL send was interrupted by a hard kill stayed
            # QUEUED with processed_at NULL forever: refresh_broadcast_counts
            # treats any such row as still active, so the whole broadcast was
            # pinned in SENDING and never reached DONE — no completion audit, no
            # final counts. There is no operator recovery action on the outbound
            # side (unlike the inbound admin "Process now"), so nothing else
            # would ever finalise it.
            row.status = BroadcastRecipient.FAILED
            row.processed_at = now
            row.processing_started_at = None
            row.error = row.error or "dead_letter:max_attempts"
            row.save(update_fields=["status", "processed_at", "processing_started_at", "error"])
            return None, "dead_letter"
        row.processing_started_at = now
        row.processing_attempts += 1
        row.next_attempt_at = None
        row.error = ""
        row.save(update_fields=["processing_started_at", "processing_attempts",
                                "next_attempt_at", "error"])
        if row.broadcast.status in {Broadcast.QUEUED, Broadcast.DRAFT}:
            Broadcast.objects.filter(pk=row.broadcast_id).update(status=Broadcast.SENDING)
        return row, "claimed"


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
    row, disposition = _claim_outbound(pk)
    if row is None:
        if disposition == "dead_letter":
            # The claim itself moved the recipient to a terminal FAILED state, so
            # the broadcast's counts and status are now stale. Every other path
            # that finalises a recipient refreshes them; skipping it here would
            # leave the broadcast pinned in SENDING with nothing left to process
            # — the exact wedge the dead-letter branch exists to clear. Re-read
            # the FK rather than returning the locked row, so a caller that only
            # checks `row is None` can never mistake a dead letter for a claim.
            broadcast_id = (BroadcastRecipient.objects.filter(pk=pk)
                            .values_list("broadcast_id", flat=True).first())
            if broadcast_id is not None:
                refresh_broadcast_counts(broadcast_id)
        return disposition
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
        ).filter(
            # Same reclaim rule as the inbound sweep: a recipient claimed for its
            # final attempt and then hard-killed sits at attempts == MAX with a
            # stale lease, and filtering on attempts < MAX alone stranded it in
            # QUEUED forever — wedging its broadcast in SENDING. Re-selecting it
            # hands it to _claim_outbound's dead-letter branch above.
            Q(processing_attempts__lt=MAX_ATTEMPTS)
            | Q(processing_attempts__gte=MAX_ATTEMPTS, processing_started_at__lte=stale),
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


# --------------------------------------------------------------------------- #
# web-service safety net
# --------------------------------------------------------------------------- #
# Replies are the worker's job. But the worker is a SEPARATE Render service, and
# when it is not running — never created, crashed at boot on a missing credential,
# OOM-killed, suspended for billing — the webhook still stores the message and
# still answers Meta 200, so the channel looks healthy from every angle while the
# customer gets nothing back. A chat product whose entire delivery path hangs on
# one process that fails silently is a bad bet.
#
# So the web service drains the queue too, opportunistically, on the one event
# that proves work exists: an inbound webhook. It changes no semantics. Rows are
# claimed with SELECT FOR UPDATE and a lease, so a running worker and this thread
# cannot process the same message — whichever claims it first wins and the other
# skips. With a healthy worker this thread finds nothing and costs nothing.
#
# It is deliberately bounded: one drain at a time per process, a small batch, and
# every failure swallowed. This is a safety net, not a second worker — it must
# never become the reason an HTTP worker thread is unavailable to serve requests.
_DRAIN_LOCK = threading.Lock()
WEB_DRAIN_BATCH = 5


def _drain_worker(batch: int) -> None:
    from django.db import connections

    try:
        process_inbound_batch(batch)
    except Exception:  # noqa: BLE001 — a safety net must never raise into nothing
        log.exception("wa_web_drain_failed")
    finally:
        try:
            # Connections are thread-local and this thread is outside the
            # request/response cycle, so Django never reaps them. Leaking one per
            # webhook would exhaust the Postgres connection limit within a day of
            # ordinary traffic. close_all() only touches THIS thread's.
            connections.close_all()
        except Exception:  # noqa: BLE001
            log.exception("wa_web_drain_cleanup_failed")
        finally:
            # Released last, so the next webhook cannot start a drain while this
            # one is still tearing down.
            _DRAIN_LOCK.release()


def drain_in_background(batch: int = WEB_DRAIN_BATCH) -> bool:
    """Kick off a bounded queue drain off the request thread. Returns whether one
    started — False means a drain is already running in this process, which is
    not an error: the message is durably queued and the running drain (or the
    worker, or the next webhook) will pick it up."""
    if not getattr(settings, "WHATSAPP_WEB_DRAIN", True):
        return False
    if not _DRAIN_LOCK.acquire(blocking=False):
        return False
    try:
        # daemon: a deploy/restart must not wait on this. A thread killed
        # mid-flight leaves the row claimed but unprocessed, and the five-minute
        # lease returns it to the queue — the same recovery a crashed worker gets.
        threading.Thread(target=_drain_worker, args=(batch,),
                         name="wa-web-drain", daemon=True).start()
        return True
    except Exception:  # noqa: BLE001 — e.g. thread limit reached
        _DRAIN_LOCK.release()
        log.exception("wa_web_drain_start_failed")
        return False
