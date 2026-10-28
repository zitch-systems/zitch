import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from banking.uat import sandbox_checks


class Command(BaseCommand):
    help = "Run redacted sandbox connectivity checks without purchases, identity submissions or activation."

    def add_arguments(self, parser):
        parser.add_argument("--fixtures", help="Private JSON file containing bank-provided account_number/mobile test fixtures")

    def handle(self, *args, **options):
        try:
            fixtures = json.loads(Path(options["fixtures"]).read_text()) if options["fixtures"] else {}
            result = sandbox_checks(fixtures)
        except (OSError, ValueError):
            raise CommandError("Sandbox environment and valid private test fixtures are required") from None
        self.stdout.write(json.dumps(result, sort_keys=True))
        if not result["connectivity_passed"]:
            raise CommandError("Sandbox checks are incomplete; see the redacted report")
