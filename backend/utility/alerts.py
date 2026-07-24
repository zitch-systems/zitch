"""Operational alerting — page a human when a background job fails or money drifts.

Sentry's Django integration only auto-captures errors that bubble through the
HTTP request/response cycle. The reconcile crons and integrity checks run as
management commands OUTSIDE that cycle, so a cron that crashes — or one that
finds a balance mismatch — would otherwise only write to stderr and the audit
log, and nobody gets paged.

``alert()`` bridges that gap: it ALWAYS logs, and additionally forwards to
Sentry (as a message, or the in-flight exception) when sentry-sdk is
initialised. It is a safe no-op when Sentry isn't configured (dev/test/no DSN),
and it never raises — an observability call must never be the thing that breaks
the job it is watching.

No PII (this is fintech; Sentry runs with ``send_default_pii=False``): pass
pseudonymous operational context only — user_id, masked account, references,
amounts — never names, emails, or raw PANs.
"""
import logging

log = logging.getLogger("zitch")

# Sentry severity levels; also used to pick the stdlib logging method.
_LOG_METHOD = {
    "debug": log.debug,
    "info": log.info,
    "warning": log.warning,
    "error": log.error,
    "fatal": log.critical,
}


def alert(message: str, *, level: str = "error", exc: bool = False, **context) -> None:
    """Log ``message`` and forward it to Sentry when Sentry is active.

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
