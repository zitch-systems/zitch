"""Reconcile Kora bank payouts that never got a settlement webhook.

Kora payouts normally settle via the /api/transfers/webhook/ callback
(transfer.success / transfer.failed). If a webhook is ever missed (delivery
failure, downtime, a dashboard misconfig), a transfer returned PENDING on send
would otherwise sit debited forever. This polls Kora's single-transaction status
for each PENDING bank payout and settles (success) or reverses (failed/reversed)
it — a safety net behind the webhook, mirroring the Wema poller.

Only runs when Kora is the payout rail, so it never queries Kora for a Wema payout
it never saw. Idempotent: settle_payout / reverse_transfer are status-guarded, so
overlapping runs (or a webhook that lands first) never double-act. Anything not a
definitive terminal status — still processing, unknown, or the query itself
unreachable — is left PENDING for the next run, so a transient blip never wrongly
reverses money. Schedule every few minutes (see render.yaml).
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from utility import kora
from utility.providers import payout_provider
from wallet.services import pending_bank_payouts, reverse_transfer, settle_payout

# Terminal FAILURE statuses from verify_payout (already lowercased). SUCCESS is
# handled via the `success` flag; `pending` is handled via its own flag. Any status
# not listed here (or an unreachable query) leaves the row PENDING for the next run.
_REVERSED = {"failed", "reversed", "expired", "cancelled", "declined", "rejected", "returned"}


class Command(BaseCommand):
    help = "Poll Kora for PENDING bank-payout settlement (a safety net behind the webhook)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--older-than-minutes", type=int, default=5,
            help="Only settle payouts at least this old (default: 5), giving the webhook first crack.",
        )

    def handle(self, *args, **options):
        checked = settled = reversed_ = 0
        # Only when Kora is the payout rail — never query Kora for a Wema payout.
        if payout_provider() == "kora":
            cutoff = timezone.now() - timedelta(minutes=max(0, options["older_than_minutes"]))
            for txn in pending_bank_payouts(cutoff):
                checked += 1
                res = kora.verify_payout(txn.reference)
                if res.get("success"):
                    if settle_payout(txn.reference) is not None:
                        settled += 1
                elif res.get("pending"):
                    continue  # still in flight — leave PENDING
                elif (res.get("status") or "").lower() in _REVERSED:
                    if reverse_transfer(txn.reference) is not None:
                        reversed_ += 1
                # anything else (unreachable / unknown status): leave PENDING

        from whatsapp.ops import record_audit
        record_audit("recon.kora_payouts_run", actor_type="system",
                     after={"checked": checked, "settled": settled, "reversed": reversed_})
        self.stdout.write(
            f"Kora payout reconcile: checked {checked}, settled {settled}, reversed {reversed_}")
