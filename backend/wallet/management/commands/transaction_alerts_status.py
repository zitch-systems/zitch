"""Read-only, privacy-bounded notification outbox health."""
from django.core.management.base import BaseCommand
from django.db.models import Count

from wallet.models import TransactionAlertDelivery


class Command(BaseCommand):
    help = "Show per-channel notification state counts; never send or replay alerts."

    def handle(self, *args, **options):
        rows = (TransactionAlertDelivery.objects.values("channel", "state")
                .annotate(count=Count("pk")).order_by("channel", "state"))
        total = attention = 0
        for row in rows:
            total += row["count"]
            if row["state"] in (TransactionAlertDelivery.REVIEW, TransactionAlertDelivery.EXHAUSTED):
                attention += row["count"]
            self.stdout.write(f"{row['channel']} {row['state']}: {row['count']}")
        self.stdout.write(f"total: {total}; review_or_exhausted: {attention}")
