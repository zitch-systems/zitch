"""Reconcile VTU / betting / exam purchases left PENDING by a provider timeout.

When a purchase send times out the outcome is unknown, so the ledger row is held
PENDING (money still debited, flagged ``meta.reconcile``) instead of refunded —
refunding a purchase that actually went through would leak money. This command
requeries each such transaction by its reference (the provider's idempotency key)
and settles it: marked Successful if delivered, or refunded if it definitively
failed. Rows still unknown stay PENDING for the next run. Schedule it every few
minutes (see render.yaml).
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from utility.providers import vtu_requery
from wallet.services import pending_vtu_purchases, settle_or_refund


class Command(BaseCommand):
    help = "Requery and settle VTU purchases stuck PENDING after a provider timeout."

    def add_arguments(self, parser):
        parser.add_argument(
            "--older-than-minutes", type=int, default=5,
            help="Only reconcile transactions at least this old (default: 5).",
        )

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(minutes=options["older_than_minutes"])
        # VTU.ng purchases only — bank-transfer payouts share the PENDING+reconcile
        # shape but are settled by the Kora payout webhook, never a VTU
        # requery (which would hit the wrong provider for a foreign reference).
        pending = pending_vtu_purchases(cutoff)
        total = pending.count()
        settled = 0
        for txn in pending:
            if settle_or_refund(txn, vtu_requery(txn.reference)) != "pending":
                settled += 1
        from whatsapp.ops import record_audit
        record_audit("recon.vtu_run", actor_type="system",
                     after={"checked": total, "settled": settled})
        self.stdout.write(f"Reconciled {settled} of {total} pending VTU transaction(s)")
