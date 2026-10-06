"""Read-only release evidence: local checks never replace Wema acceptance."""
import json

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.exceptions import BadMigrationError, InconsistentMigrationHistory, NodeNotFoundError

from accounts.models import IdentityProof
from wallet.models import Wallet
from wema_vas.config import config, enrollment_release_policy, validate_configuration
from wema_vas.enrollment import _cutover_reference, _verified_proofs, enrollment_available
from wema_vas.identity import decrypt_identity
from wema_vas.models import MigrationApproval, Receipt, VirtualAccount


def sample_readiness(numbers, *, mode, prefix):
    """Return selected records and a PII-free evidence result; perform no writes."""
    rows = list(VirtualAccount.objects.select_related("user").filter(
        number__in=numbers, mode=mode, prefix=prefix, active=True, user__is_active=True,
    ).order_by("pk"))
    evidence_valid = len(rows) == len(set(numbers)) == len(numbers)
    for account in rows:
        try:
            account.clean()
            identity = decrypt_identity(account.encrypted_identity)
            proofs = _verified_proofs(account.user, identity["bvn"], identity["nin"])
            name = " ".join(proofs[0].verified_name.split())
            evidence_valid = evidence_valid and bool(
                account.verification_reference == ",".join(f"IdentityProof:{proof.pk}" for proof in proofs)
                and account.verified_at == max(proof.created for proof in proofs)
                and all(" ".join(proof.verified_name.split()).casefold() == name.casefold() for proof in proofs)
                and account.display_name == ("Zitch/" + name)[:160]
                and identity["phone"] == (account.user.phone or "").lstrip("+")
            )
            wallet = Wallet.objects.get(user_id=account.user_id)
            if mode == VirtualAccount.VALIDATION:
                # Bank-only test accounts must remain financially isolated.
                _cutover_reference(account.user, wallet, validation=True)
            elif wallet.account_number:
                evidence_valid = evidence_valid and bool(account.cutover_reference) and MigrationApproval.objects.filter(
                    user_id=account.user_id, legacy_account_number=wallet.account_number,
                    reference=account.cutover_reference,
                ).exists()
        except (ValidationError, ImproperlyConfigured, Wallet.DoesNotExist, KeyError, TypeError, ValueError):
            evidence_valid = False
    return rows, bool(evidence_valid)


def database_checks():
    """Inspect actual schema/migration records; never migrate or create tables."""
    result = {"postgresql": connection.vendor == "postgresql", "schema": False,
              "migrations": False, "immutable_evidence": False}
    with connection.cursor() as cursor:
        tables = set(connection.introspection.table_names(cursor))
        models = (VirtualAccount, Receipt, MigrationApproval, Wallet, IdentityProof)
        result["schema"] = all(model._meta.db_table in tables for model in models)
        if result["schema"]:
            for model in models:
                columns = {column.name for column in connection.introspection.get_table_description(cursor, model._meta.db_table)}
                if any(field.column not in columns for field in model._meta.local_fields):
                    result["schema"] = False
    loader = MigrationLoader(connection)
    required = set()
    for leaf in loader.graph.leaf_nodes():
        if leaf[0] in ("wema_vas", "wallet", "accounts"):
            required.update(loader.graph.forwards_plan(leaf))
    result["migrations"] = bool(required) and required.issubset(loader.applied_migrations)
    if result["postgresql"] and result["schema"]:
        expected = {("wema_vas_receipt", "wema_vas_receipt_immutable"),
                    ("wema_vas_migrationapproval", "wema_vas_migrationapproval_immutable"),
                    ("wema_vas_virtualaccount", "wema_vas_account_immutable"),
                    ("wallet_transaction", "wallet_transaction_immutable_guard")}
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT c.relname, t.tgname FROM pg_trigger t
                JOIN pg_class c ON c.oid = t.tgrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE NOT t.tgisinternal AND t.tgenabled IN ('O', 'A')
                    AND n.nspname = ANY (current_schemas(false))
            """)
            result["immutable_evidence"] = expected.issubset(set(cursor.fetchall()))
    return result


def build_report(stage="validation", accounts=None):
    """Return only redacted evidence; safe for CLI and authenticated diagnostics."""
    if stage not in ("validation", "controlled-live-pilot"):
        raise ValueError("Unsupported readiness stage")
    values = config()
    validation = stage == "validation"
    mode = VirtualAccount.VALIDATION if validation else VirtualAccount.LIVE
    checks = []

    def check(code, passed):
        checks.append({"code": code, "status": "pass" if passed else "fail"})

    valid_config = True
    try:
        validate_configuration()
    except (ImproperlyConfigured, TypeError, ValueError):
        valid_config = False
    check("bank_configuration", valid_config)
    check("bank_endpoints_enabled", values.get("ENABLED") is True)
    check("requested_mode", values.get("MODE") == mode)
    check("https_required", values.get("REQUIRE_HTTPS", True) is True)

    try:
        db = database_checks()
    except (DatabaseError, ImproperlyConfigured, ValueError, LookupError,
            BadMigrationError, InconsistentMigrationHistory, NodeNotFoundError):
        db = {"postgresql": False, "schema": False, "migrations": False, "immutable_evidence": False}
    for code, passed in db.items():
        check("database_" + code, passed)

    policy = enrollment_release_policy(values)
    if not validation:
        check("controlled_pilot_policy", policy["phase"] == "pilot" and not policy["errors"])
        check("pilot_allowlist_present", bool(policy["pilot_user_ids"]))

    numbers = list(accounts or [])
    selected_count, valid_samples, enrollment_gate = 0, False, False
    if db["schema"] and valid_config:
        try:
            if not numbers:
                candidates = VirtualAccount.objects.filter(mode=mode, prefix=values.get("PREFIX"),
                    active=True, user__is_active=True).order_by("pk")
                if not validation:
                    candidates = candidates.filter(user_id__in=policy["pilot_user_ids"])
                numbers = list(candidates.values_list("number", flat=True)[:3 if validation else 1])
            cardinality = len(numbers) == 3 if validation else 1 <= len(numbers) <= 10
            if len(numbers) <= 10 and len(set(numbers)) == len(numbers):
                rows, valid_samples = sample_readiness(numbers, mode=mode, prefix=values.get("PREFIX"))
                selected_count = len(rows)
                valid_samples = cardinality and valid_samples
                enrollment_gate = bool(rows) and all(enrollment_available(account.user) for account in rows)
        except (DatabaseError, ImproperlyConfigured, TypeError, ValueError):
            valid_samples = False
    check("active_verified_samples", valid_samples)
    if not validation:
        check("selected_pilot_users_allowed", enrollment_gate)
    local_ready = all(item["status"] == "pass" for item in checks)
    external = ["bank_endpoint_validation", "bank_inflow_and_collection_settlement_confirmation"]
    if not validation:
        external.extend(["payout_and_outward_tsq_acceptance", "biller_continuity_confirmation",
                         "settlement_and_commercial_terms", "bank_customer_migration_signoff"])
    report = {
        "stage": stage, "read_only": True, "local_ready": local_ready,
        "status": ("blocked_local_requirements" if not local_ready else
                   "ready_for_bank_validation" if validation else "bank_verification_pending"),
        "sample_count": selected_count, "checks": checks,
        "bank_evidence": [{"code": item, "status": "pending_external_verification"} for item in external],
        "full_go_live_ready": False,
        "scope": "Local inspection only; bank acceptance, settlement and production approval were not verified.",
    }
    return report


class Command(BaseCommand):
    help = "Print redacted, read-only VAS validation/pilot readiness. Never authorizes public go-live."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--stage", choices=("validation", "controlled-live-pilot"), default="validation")
        parser.add_argument("--account", action="append", default=[],
                            help="Optional existing sample account; values are never echoed in the report.")

    def handle(self, *args, **options):
        report = build_report(stage=options["stage"], accounts=options["account"])
        self.stdout.write(json.dumps(report, indent=2, sort_keys=True))
        if not report["local_ready"] or options["stage"] != "validation":
            raise CommandError("VAS preflight is blocked; use the redacted JSON report for required evidence.")
