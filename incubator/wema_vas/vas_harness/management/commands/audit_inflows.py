"""Read-only consistency check of the isolated VAS receipt ledger."""
import json
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.db.models import Sum

from ...models import Inflow, VirtualAccount


class Command(BaseCommand):
    help = "Compare each isolated account balance against accepted inflows. Never adjusts money."

    def handle(self, *args, **options):
        findings = []
        with transaction.atomic():
            if connection.vendor == "postgresql":
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            for account in VirtualAccount.objects.filter(mode=settings.VAS_MODE).order_by("number"):
                credited = Inflow.objects.filter(account=account, held=False).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
                if account.simulated_balance != credited:
                    findings.append({"account": account.number, "posted_balance": str(account.simulated_balance),
                                     "accepted_receipts": str(credited), "action": "manual_bank_evidence_review"})
        self.stdout.write(json.dumps({"checked_mode": settings.VAS_MODE, "findings": findings}, sort_keys=True))
