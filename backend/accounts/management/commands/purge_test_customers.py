"""One-time destructive reset of customer test data.

This command exists for the final pre-launch reset only. It preserves staff
operators and system configuration while deleting every non-staff customer and
the records connected to those customers. The ledger trigger is suspended only
inside the database transaction and is always restored.
"""

import os

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection, models, transaction
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.core.management.base import BaseCommand, CommandError


CONFIRMATION = "DELETE-ALL-TEST-CUSTOMERS"


class Command(BaseCommand):
    help = "Irreversibly delete every customer and connected test record, preserving operators."

    def add_arguments(self, parser):
        parser.add_argument("--confirm", required=True)

    def handle(self, *args, **options):
        if os.environ.get("ALLOW_TEST_DATA_PURGE", "").lower() != "true":
            raise CommandError("Set ALLOW_TEST_DATA_PURGE=true for this one deployment.")
        if options["confirm"] != CONFIRMATION:
            raise CommandError(f"Confirmation must exactly equal {CONFIRMATION}.")

        User = get_user_model()
        # Preserve operators defensively even if a historical/manual edit left
        # one of their flags inconsistent. Role-group membership is sufficient
        # to keep an account out of this destructive set.
        operator_roles = ("super_admin", "finance", "support", "read_only")
        customers = User.objects.exclude(
            Q(is_staff=True) | Q(is_superuser=True) | Q(groups__name__in=operator_roles)
        ).distinct()
        before = customers.count()
        if before == 0:
            self.stdout.write(self.style.SUCCESS("No non-staff customers found; nothing to purge."))
            return

        # Django's PROTECT relationships are correct for normal operation. For
        # this explicitly confirmed pre-launch reset, temporarily teach the ORM
        # collector to cascade through protected customer-owned records. The
        # model metadata is restored in finally, regardless of success.
        protected_fields = []
        for model in apps.get_models():
            for field in model._meta.fields:
                remote = getattr(field, "remote_field", None)
                if remote is not None and remote.on_delete is models.PROTECT:
                    protected_fields.append((remote, remote.on_delete))
                    remote.on_delete = models.CASCADE

        try:
            with transaction.atomic():
                if connection.vendor == "postgresql":
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "ALTER TABLE wallet_transaction "
                            "DISABLE TRIGGER wallet_transaction_immutable_guard"
                        )
                try:
                    deleted, detail = customers.delete()
                except ProtectedError as exc:
                    raise CommandError(
                        "A protected relation was not included in the purge collector; "
                        "the transaction was rolled back."
                    ) from exc

                if connection.vendor == "postgresql":
                    # This is deliberately in the same atomic transaction as
                    # DISABLE. Any exception rolls both ALTER statements and all
                    # deletes back, so the immutable-ledger trigger cannot be
                    # left disabled by a partial purge.
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "ALTER TABLE wallet_transaction "
                            "ENABLE TRIGGER wallet_transaction_immutable_guard"
                        )

                remaining = customers.count()
                if remaining:
                    raise CommandError(
                        f"Purge verification failed: {remaining} customer(s) remain; rolled back."
                    )

                from whatsapp.ops import record_audit
                record_audit(
                    "ops.purge_test_customers",
                    actor_type="system",
                    after={
                        "customers": before,
                        "rows": deleted,
                        "models": dict(sorted(detail.items())),
                    },
                )
        finally:
            for remote, original in protected_fields:
                remote.on_delete = original

        summary = ", ".join(f"{label}={count}" for label, count in sorted(detail.items()))
        self.stdout.write(self.style.SUCCESS(
            f"Purged {before} test customer(s) and {deleted} connected row(s). {summary}"
        ))
