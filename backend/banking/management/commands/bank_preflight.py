import json
from django.core.management.base import BaseCommand, CommandError
from banking.readiness import report
from utility.launch_readiness import financial_controls_report


class Command(BaseCommand):
    help = "Report current bank readiness without credentials, PII or payment instructions."

    def add_arguments(self, parser):
        parser.add_argument("--report-only", action="store_true")
        parser.add_argument("--strict", action="store_true")
        parser.add_argument("--launch-scope", choices=["core", "full"], default="full")

    def handle(self, *args, **options):
        result = report()
        result["financial_controls"] = financial_controls_report()
        self.stdout.write(json.dumps(result, sort_keys=True))
        if not options["report_only"] and (not result["production_ready"] or not result["financial_controls"].get("financial_controls_ready")):
            raise CommandError("Production banking is not ready; see redacted blockers")
