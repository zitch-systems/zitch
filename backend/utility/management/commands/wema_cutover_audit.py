"""Read-only preflight of a 9PSB-era database before restoring Wema Partnership.

Intentionally does not open accounts, copy account numbers, relabel balances,
clear pending payments, send bank requests or mutate customer records.
Run on the current PostgreSQL schema before approving a cutover.
"""
import json

from django.core.management.base import BaseCommand, CommandError
from django.db import connection


def evaluate_cutover(counts):
    """Conservative blockers; absence of a blocker is not Wema bank approval."""
    blockers = []
    if counts.get("banking_migrations", 0):
        blockers.append("9psb_era_migrations_need_forward_compatibility")
    if counts.get("bank_accounts", 0):
        blockers.append("9psb_accounts_require_bank_side_closeout_and_reconciliation")
    if counts.get("archived_bank_wallets", 0):
        blockers.append("previous_wema_accounts_require_bank_confirmed_ownership")
    if counts.get("archived_bank_reviews", 0):
        blockers.append("historical_bank_balances_or_pending_items_require_review")
    if counts.get("pending_transfers", 0):
        blockers.append("pending_wallet_transfers_require_provider_outcome_review")
    if counts.get("unsettled_funding", 0):
        blockers.append("unsettled_funding_requires_attributable_bank_evidence")
    if counts.get("archived_bank_wallets", 0) and not counts.get("active_wema_wallets", 0):
        blockers.append("wallet_account_numbers_were_detached_during_previous_cutover")
    return blockers


class Command(BaseCommand):
    help = "Read-only Wema Partnership return preflight; fail closed on unresolved 9PSB-era bank state."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json",
                            help="Print aggregate-only JSON (no customer account numbers or credentials).")

    def handle(self, *args, **options):
        tables = set(connection.introspection.table_names())
        counts = {}
        # Never infer bank state from Django models in this restored code tree:
        # the newer banking models/migrations are intentionally not imported.
        with connection.cursor() as cur:
            if "django_migrations" in tables:
                cur.execute("SELECT COUNT(*) FROM django_migrations WHERE app = %s", ["banking"])
                counts["banking_migrations"] = int(cur.fetchone()[0])
            else:
                counts["banking_migrations"] = 0

            def count(table, where="1=1"):
                if table not in tables:
                    return 0
                # All table names and predicates below are fixed developer literals.
                cur.execute(f'SELECT COUNT(*) FROM "{table}" WHERE {where}')
                return int(cur.fetchone()[0])

            counts["bank_accounts"] = count("banking_bankaccount")
            counts["archived_bank_wallets"] = count(
                "banking_previousbankarchive", "account_number IS NOT NULL AND account_number <> ''")
            counts["archived_bank_reviews"] = count(
                "banking_previousbankarchive", "review_required = TRUE")
            counts["pending_transfers"] = count(
                "wallet_transaction",
                "direction = 'out' AND transaction_status = 'Pending'")
            counts["active_wema_wallets"] = count(
                "wallet_wallet", "account_number IS NOT NULL AND account_number <> ''")
            counts["unsettled_funding"] = count(
                "wallet_unsettledfunding",
                ("NOT EXISTS (SELECT 1 FROM wallet_retiredfundingarchive a "
                 "WHERE a.funding_id = wallet_unsettledfunding.id)")
                if "wallet_retiredfundingarchive" in tables else "1=1")

        blockers = evaluate_cutover(counts)
        report = {
            "provider_target": "wema_partnership",
            "ready_for_cutover": False,  # A database query cannot prove live bank UAT.
            "database_review_blocked": bool(blockers),
            "aggregate_counts": counts,
            "blockers": blockers,
            "note": "Bank confirmation, forward migrations, ledger review and channel UAT are required even if counts are zero.",
        }
        self.stdout.write(json.dumps(report, indent=2) if options["as_json"] else (
            f"Wema Partnership cutover preflight: {len(blockers)} blockers; "
            f"bank UAT still required.\\n" +
            "\\n".join(f"- {item}" for item in blockers) +
            f"\\nAggregate counts: {counts}"
        ))
        if blockers:
            raise CommandError("Wema Partnership cutover is not cleared; preserve all financial evidence.")
