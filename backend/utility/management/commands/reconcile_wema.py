"""Reconcile Wema/ALAT money movement that has no webhook — inbound funding AND
outbound payout settlement.

ALAT exposes NO webhooks, so two things must be polled:

1. FUNDING (credits): a bank transfer into a user's Wema NUBAN is invisible until
   we poll. This sweeps each Wema-provisioned wallet's transaction history over a
   recent window and credits every inbound (``creditType == "Credit"``) deposit —
   idempotent on Wema's ``referenceId`` (stored under a ``WEMA-CR-`` ledger key), so
   re-polling the same window never double-credits.

2. PAYOUTS (settlement): a Wema transfer returned PENDING/PROCESSING has no
   disbursement webhook to settle it (unlike Kora). This polls
   confirm_transfer_status for each PENDING bank payout and settles (SUCCESS) or
   reverses (FAILED) it — the equivalent of the Kora payout webhook. Only runs when
   Wema is the payout rail, so it never touches a Kora payout it can't query.

Schedule every few minutes (see render.yaml); each phase only does work when Wema
is the relevant rail, so it's harmless otherwise.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from utility import wema
from utility.providers import payout_provider
from wallet.services import (
    apply_wema_credit, pending_bank_payouts, reverse_transfer, settle_payout,
    wema_provisioned_wallets,
)

# Terminal statuses from confirm_transfer_status (the ALAT legend is not published,
# so match defensively — anything else leaves the row PENDING for the next run).
_SETTLED = {"SUCCESS", "SUCCESSFUL", "COMPLETED", "PAID", "APPROVED"}
_REVERSED = {"FAILED", "REVERSED", "DECLINED", "CANCELLED", "REJECTED", "RETURNED"}


class Command(BaseCommand):
    help = "Poll Wema for inbound deposits (credit) and PENDING payout settlement (no webhooks)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--lookback-days", type=int, default=2,
            help="Days of history to scan per wallet (default: 2). Idempotent, so overlap is safe.",
        )
        parser.add_argument(
            "--payout-older-than-minutes", type=int, default=2,
            help="Only settle payouts at least this old (default: 2).",
        )

    def handle(self, *args, **options):
        today = timezone.now().date()
        date_to = today.strftime("%Y-%m-%d")
        date_from = (today - timedelta(days=max(0, options["lookback_days"]))).strftime("%Y-%m-%d")

        # Phase 1 — inbound funding credits.
        scanned = 0
        credited = 0
        for wallet in wema_provisioned_wallets():
            scanned += 1
            res = wema.get_transactions(wallet.account_number, date_from, date_to)
            if not res.get("success"):
                continue
            for tx in res.get("transactions", []) or []:
                if apply_wema_credit(wallet, tx) is not None:
                    credited += 1

        # Phase 2 — settle PENDING payouts (only when Wema is the payout rail, so we
        # never query Wema for a Kora payout it never saw).
        settled = 0
        reversed_ = 0
        if payout_provider() == "wema":
            cutoff = timezone.now() - timedelta(minutes=max(0, options["payout_older_than_minutes"]))
            for txn in pending_bank_payouts(cutoff):
                st = wema.confirm_transfer_status(txn.reference)
                if not st.get("success"):
                    continue  # query unreachable / unknown — leave PENDING for next run
                status = (st.get("status") or "").upper()
                if status in _SETTLED:
                    if settle_payout(txn.reference) is not None:
                        settled += 1
                elif status in _REVERSED:
                    if reverse_transfer(txn.reference) is not None:
                        reversed_ += 1
                # anything else: still in flight — leave PENDING

        from whatsapp.ops import record_audit
        record_audit("recon.wema_run", actor_type="system",
                     after={"wallets": scanned, "credited": credited,
                            "payouts_settled": settled, "payouts_reversed": reversed_})
        self.stdout.write(
            f"Wema reconcile: {credited} credit(s) / {scanned} wallet(s); "
            f"payouts settled {settled}, reversed {reversed_}")
