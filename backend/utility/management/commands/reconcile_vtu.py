"""Reconcile VTU / betting / exam purchases left PENDING by a provider timeout.

When a purchase send times out the outcome is unknown, so the ledger row is held
PENDING (money still debited, flagged ``meta.reconcile``) instead of refunded â€”
refunding a purchase that actually went through would leak money. This command
requeries each such transaction by its reference (the provider's idempotency key)
and settles it: marked Successful if delivered, or refunded if it definitively
failed. Rows still unknown stay PENDING for the next run. Schedule it every few
minutes (see render.yaml).
"""
from datetime import timedelta

from django.conf import settings
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
        # VTU.ng purchases only â€” bank-transfer payouts share the PENDING+reconcile
        # shape but are settled by the reconcile_wema poller, never a VTU
        # requery (which would hit the wrong provider for a foreign reference).
        pending = pending_vtu_purchases(cutoff)
        total = pending.count()
        settled = 0
        for txn in pending:
            if settle_or_refund(txn, vtu_requery(txn.reference)) != "pending":
                settled += 1
        # A purchase stuck PENDING is invisible to every other control, exactly
        # as a stuck bank payout is (see reconcile_wema): it is a pending DEBIT,
        # so integrity_check counts it as owed and nothing anywhere says "this
        # customer paid for airtime that never arrived and was never refunded".
        # Holding an unknown outcome PENDING is right in the minutes after a
        # timeout and wrong after hours - by then this sweep has requeried dozens
        # of times and will not resolve it on its own, and it will keep looping
        # in silence until the customer complains.
        stuck_after = timedelta(
            hours=int(getattr(settings, "VTU_PURCHASE_STUCK_HOURS", 2) or 2))
        stuck = list(pending_vtu_purchases(timezone.now() - stuck_after)[:50])
        if stuck:
            from utility.alerts import alert

            alert(f"reconcile_vtu: {len(stuck)} purchase(s) still PENDING after "
                  f"{stuck_after} - the customer is debited and the service was "
                  f"neither delivered nor refunded; no other control reports this",
                  level="error", purchases=len(stuck),
                  references=[t.reference for t in stuck[:10]])

        # A provider that cannot answer after many retries must not hold a
        # customer's money forever. This is deliberately later than the alert
        # threshold above, and each row is re-queried immediately before reversal.
        # The transaction lock in settle_or_refund makes the refund idempotent.
        auto_reverse_hours = int(
            getattr(settings, "VTU_PURCHASE_AUTO_REVERSE_HOURS", 6) or 6)
        auto_reversed = 0
        if auto_reverse_hours > 0:
            stale = list(pending_vtu_purchases(
                timezone.now() - timedelta(hours=auto_reverse_hours))[:50])
            for txn in stale:
                check = vtu_requery(txn.reference)
                if check.get("pending"):
                    check = {
                        "success": False,
                        "message": "The service provider could not confirm this purchase in time.",
                        "status": "unconfirmed_timeout",
                    }
                if settle_or_refund(txn, check) == "failed":
                    auto_reversed += 1
            if auto_reversed:
                from utility.alerts import alert
                alert(
                    f"reconcile_vtu: automatically refunded {auto_reversed} unresolved purchase(s) "
                    f"after {auto_reverse_hours}h",
                    level="warning", purchases=auto_reversed,
                    references=[txn.reference for txn in stale[:10]],
                )

        from whatsapp.ops import record_audit
        record_audit("recon.vtu_run", actor_type="system",
                     after={"checked": total, "settled": settled,
                            "auto_reversed": auto_reversed})
        self.stdout.write(f"Reconciled {settled} of {total} pending VTU transaction(s)")
