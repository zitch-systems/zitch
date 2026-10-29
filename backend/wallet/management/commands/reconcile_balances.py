"""Read-only 9PSB reconciliation including unresolved archived liabilities."""
import json
from django.core.management.base import BaseCommand, CommandError
from banking.audit import snapshot, alert_review
from whatsapp.ops import record_audit


class Command(BaseCommand):
    help = "Compare verified bank balances with customer liabilities without moving money."

    def add_arguments(self, parser):
        parser.add_argument("--tolerance", default="0.00")
        parser.add_argument("--fail-nonzero", action="store_true")
        parser.add_argument("--fail-over", action="store_true")

    def handle(self, *args, **options):
        from decimal import Decimal, InvalidOperation
        try:
            if Decimal(options["tolerance"]) != 0:
                raise ValueError()
        except (InvalidOperation, ValueError):
            raise CommandError("Reconciliation requires exact zero tolerance") from None
        result = snapshot()
        record_audit("recon.balance_check", actor_type="system", after=result)
        self.stdout.write(json.dumps(result, sort_keys=True))
        if result["incomplete"]:
            alert_review("balance reconciliation", result)
        if result["incomplete"] and (options["fail_nonzero"] or options["fail_over"]):
            raise CommandError("Bank balances or historical liabilities require review")
