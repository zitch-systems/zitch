import signal
import threading
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
        parser.add_argument(
            "--settlement-interval-seconds",
            type=float,
            default=10.0,
            help="Run money reconciliation in the background at this interval.",
        )

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
        reconcile_lock = threading.Lock()

        def stop(*_args):
            nonlocal stopped
            stopped = True

        def reconcile_money():
            # The bank has no webhook for these state changes. Keep this pass out
            # of the message-processing loop so a slow provider request cannot
            # delay customer replies. The lock prevents a slow pass and the next
            # tick from running concurrently and double-applying a credit.
            if not reconcile_lock.acquire(blocking=False):
                return
            try:
                from utility.management.commands.reconcile_vtu import Command as VtuCommand
                from utility.management.commands.reconcile_wema import Command as WemaCommand

                VtuCommand().handle(older_than_minutes=0)
                WemaCommand()._run(
                    lookback_days=2,
                    payout_older_than_minutes=2,
                    account_recovery_limit=20,
                )
            except Exception:  # noqa: BLE001 - reconciliation retries on next tick
                self.stderr.write("background money reconciliation failed; retrying")
            finally:
                reconcile_lock.release()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        batch = max(1, min(int(options["batch_size"]), 200))
        poll = max(0.2, min(float(options["poll_seconds"]), 30.0))
        interval = max(
            5.0,
            min(float(options["settlement_interval_seconds"]), 60.0),
        )
        next_reconcile = time.monotonic()

        while not stopped:
            now = time.monotonic()
            if not options["once"] and now >= next_reconcile:
                threading.Thread(
                    target=reconcile_money,
                    name="money-reconcile",
                    daemon=True,
                ).start()
                next_reconcile = now + interval

            inbound, outbound = process_once(batch)
            if options["once"]:
                self.stdout.write(f"inbound={inbound} outbound={outbound}")
                return
            if inbound == 0 and outbound == 0:
                time.sleep(poll)
