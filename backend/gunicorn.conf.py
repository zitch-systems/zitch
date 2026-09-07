"""Production defaults even when a Render service has a bare gunicorn command.

The repository Blueprint supplies the same values explicitly, but the long-lived
Render service predates that Blueprint and currently starts with only
``gunicorn zitch_api.wsgi:application``. Gunicorn automatically reads this file from
the service working directory, so both deployment paths get the same bounded,
threaded process model without a dashboard-only command edit.
"""
import os


def _positive_int(name: str, default: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, maximum))


bind = f"0.0.0.0:{_positive_int('PORT', 10000, 65535)}"

# One process fits the current 512 MiB service and keeps a safe margin for image/
# provider payloads. Eight threads absorb slow upstream I/O without multiplying the
# Django/provider memory footprint. Shared Redis still backs cross-request controls.
workers = _positive_int("WEB_CONCURRENCY", 1, 4)
worker_class = "gthread"
threads = _positive_int("GUNICORN_THREADS", 8, 32)
timeout = _positive_int("GUNICORN_TIMEOUT", 60, 120)
graceful_timeout = _positive_int("GUNICORN_GRACEFUL_TIMEOUT", 30, 120)
keepalive = _positive_int("GUNICORN_KEEPALIVE", 5, 30)

# Recycle a process eventually so a slow native/provider leak cannot grow forever;
# jitter prevents synchronized restarts if the service is scaled later.
max_requests = _positive_int("GUNICORN_MAX_REQUESTS", 2000, 100000)
max_requests_jitter = _positive_int("GUNICORN_MAX_REQUESTS_JITTER", 200, 10000)

accesslog = "-"
errorlog = "-"
capture_output = True
