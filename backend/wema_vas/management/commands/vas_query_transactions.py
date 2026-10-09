"""Query a single bank rail and compare returned inbound rows without writes."""
import json
from django.core.management.base import BaseCommand, CommandError
from wema_vas.contracts import InvalidPayload
from wema_vas.transaction_query import QueryUnavailable, query_and_reconcile


class Command(BaseCommand):
    help = "Read-only Wema NIP/Etranzact inbound transaction verification. Never moves money."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--rail", choices=("nip", "etranzact"), required=True)
        query = parser.add_mutually_exclusive_group(required=True)
        query.add_argument("--session-id", default="")
        query.add_argument("--account", default="")

    def handle(self, *args, **options):
        try:
            report = query_and_reconcile(options["rail"], session_id=options["session_id"], account=options["account"])
        except (InvalidPayload, QueryUnavailable) as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(json.dumps(report, indent=2, sort_keys=True))
        if report["action_required"] or not report["observed_count"]:
            raise CommandError("No verified transaction match; inspect the redacted report and bank evidence.")
