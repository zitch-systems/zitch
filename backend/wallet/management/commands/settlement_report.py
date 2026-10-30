"""Daily liability report; unverified assets never become zero or a green result."""
import json
from django.core.management.base import BaseCommand, CommandError
from banking.audit import snapshot, alert_review
from whatsapp.ops import record_audit


class Command(BaseCommand):
    help = "Report total book liabilities, verified assets and unresolved historical evidence."

    def add_arguments(self, parser):
        parser.add_argument("--max-shortfall", default="0.00")
        parser.add_argument("--max-surplus", default="")
        parser.add_argument("--fail-on-breach", action="store_true")

    def handle(self, *args, **options):
        from decimal import Decimal, InvalidOperation
        try:
            thresholds = [Decimal(options["max_shortfall"])]
            if options["max_surplus"]:
                thresholds.append(Decimal(options["max_surplus"]))
            if any(not n.is_finite() or n < 0 for n in thresholds):
                raise ValueError()
        except (InvalidOperation, ValueError):
            raise CommandError("Invalid settlement threshold") from None
        result = snapshot()
        position = Decimal(result["position"]) if result["position"] is not None else None
        breach = (result["incomplete"] or position is None or position < -thresholds[0]
                  or (len(thresholds) > 1 and position > thresholds[1]))
        record_audit("recon.settlement_report", actor_type="system", after=result)
        self.stdout.write(json.dumps(result, sort_keys=True))
        if breach:
            alert_review("settlement report", result)
        if breach and options["fail_on_breach"]:
            raise CommandError("Settlement is incomplete or outside the declared thresholds")
