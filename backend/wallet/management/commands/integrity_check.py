"""Ledger <-> balance reconciliation — the core financial-integrity guarantee.

For every wallet, recompute the expected balance from the append-only ledger and
compare it to the stored balance. The ledger state machine implies:

    expected = sum(IN, Successful) - sum(OUT, Pending or Successful)

(debits deduct at PENDING; a FAILED debit was refunded back; credits are only
ever written Successful). Any mismatch means a money bug or tampering — it is
reported, and recorded to the immutable AuditLog so drift is never silent.

Read-only by default (safe to run any time; schedule daily). --fail-nonzero makes
mismatches exit(1) for CI/cron alerting. This is the "reconciliation engine +
balance snapshot" control from the hardening plan, kept additive: no schema
change, the ledger itself is the source of truth.
"""
from django.core.management.base import BaseCommand

from wallet.models import Wallet
from wallet.services import wallet_expected_balance  # single source of truth


class Command(BaseCommand):
    help = "Reconcile every wallet balance against its ledger; report (and audit-log) mismatches."

    def add_arguments(self, parser):
        parser.add_argument("--fail-nonzero", action="store_true",
                            help="Exit 1 if any mismatch is found (for cron/CI alerting).")

    def handle(self, *args, **options):
        checked = 0
        mismatches = []
        for w in Wallet.objects.all().only("id", "user_id", "balance"):
            checked += 1
            expected = wallet_expected_balance(w.user_id)
            if expected != w.balance:
                mismatches.append((w.user_id, str(w.balance), str(expected)))

        from whatsapp.ops import record_audit
        record_audit("recon.integrity_check", actor_type="system",
                     after={"wallets": checked, "mismatches": len(mismatches),
                            "detail": [{"user": u, "balance": b, "ledger": e}
                                       for u, b, e in mismatches[:50]]})

        for user_id, bal, exp in mismatches:
            self.stderr.write(f"MISMATCH user={user_id} balance={bal} ledger={exp}")
        # A ledger/balance mismatch is a money bug or tampering — page it, don't
        # let it sit silently in stderr + the audit log until someone looks.
        if mismatches:
            from utility.alerts import alert
            alert("integrity_check: wallet balance diverges from ledger", level="error",
                  wallets=checked, mismatches=len(mismatches),
                  sample=[{"user": u, "balance": b, "ledger": e} for u, b, e in mismatches[:10]])
        self.stdout.write(f"Integrity: {checked} wallet(s), {len(mismatches)} mismatch(es)")
        if mismatches and options["fail_nonzero"]:
            raise SystemExit(1)
