import json
from django.core.management.base import BaseCommand, CommandError
from banking.reconciliation import sweep


class Command(BaseCommand):
    help = "Recover confirmed 9PSB payments, notifications, identities and account status."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=10)
        parser.add_argument("--budget-seconds", type=float, default=45)

    def handle(self, *args, **options):
        result = sweep(limit=options["limit"], budget_seconds=options["budget_seconds"])
        self.stdout.write(json.dumps(result, sort_keys=True))
        if result["errors"]:
            raise CommandError("Bank recovery has unconfirmed reads; persisted work remains queued")
