import signal
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from whatsapp.jobs import process_once
from whatsapp.providers import wa_live


class Command(BaseCommand):
    help = "Process durable WhatsApp inbound messages and broadcast recipients."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--batch-size", type=int, default=20)
        parser.add_argument("--poll-seconds", type=float, default=1.0)

    def handle(self, *args, **options):
        # A misconfigured production worker must never consume commands, execute
        # money movement and then discover that it cannot send the receipt. Local
        # sandbox/test runs remain supported for deterministic E2E verification.
        if not getattr(settings, "DEBUG", False) and not getattr(settings, "TESTING", False):
            if not wa_live():
                raise CommandError("Production WhatsApp worker requires WHATSAPP_MODE=live")
            if not getattr(settings, "WHATSAPP_QUEUE_KEY", ""):
                raise CommandError("Production WhatsApp worker requires WHATSAPP_QUEUE_KEY")

        stopped = False

        def stop(*_args):
            nonlocal stopped
            stopped = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        batch = max(1, min(int(options["batch_size"]), 200))
        poll = max(0.2, min(float(options["poll_seconds"]), 30.0))
        while not stopped:
            inbound, outbound = process_once(batch)
            if options["once"]:
                self.stdout.write(f"inbound={inbound} outbound={outbound}")
                return
            if inbound == 0 and outbound == 0:
                time.sleep(poll)
