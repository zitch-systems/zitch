import json
from django.core.management.base import BaseCommand, CommandError
from banking.maintenance import run_due


class Command(BaseCommand):
    help = "Run due maintenance once per durable period; isolate failures."

    def handle(self, *args, **options):
        results = run_due()
        self.stdout.write(json.dumps(results, sort_keys=True))
        if "failed" in results.values():
            raise CommandError("One or more maintenance jobs failed; inspect persisted run records")
