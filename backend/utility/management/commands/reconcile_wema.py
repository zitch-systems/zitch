"""Reconcile inbound Wema/ALAT wallet funding — credit deposits that have no webhook.

ALAT exposes NO inbound-credit webhook, so a bank transfer into a user's Wema
NUBAN is invisible to us until we poll. This command sweeps each Wema-provisioned
wallet's transaction history over a recent window and credits every inbound
(``creditType == "Credit"``) deposit to the ledger — idempotently keyed on Wema's
per-transaction ``referenceId`` (the ledger's unique reference), so re-polling the
same window never double-credits. Rows already applied are skipped. Schedule it
every few minutes (see render.yaml); it only does work when Wema is configured.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from utility import wema
from wallet.services import apply_wema_credit, wema_provisioned_wallets


class Command(BaseCommand):
    help = "Poll Wema wallet accounts and credit inbound deposits (ALAT has no funding webhook)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--lookback-days", type=int, default=2,
            help="Days of history to scan per wallet (default: 2). Idempotent, so overlap is safe.",
        )

    def handle(self, *args, **options):
        today = timezone.now().date()
        date_to = today.strftime("%Y-%m-%d")
        date_from = (today - timedelta(days=max(0, options["lookback_days"]))).strftime("%Y-%m-%d")

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

        from whatsapp.ops import record_audit
        record_audit("recon.wema_run", actor_type="system",
                     after={"wallets": scanned, "credited": credited})
        self.stdout.write(f"Wema reconcile: {credited} new credit(s) across {scanned} wallet(s)")
