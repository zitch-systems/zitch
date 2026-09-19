"""Ledger <-> BANK (Wema NUBAN) balance reconciliation — the EXTERNAL integrity check.

``integrity_check`` compares each wallet's stored balance against its own
append-only ledger (internal vs internal). This command goes one step further and
compares the ledger against what the BANK actually holds in the user's Wema NUBAN
(internal vs external) — the check that catches float leaks, double-credits, and
any drift between our books and the bank that a purely-internal check cannot see.

Direction matters, so the two are reported separately:

  * ledger > bank  — we think the user has MORE than their NUBAN holds. The
    dangerous direction (a double-credit, or a debit that never reached the bank).
    This PAGES via Sentry.
  * bank > ledger  — the NUBAN holds more than our books show. It can be a
    transient deposit between sweeps, but a six-hour reconciliation is too late
    to dismiss as harmless. It is escalated for operator review (rate-limited by
    the exact discrepancy), without ever changing a customer balance.

Read-only; safe to run any time. It only does work when Wema is LIVE — in
simulation/mock ``get_balance`` returns 0.00, which would flag every funded wallet
as diverging, so it no-ops there instead of flooding false positives. Schedule it
less often than the funding sweep (e.g. hourly/daily). ``--fail-nonzero`` exits 1
on ANY divergence beyond ``--tolerance`` or incomplete bank reads for cron/CI alerting.
"""
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand

from utility import wema
from wallet.services import wallet_expected_balance, wema_provisioned_wallets


class Command(BaseCommand):
    help = ("Reconcile each Wema-provisioned wallet's ledger balance against the real "
            "NUBAN balance at the bank; escalate any non-zero divergence without "
            "changing money or payment state.")

    def add_arguments(self, parser):
        parser.add_argument(
            "--tolerance", default="0.00",
            help="Naira delta to treat as noise, e.g. rounding (default: 0.00).")
        parser.add_argument(
            "--fail-nonzero", action="store_true",
            help="Exit 1 on ANY divergence beyond tolerance (over or under), or an incomplete "
                 "bank read — for CI/manual audit.")
        parser.add_argument(
            "--fail-over", action="store_true",
            help="Exit 1 on the dangerous ledger>bank direction or an incomplete bank read. "
                 "Bank>ledger discrepancies are still escalated for review but do not by "
                 "themselves select a customer balance or payment outcome.")

    def handle(self, *args, **options):
        from utility.alerts import alert

        try:
            tolerance = Decimal(options["tolerance"])
        except (InvalidOperation, TypeError):
            tolerance = Decimal("0.00")

        # Only meaningful against live NUBANs. In simulation/mock, get_balance
        # returns 0.00 for everyone, which would mark every funded wallet as
        # diverging — so no-op with a clear message instead of paging noise.
        if not wema.wema_live():
            self.stdout.write("reconcile_balances: Wema not live (simulation/mock) — no real "
                              "NUBAN balances to compare. Skipping.")
            return

        checked = 0
        unreachable = 0
        over = []   # ledger > bank  (dangerous — float risk)
        under = []  # bank > ledger  (operator review; no automatic correction)
        for w in wema_provisioned_wallets():
            checked += 1
            res = wema.get_balance(w.account_number)
            bank = res.get("balance_naira") if res.get("success") else None
            if bank is None:
                unreachable += 1
                continue
            ledger = wallet_expected_balance(w.user_id)
            delta = ledger - bank  # +ve => ledger exceeds bank
            row = {"user": w.user_id, "ledger": str(ledger), "bank": str(bank), "delta": str(delta)}
            if delta > tolerance:
                over.append(row)
            elif -delta > tolerance:
                under.append(row)

        from whatsapp.ops import record_audit
        record_audit("recon.balance_check", actor_type="system",
                     after={"wallets": checked, "unreachable": unreachable,
                            "ledger_over_bank": len(over), "bank_over_ledger": len(under),
                            "over_sample": over[:25], "under_sample": under[:25]})

        for row in over:
            self.stderr.write(f"OVER  user={row['user']} ledger={row['ledger']} "
                              f"bank={row['bank']} delta=+{row['delta']}")
        for row in under:
            self.stdout.write(f"under user={row['user']} ledger={row['ledger']} "
                              f"bank={row['bank']} delta={row['delta']}")

        # Ledger exceeding bank is an immediate float risk. Bank exceeding
        # ledger is not safe to ignore either once it survives to this
        # six-hour job: it can be a missed funding credit or other provenance
        # break. Both incidents only alert; neither branch changes money,
        # refunds, or resolves a pending transaction.
        if over:
            alert("reconcile_balances: ledger exceeds bank NUBAN balance (possible float leak "
                  "or double-credit)", level="error",
                  wallets=checked, over=len(over), sample=over[:10])
        if under:
            from utility.reconciliation import alert_due
            fingerprint = [
                f"{row['user']}:{row['ledger']}:{row['bank']}"
                for row in under
            ]
            if alert_due("balance-bank-over-ledger", fingerprint):
                alert("reconcile_balances: bank NUBAN balance exceeds ledger; "
                      "operator provenance review required and no correction was applied",
                      level="error", wallets=checked, under=len(under), sample=under[:10])
        # Total outage (every balance read failed) is its own signal.
        if checked and unreachable == checked:
            alert(f"reconcile_balances: all {checked} NUBAN balance reads failed — Wema "
                  f"unreachable or auth rejected", level="error", wallets=checked)

        self.stdout.write(
            f"Balance recon: {checked} wallet(s), {len(over)} over / {len(under)} under / "
            f"{unreachable} unreachable (tolerance {tolerance})")
        incomplete = unreachable > 0
        if ((incomplete or over) and options["fail_over"]
                or (incomplete or over or under) and options["fail_nonzero"]):
            raise SystemExit(1)
