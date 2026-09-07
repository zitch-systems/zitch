import logging
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "zitch_api.settings")

application = get_wsgi_application()


def _recover_whatsapp_queue_on_startup() -> None:
    """Drain a bounded batch that may have survived the previous web process.

    The free Render service has no dedicated WhatsApp worker.  Its normal
    post-webhook drain runs in a daemon thread, so a cold start can acknowledge a
    callback and then reap the thread before it claims the durable row.  Waiting
    for another customer callback to recover that row can leave the first customer
    unanswered indefinitely.

    WSGI startup is the one point at which the replacement process is known to be
    alive.  The normal queue lease makes this safe when several web workers (or a
    future dedicated worker) start together, and the bounded batch keeps startup
    recovery from turning the web process into a queue worker.
    """
    try:
        from whatsapp.jobs import WEB_DRAIN_BATCH, drain_in_background

        drain_in_background(batch=WEB_DRAIN_BATCH)
    except Exception:  # noqa: BLE001 - queue recovery must not prevent HTTP startup
        logging.getLogger("whatsapp").exception("wa_startup_drain_failed")


_recover_whatsapp_queue_on_startup()
