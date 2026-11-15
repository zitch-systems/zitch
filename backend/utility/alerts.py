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
_pending = []        # email threads still sending
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


def _first_in_window(message: str) -> bool:
    """True the first time ``message`` is seen in the repeat window.

    Digits are folded so "all 3 fetches failed" and "all 4 fetches failed" are
    one alert. The shared cache spreads the window across every service; without
    it each process keeps its own.
    """
    from django.conf import settings

    repeat = int(getattr(settings, "OPS_ALERT_REPEAT_SECONDS", 21600) or 21600)
    digest = hashlib.sha256(re.sub(r"\d+", "#", message).encode()).hexdigest()[:32]
    try:
        from django.core.cache import cache

        return bool(cache.add(f"ops-alert:email:{digest}", True, timeout=repeat))
    except Exception:  # noqa: BLE001 - a cache outage must not hide an alert
        now = time.monotonic()
        with _lock:
            last = _seen_locally.get(digest)
            if last is not None and now - last < repeat:
                return False
            _seen_locally[digest] = now
            return True


def _under_hourly_cap() -> bool:
    now = time.monotonic()
    with _lock:
        _sent[:] = [stamp for stamp in _sent if now - stamp < 3600]
        if len(_sent) >= _EMAILS_PER_HOUR:
            return False
        _sent.append(now)
        return True


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
    subject = f"[Zitch {level}] {' '.join(message.split())[:110]}"
    return subject, "\n".join(lines)[:_BODY_LIMIT]


def _email_operator(message: str, level: str, context: dict) -> None:
    if not operator_email_configured():
        return
    if not _first_in_window(message) or not _under_hourly_cap():
        return
    recipients = operator_recipients()
    subject, body = _email_body(message, level, context)

    def send():
        from utility.providers import send_email

        for address in recipients:
            try:
                result = send_email(address, subject, body, timeout=10)
                if not result.get("success") or result.get("mock"):
                    log.warning("ops_alert_email_not_sent http_status=%s",
                                result.get("http_status"))
            except Exception:  # noqa: BLE001 - the log line above is the record
                log.warning("ops_alert_email_failed")

    # Off the caller's thread: an alert raised while serving a request or moving
    # money must not wait on the email provider. _drain() lets a short-lived
    # command finish sending before the process exits.
    thread = threading.Thread(target=send, name="ops-alert-email", daemon=True)
    with _lock:
        _pending[:] = [t for t in _pending if t.is_alive()]
        _pending.append(thread)
    thread.start()


def _drain(timeout: float = 15.0) -> None:
    """Wait (briefly) for operator emails still being sent."""
    deadline = time.monotonic() + timeout
    with _lock:
        threads = list(_pending)
        _pending.clear()
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))


atexit.register(_drain)
