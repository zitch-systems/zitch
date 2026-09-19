from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.models import Count

from cards.models import VirtualCard


class Command(BaseCommand):
    help = (
        "Fail safely when existing data contains more than one virtual-card "
        "row for a user. Run before applying the one-card-per-user constraint."
    )

    def _table_exists(self):
        return VirtualCard._meta.db_table in connection.introspection.table_names()

    def _duplicate_groups(self):
        return list(
            VirtualCard.objects.values("user_id")
            .annotate(card_count=Count("id"))
            .filter(card_count__gt=1)
            .order_by("user_id")[:100]
        )

    def handle(self, *args, **options):
        # A brand-new database has not created the cards table yet. In that case
        # the migration itself creates an empty table and the constraint safely.
        if not self._table_exists():
            self.stdout.write("Virtual-card table not created yet; preflight skipped.")
            return

        duplicate_groups = self._duplicate_groups()
        if not duplicate_groups:
            self.stdout.write(self.style.SUCCESS(
                "Virtual-card uniqueness preflight passed."
            ))
            return

        self.stderr.write(self.style.ERROR(
            "Virtual-card uniqueness preflight failed. Affected user ids "
            "and local row counts follow (maximum 100 groups):"
        ))
        for group in duplicate_groups:
            self.stderr.write(
                f"  user_id={group['user_id']} rows={group['card_count']}"
            )
        raise CommandError(
            "Do not apply cards migration 0004 yet. Back up the database, "
            "reconcile every duplicate against issuer evidence, retire duplicate "
            "cards at the issuer, and keep exactly one authoritative local row "
            "per user. Never choose a row by age or last4 alone."
        )
