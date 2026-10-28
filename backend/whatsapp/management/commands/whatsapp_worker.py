import hashlib
import json
import re
import signal
import threading
import time
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections, connections
from django.utils import timezone

from whatsapp.jobs import process_once
from whatsapp.providers import wa_live


def _local_flow_digest():
    try:
        asset = Path(__file__).resolve().parents[2] / "flow_assets" / "pin_flow.json"
        document = json.loads(asset.read_bytes())
        return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    except (OSError, ValueError, TypeError):
        return "unreadable"


class Command(BaseCommand):
    help = "Process durable WhatsApp messages and retry terminal transaction alerts."

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
        parser.add_argument(
            "--account-repair-interval-seconds",
            type=float,
            default=30.0,
            help="Read back missing partner-bank funding accounts at this interval.",
        )
        parser.add_argument(
            "--alert-retry-interval-seconds",
            type=float,
            default=float(getattr(settings, "WHATSAPP_ALERT_RETRY_INTERVAL_SECONDS", 60.0)),
            help="Retry undelivered terminal transaction alerts at this interval.",
        )
        parser.add_argument(
            "--alert-retry-lookback-days",
            type=int,
            default=int(getattr(settings, "WHATSAPP_ALERT_RETRY_LOOKBACK_DAYS", 2)),
            help="Recent terminal-transaction window for alert retries (default: 2 days).",
        )
        parser.add_argument(
            "--alert-retry-limit",
            type=int,
            default=int(getattr(settings, "WHATSAPP_ALERT_RETRY_LIMIT", 50)),
            help="Maximum terminal transaction alerts to retry per sweep (default: 50).",
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

        selected = str((getattr(settings, "WHATSAPP_FLOW", {}) or {}).get("FLOW_ID") or "")
        selected = selected if re.fullmatch(r"[0-9]{1,40}", selected) else "unset"
        # Evidence from the running worker, not only an API candidate build.
        # No Graph calls, phone/channel identifiers, keys or tokens are logged.
        self.stdout.write(f"whatsapp_worker_flow flow_id={selected} contract_sha256={_local_flow_digest()}")
        self.stdout.flush()

        stopped = False
        reconcile_lock = threading.Lock()
        account_repair_lock = threading.Lock()
        alert_retry_lock = threading.Lock()

        def stop(*_args):
            nonlocal stopped
            stopped = True

        def reconcile_money():
            # This is the safety net for missed bank callbacks. Keep this pass out
            # of the message-processing loop so a slow provider request cannot
            # delay customer replies. The lock prevents a slow pass and the next
            # tick from running concurrently and double-applying a credit.
            if not reconcile_lock.acquire(blocking=False):
                return
            try:
                close_old_connections()
                from banking.reconciliation import sweep

                sweep(limit=10, budget_seconds=40)
            except Exception:  # noqa: BLE001 - reconciliation retries on next tick
                self.stderr.write("background money reconciliation failed; retrying")
            finally:
                connections.close_all()
                reconcile_lock.release()

        def repair_accounts():
            # A verified BVN with no returned NUBAN is a provider-side partial
            # completion, not a reason to ask the customer for their BVN again.
            # This only performs the safe read-back/attach operation and is
            # serialized so a slow provider cannot make overlapping requests.
            if not account_repair_lock.acquire(blocking=False):
                return
            try:
                close_old_connections()
                from banking.reconciliation import sweep

                sweep(limit=3, budget_seconds=20)
            except Exception:  # noqa: BLE001 - retry on the next scheduled pass
                self.stderr.write("background partner-bank account repair failed; retrying")
            finally:
                connections.close_all()
                account_repair_lock.release()

        def retry_transaction_alerts():
            # Money recovery uses the bank outbox. This
            # worker-owned pass only resumes delivery of alerts that were already
            # claimed safely on terminal ledger rows. It uses the worker's Meta
            # credentials, so the bank cron never needs to hold or duplicate them.
            if not alert_retry_lock.acquire(blocking=False):
                return
            try:
                close_old_connections()
                from wallet.alerts import retry_pending_whatsapp_alerts

                retry_pending_whatsapp_alerts(
                    since=timezone.now() - timedelta(days=alert_retry_lookback_days),
                    limit=alert_retry_limit,
                )
            except Exception:  # noqa: BLE001 - delivery is retried on the next scheduled pass
                self.stderr.write("background WhatsApp transaction alert retry failed; retrying")
            finally:
                connections.close_all()
                alert_retry_lock.release()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        batch = max(1, min(int(options["batch_size"]), 200))
        poll = max(0.2, min(float(options["poll_seconds"]), 30.0))
        interval = max(
            5.0,
            min(float(options["settlement_interval_seconds"]), 60.0),
        )
        account_repair_interval = max(
            30.0,
            min(float(options["account_repair_interval_seconds"]), 3600.0),
        )
        alert_retry_interval = max(
            10.0,
            min(float(options["alert_retry_interval_seconds"]), 3600.0),
        )
        alert_retry_lookback_days = max(1, min(int(options["alert_retry_lookback_days"]), 7))
        alert_retry_limit = max(1, min(int(options["alert_retry_limit"]), 200))
        # Money reconciliation belongs to the dedicated cron. It can still be
        # enabled explicitly for a single-process local/test worker, but keeping
        # it off by default prevents several worker processes from sweeping and
        # notifying the same settled transaction.
        run_reconcile = bool(getattr(settings, "WHATSAPP_WORKER_RECONCILE", False))
        next_reconcile = time.monotonic()
        next_account_repair = time.monotonic()
        next_alert_retry = time.monotonic()

        idle_poll = poll
        while not stopped:
            now = time.monotonic()
            if run_reconcile and not options["once"] and now >= next_reconcile:
                threading.Thread(
                    target=reconcile_money,
                    name="money-reconcile",
                    daemon=True,
                ).start()
                next_reconcile = now + interval

            if not run_reconcile and not options["once"] and now >= next_account_repair:
                threading.Thread(
                    target=repair_accounts,
                    name="partner-bank-account-repair",
                    daemon=True,
                ).start()
                next_account_repair = now + account_repair_interval

            if not options["once"] and now >= next_alert_retry:
                threading.Thread(
                    target=retry_transaction_alerts,
                    name="whatsapp-transaction-alert-retry",
                    daemon=True,
                ).start()
                next_alert_retry = now + alert_retry_interval

            inbound, outbound = process_once(batch)
            if options["once"]:
                self.stdout.write(f"inbound={inbound} outbound={outbound}")
                return
            if inbound == 0 and outbound == 0:
                time.sleep(idle_poll)
                idle_poll = min(3.0, max(poll, idle_poll * 1.5))
            else:
                idle_poll = poll
