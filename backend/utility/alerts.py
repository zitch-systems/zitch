"""Operational alerting — page a human when a background job fails or money drifts.

Sentry's Django integration only auto-captures errors that bubble through the
HTTP request/response cycle. The reconcile crons and integrity checks run as
management commands OUTSIDE that cycle, so a cron that crashes — or one that
finds a balance mismatch — would otherwise only write to stderr and the audit
log, and nobody gets paged.

``alert()`` bridges that gap: it ALWAYS logs, and additionally forwards to
Sentry (as a message, or the in-flight exception) when sentry-sdk is
initialised. Error and fatal alerts are also emailed to ``OPS_ALERT_EMAILS``
through Resend, at most once per ``OPS_ALERT_REPEAT_SECONDS`` for the same
message. It is a safe no-op when neither is configured (dev/test/no DSN), and it
never raises — an observability call must never be the thing that breaks the
job it is watching.

No PII (this is fintech; Sentry runs with ``send_default_pii=False``): pass
pseudonymous operational context only — user_id, masked account, references,
amounts — never names, emails, or raw PANs. The operator email carries the same
context and nothing else.
"""
import atexit
import hashlib
import logging
import os
import re
import threading
import time

log = logging.getLogger("zitch")

# Sentry severity levels; also used to pick the stdlib logging method.
_LOG_METHOD = {
    "debug": log.debug,
    "info": log.info,
    "warning": log.warning,
    "error": log.error,
    "fatal": log.critical,
}

#: Levels that mean a person has to look, and so are emailed.
_EMAIL_LEVELS = frozenset({"error", "fatal"})
#: At most this many operator emails per process per hour, whatever happens.
#: A flood of distinct errors is one incident, and the log keeps every line.
_EMAILS_PER_HOUR = 12
_VALUE_LIMIT = 500
_BODY_LIMIT = 4000

_lock = threading.Lock()
_sent = []           # monotonic times of this process's emails in the last hour
_seen_locally = {}   # message digest -> monotonic time, when the shared cache is down


def alert(message: str, *, level: str = "error", exc: bool = False, **context) -> None:
    """Log ``message``, forward it to Sentry when Sentry is active, and email the
    operator for error and fatal levels.

    Args:
        level: one of debug/info/warning/error/fatal (Sentry levels).
        exc:   when True, capture the currently-handled exception (call from
               inside an ``except`` block) so Sentry gets the traceback;
               otherwise the message itself is captured.
        context: extra key/values attached to the Sentry event and appended to
               the log line. Keep it PII-free.
    """
    if level not in _LOG_METHOD:
        level = "error"
    suffix = ("  " + " ".join(f"{k}={v}" for k, v in context.items())) if context else ""
    _LOG_METHOD[level]("%s%s", message, suffix, exc_info=exc)

    if level in _EMAIL_LEVELS:
        try:
            _email_operator(message, level, context)
        except Exception:  # noqa: BLE001 — observability must never raise
            log.warning("ops_alert_email_failed")

    # Best-effort Sentry forward. Import lazily so the SDK is never required, and
    # swallow everything — alerting must not break the caller.
    try:
        import sentry_sdk

        if not sentry_sdk.get_client().is_active():
            return  # init() never ran (no DSN, or tests) — logging above suffices
        with sentry_sdk.new_scope() as scope:
            scope.level = level
            for key, value in context.items():
                scope.set_extra(key, value)
            if exc:
                sentry_sdk.capture_exception()
            else:
                sentry_sdk.capture_message(message, level=level)
    except Exception:  # noqa: BLE001 — observability must never raise
        log.exception("sentry_alert_failed")


def operator_recipients() -> list:
    from django.conf import settings

    return [str(address) for address in (getattr(settings, "OPS_ALERT_EMAILS", None) or [])
            if str(address).strip()]


def operator_email_configured() -> bool:
    from django.conf import settings

    resend = getattr(settings, "RESEND", None) or {}
    return bool(operator_recipients() and resend.get("API_KEY"))


def sentry_active() -> bool:
    try:
        import sentry_sdk

        return bool(sentry_sdk.get_client().is_active())
    except Exception:  # noqa: BLE001 - no SDK, or not initialised
        return False


def operator_channel_configured() -> bool:
    """Whether an error alert reaches a person by some route other than the log."""
    return operator_email_configured() or sentry_active()


#: Context keys that name WHICH incident an alert is about. Two alerts with the
#: same wording but a different customer, reference or case are two incidents
#: and are each emailed; counts (wallets=40, payouts=3) are not identity.
_IDENTITY_KEYS = ("user_id", "user", "wallet_id", "reference", "references", "payout",
                  "payouts_refs", "evidence_id", "evidence_ids", "case_ids", "account",
                  "job", "sample", "dedupe")
#: How long a send in progress holds the window, so two processes raising the
#: same alert at once send one email. Made permanent only once a send succeeds.
_CLAIM_SECONDS = 120
_SEND_ATTEMPTS = 3
#: Resend allows about two requests a second per team.
_SEND_SPACING_SECONDS = 0.6
_QUEUE_LIMIT = 200

_queue = None              # emails waiting for the sender thread
_sender = None             # the one sender thread of this process
_dropped = {"capped": 0}   # alerts refused by the hourly cap since the last email


def _digest(message: str, context: dict) -> str:
    """The incident: the wording with digits folded, plus whatever names it."""
    identity = "|".join(f"{key}={context[key]}" for key in _IDENTITY_KEYS if key in context)
    text = re.sub(r"\d+", "#", message) + "||" + identity
    return hashlib.sha256(text.encode()).hexdigest()[:32]


def _claim(digest: str, timeout: int) -> bool:
    """Claim the window for this incident across every service (the shared
    cache), or for this process alone when the cache is down."""
    try:
        from django.core.cache import cache

        return bool(cache.add(f"ops-alert:email:{digest}", True, timeout=timeout))
    except Exception:  # noqa: BLE001 - a cache outage must not hide an alert
        now = time.monotonic()
        with _lock:
            until = _seen_locally.get(digest)
            if until is not None and now < until:
                return False
            _seen_locally[digest] = now + timeout
            return True


def _hold(digest: str, timeout: int) -> None:
    try:
        from django.core.cache import cache

        cache.set(f"ops-alert:email:{digest}", True, timeout=timeout)
    except Exception:  # noqa: BLE001
        pass
    with _lock:
        _seen_locally[digest] = time.monotonic() + timeout


def _release(digest: str) -> None:
    """A send that did not happen must not silence the next occurrence."""
    try:
        from django.core.cache import cache

        cache.delete(f"ops-alert:email:{digest}")
    except Exception:  # noqa: BLE001
        pass
    with _lock:
        _seen_locally.pop(digest, None)


def _cap_allows() -> bool:
    now = time.monotonic()
    with _lock:
        _sent[:] = [stamp for stamp in _sent if now - stamp < 3600]
        return len(_sent) < _EMAILS_PER_HOUR


def _email_body(message: str, level: str, context: dict) -> tuple:
    from django.conf import settings
    from django.utils import timezone

    lines = [message, ""]
    for key, value in context.items():
        lines.append(f"{key}: {str(value)[:_VALUE_LIMIT]}")
    service = os.environ.get("RENDER_SERVICE_NAME", "")
    repeat_hours = round(int(getattr(settings, "OPS_ALERT_REPEAT_SECONDS", 21600) or 21600) / 3600, 1)
    lines += ["", f"Service: {service or 'unknown'}",
              f"Time (UTC): {timezone.now():%Y-%m-%d %H:%M:%S}",
              f"The same alert is not emailed again for {repeat_hours:g} hours. "
              "Every occurrence is in the service log."]
    with _lock:
        capped, _dropped["capped"] = _dropped["capped"], 0
    if capped:
        lines.append(f"{capped} other alert(s) in the last hour were not emailed "
                     "(hourly limit); see the service log.")
    subject = f"[Zitch {level}] {' '.join(message.split())[:110]}"
    return subject, "\n".join(lines)[:_BODY_LIMIT]


def _send_one(recipients, subject, body) -> bool:
    """Deliver to every recipient, retrying what the provider might accept later."""
    from utility.providers import send_email

    delivered = False
    for address in recipients:
        for attempt in range(1, _SEND_ATTEMPTS + 1):
            try:
                result = send_email(address, subject, body, timeout=10)
            except Exception:  # noqa: BLE001
                result = {"success": False, "uncertain": True}
            if result.get("success") and not result.get("mock"):
                delivered = True
                break
            log.warning("ops_alert_email_not_sent http_status=%s attempt=%s",
                        result.get("http_status"), attempt)
            if not (result.get("retryable") or result.get("uncertain")):
                break
            time.sleep(min(10.0, 2.0 * attempt))
        time.sleep(_SEND_SPACING_SECONDS)
    return delivered


def _send_queued(item) -> None:
    from django.conf import settings

    message, level, context = item
    repeat = int(getattr(settings, "OPS_ALERT_REPEAT_SECONDS", 21600) or 21600)
    digest = _digest(message, context)
    if not _cap_allows():
        with _lock:
            _dropped["capped"] += 1
        return                      # nothing claimed: a later occurrence still emails
    if not _claim(digest, _CLAIM_SECONDS):
        return                      # emailed already in this window, or being sent
    subject, body = _email_body(message, level, context)
    with _lock:
        _sent.append(time.monotonic())
    if _send_one(operator_recipients(), subject, body):
        _hold(digest, repeat)
    else:
        _release(digest)
        with _lock:
            if _sent:
                _sent.pop()


def _sender_loop() -> None:
    while True:
        item = _queue.get()
        try:
            _send_queued(item)
        except Exception:  # noqa: BLE001 - one bad alert must not stop the sender
            log.warning("ops_alert_email_failed")
        finally:
            _queue.task_done()


def _email_operator(message: str, level: str, context: dict) -> None:
    """Queue the alert for this process's one sender thread.

    The caller's thread only checks configuration and enqueues: no cache call,
    no network, so an alert raised while serving a request or holding a row
    lock never waits on Redis or the email provider.
    """
    global _queue, _sender
    if not operator_email_configured():
        return
    import queue as queue_module

    with _lock:
        if _queue is None:
            _queue = queue_module.Queue(maxsize=_QUEUE_LIMIT)
        if _sender is None or not _sender.is_alive():
            thread = threading.Thread(target=_sender_loop, name="ops-alert-email", daemon=True)
            try:
                thread.start()
            except RuntimeError:            # cannot start a thread: log only
                log.warning("ops_alert_email_thread_unavailable")
                return
            _sender = thread
    try:
        _queue.put_nowait((message, level, dict(context)))
    except queue_module.Full:
        with _lock:
            _dropped["capped"] += 1


def _drain(timeout: float = 15.0) -> None:
    """Wait (briefly) for queued operator emails before the process exits."""
    if _queue is None or _sender is None or not _sender.is_alive():
        return
    deadline = time.monotonic() + timeout
    while _queue.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(0.05)


atexit.register(_drain)
