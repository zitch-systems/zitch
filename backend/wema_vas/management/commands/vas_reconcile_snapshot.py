"""Read an existing bank-search export without bank calls or ledger mutation."""
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from wema_vas.contracts import InvalidPayload
from wema_vas.reconciliation import reconcile_snapshot
from wema_vas.views import _unique_keys

MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024


class Command(BaseCommand):
    help = "Inspect a supplied Wema INBOUND Transaction Search JSON snapshot; never credits/refunds or contacts the bank."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--snapshot", required=True, help="Path to the existing Transaction Search JSON response.")
        query = parser.add_mutually_exclusive_group(required=True)
        query.add_argument("--session-id", default="")
        query.add_argument("--account", default="")

    def handle(self, *args, **options):
        try:
            with Path(options["snapshot"]).open("rb") as source:
                raw = source.read(MAX_SNAPSHOT_BYTES + 1)
            if len(raw) > MAX_SNAPSHOT_BYTES:
                raise InvalidPayload("Snapshot too large")
            body = json.loads(raw, object_pairs_hook=_unique_keys)
            report = reconcile_snapshot(body, session_id=options["session_id"], account=options["account"])
        except (InvalidPayload, OSError, UnicodeError, json.JSONDecodeError, RecursionError):
            raise CommandError("Invalid or unreadable Transaction Search snapshot; no financial changes made.") from None
        except DatabaseError:
            raise CommandError("Local reconciliation evidence unavailable; no financial changes made.") from None
        self.stdout.write(json.dumps(report, indent=2, sort_keys=True))
        if report["action_required"] or not report["observed_count"]:
            raise CommandError("Snapshot requires bank support or further evidence; inspect the redacted report.")
