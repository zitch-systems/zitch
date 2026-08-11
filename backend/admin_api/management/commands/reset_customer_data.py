"""One-time, simulation-only customer/test-data reset.

This command exists for a deliberately isolated pre-launch environment.  It is
not a general account-deletion path: execution requires the two production
simulation switches, an exact confirmation phrase, and a 256-bit nonce supplied
through the deployment environment.  A hashed receipt makes each nonce usable
at most once.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from collections import defaultdict

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction


CONFIRMATION = "CONFIRM CUSTOMER RESET"
RECEIPT_KEY = "customer_reset_last_nonce_sha256"

# These tables are customer-owned but intentionally PROTECT their User foreign
# key.  Delete only rows belonging to non-staff/non-superuser accounts; operator
# financial state (if any) is outside this reset's scope.
CUSTOMER_MODELS = (
    ("loans", "Loan"),
    ("savings", "FixedSave"),
    ("cards", "VirtualCard"),
    ("convert", "ConversionRequest"),
    ("banklink", "LinkedBankAccount"),
    ("transfers", "Beneficiary"),
    ("wallet", "FxQuote"),
    ("wallet", "FundingIntent"),
    ("wallet", "WemaProvisioningAttempt"),
    ("wallet", "Transaction"),
    ("wallet", "CurrencyWallet"),
    ("wallet", "Wallet"),
)

# These are environment-wide test/session records.  Their rows are not always
# linked to a User (WhatsApp onboarding and message logs are keyed by MSISDN), so
# a customer User cascade cannot remove them.  Order matters: Broadcast protects
# ApprovalRequest, therefore broadcasts must go first.
GLOBAL_MODELS = (
    ("whatsapp", "BroadcastRecipient"),
    ("whatsapp", "Broadcast"),
    ("whatsapp", "ApprovalRequest"),
    ("compliance", "Dispute"),
    ("compliance", "AmlCase"),
    ("compliance", "DataSubjectRequest"),
    ("whatsapp", "PendingAction"),
    ("whatsapp", "WhatsAppLink"),
    ("whatsapp", "WaOnboarding"),
    ("whatsapp", "WaMessageLog"),
    ("whatsapp", "ConversationState"),
    ("whatsapp", "WebhookEvent"),
    ("whatsapp", "AuditLog"),
    ("accounts", "AccessToken"),
    ("accounts", "OTP"),
    ("admin", "LogEntry"),
    ("sessions", "Session"),
)

# Seed/reference/config rows that must survive.  Counts are checked before the
# transaction commits, and every pre-existing SystemSetting key/value is checked
# exactly (apart from this command's own one-time receipt key).
REFERENCE_MODELS = (
    ("utility", "DataPlan"),
    ("utility", "CablePlan"),
    ("exams", "ExamProduct"),
    ("betting", "BettingPlatform"),
    ("transfers", "Bank"),
    ("auth", "Group"),
)


def _model(spec):
    return apps.get_model(*spec)


def _label(spec):
    return _model(spec)._meta.label


class Command(BaseCommand):
    help = "Irreversibly remove all customer/test data while preserving staff, config and catalogs."

    def add_arguments(self, parser):
        parser.add_argument("--confirm", default="")
        parser.add_argument("--nonce", default="")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        nonce = self._authorise(options)
        nonce_digest = hashlib.sha256(nonce.encode()).hexdigest()
        SystemSetting = apps.get_model("whatsapp", "SystemSetting")

        if SystemSetting.get(RECEIPT_KEY) == nonce_digest:
            self._clear_cache()
            self.stdout.write("CUSTOMER_RESET_ALREADY_COMPLETE")
            return

        User = get_user_model()
        customer_ids = list(
            User.objects.filter(is_staff=False, is_superuser=False)
            .order_by("pk").values_list("pk", flat=True)
        )
        before = self._scope_counts(customer_ids)
        self.stdout.write(
            "CUSTOMER_RESET_DRY_RUN " + json.dumps(before, sort_keys=True)
        )
        if options["dry_run"]:
            return

        deleted = defaultdict(int)
        with transaction.atomic():
            # Block concurrent customer creation/update while the destructive
            # transaction selects its final scope.  SQLite (unit tests/local)
            # serialises writes differently and does not support LOCK TABLE.
            if connection.vendor == "postgresql":
                table = connection.ops.quote_name(User._meta.db_table)
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"LOCK TABLE {table} IN SHARE ROW EXCLUSIVE MODE"
                    )

            staff_ids = list(
                User.objects.filter(is_staff=True).values_list("pk", flat=True)
            )
            superuser_ids = list(
                User.objects.filter(is_superuser=True).values_list("pk", flat=True)
            )
            customer_ids = list(
                User.objects.filter(is_staff=False, is_superuser=False)
                .order_by("pk").values_list("pk", flat=True)
            )
            reference_before = {
                _label(spec): _model(spec).objects.count()
                for spec in REFERENCE_MODELS
            }
            settings_before = dict(
                SystemSetting.objects.exclude(key=RECEIPT_KEY)
                .values_list("key", "value")
            )

            for spec in GLOBAL_MODELS:
                self._delete(_model(spec).objects.all(), deleted)
            for spec in CUSTOMER_MODELS:
                self._delete(
                    _model(spec).objects.filter(user_id__in=customer_ids), deleted
                )

            self._delete(User.objects.filter(pk__in=customer_ids), deleted)

            if User.objects.filter(is_staff=False, is_superuser=False).exists():
                raise CommandError("Customer reset verification failed: customer users remain")
            if set(staff_ids) - set(User.objects.filter(pk__in=staff_ids).values_list("pk", flat=True)):
                raise CommandError("Customer reset verification failed: a staff account was removed")
            if set(superuser_ids) - set(User.objects.filter(pk__in=superuser_ids).values_list("pk", flat=True)):
                raise CommandError("Customer reset verification failed: a superuser was removed")

            for spec in GLOBAL_MODELS:
                # AuditLog is still empty here; the non-PII reset receipt is
                # created only after every zero-count assertion passes.
                if _model(spec).objects.exists():
                    raise CommandError(
                        f"Customer reset verification failed: {_label(spec)} rows remain"
                    )
            for spec in CUSTOMER_MODELS:
                if _model(spec).objects.filter(user_id__in=customer_ids).exists():
                    raise CommandError(
                        f"Customer reset verification failed: {_label(spec)} customer rows remain"
                    )

            reference_after = {
                _label(spec): _model(spec).objects.count()
                for spec in REFERENCE_MODELS
            }
            if reference_after != reference_before:
                raise CommandError("Customer reset verification failed: reference data changed")
            settings_after = dict(
                SystemSetting.objects.exclude(key=RECEIPT_KEY)
                .values_list("key", "value")
            )
            if settings_after != settings_before:
                raise CommandError("Customer reset verification failed: platform settings changed")

            SystemSetting.objects.update_or_create(
                key=RECEIPT_KEY, defaults={"value": nonce_digest}
            )
            AuditLog = apps.get_model("whatsapp", "AuditLog")
            AuditLog.objects.create(
                actor_type="system",
                action="customer_data.reset",
                target="all_non_staff_customers",
                after={
                    "customers": len(customer_ids),
                    "rows": sum(deleted.values()),
                    "simulation_only": True,
                },
            )

        self._clear_cache()
        result = {
            "customers": len(customer_ids),
            "rows": sum(deleted.values()),
            "deleted_by_model": dict(sorted(deleted.items())),
            "staff_preserved": len(set(staff_ids) | set(superuser_ids)),
        }
        self.stdout.write(
            self.style.SUCCESS(
                "CUSTOMER_RESET_COMPLETE " + json.dumps(result, sort_keys=True)
            )
        )

    def _authorise(self, options):
        if options["confirm"] != CONFIRMATION:
            raise CommandError(f"Pass --confirm '{CONFIRMATION}' exactly")
        expected = os.environ.get("CUSTOMER_RESET_NONCE", "")
        supplied = options.get("nonce") or ""
        if len(expected) < 32 or not secrets.compare_digest(expected, supplied):
            raise CommandError("A matching 32+ character CUSTOMER_RESET_NONCE is required")
        if not bool(getattr(settings, "WEMA", {}).get("SIMULATION")):
            raise CommandError("Refusing customer reset unless WEMA_SIMULATION=true")
        if not bool(getattr(settings, "ALLOW_PRODUCTION_SIMULATION", False)):
            raise CommandError(
                "Refusing customer reset unless ALLOW_PRODUCTION_SIMULATION=true"
            )
        return supplied

    def _scope_counts(self, customer_ids):
        User = get_user_model()
        counts = {User._meta.label: len(customer_ids)}
        for spec in GLOBAL_MODELS:
            counts[_label(spec)] = _model(spec).objects.count()
        for spec in CUSTOMER_MODELS:
            counts[_label(spec)] = _model(spec).objects.filter(
                user_id__in=customer_ids
            ).count()
        return dict(sorted(counts.items()))

    @staticmethod
    def _delete(queryset, deleted):
        _total, details = queryset.delete()
        for model_label, count in details.items():
            deleted[model_label] += count

    @staticmethod
    def _clear_cache():
        try:
            cache.clear()
        except Exception as exc:  # noqa: BLE001 - reset is done; surface cache failure loudly
            raise CommandError(
                "Database reset completed, but clearing shared cache failed; "
                "redeploy with the same nonce to retry cache clearing"
            ) from exc
