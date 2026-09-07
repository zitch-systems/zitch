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
  * bank > ledger  — the NUBAN holds more than our books show. Usually benign and
    transient: a deposit that has landed at the bank but not yet been swept by
    ``reconcile_wema``, or a queued payout already deducted from our ledger. It
    self-heals on the next sweep, so it is reported but does not page by default.

Read-only; safe to run any time. It only does work when Wema is LIVE — in
simulation/mock ``get_balance`` returns 0.00, which would flag every funded wallet
as diverging, so it no-ops there instead of flooding false positives. Schedule it
less often than the funding sweep (e.g. hourly/daily). ``--fail-nonzero`` exits 1
on ANY divergence beyond ``--tolerance`` for cron/CI alerting.
"""
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand

from utility import wema
from wallet.services import wallet_expected_balance, wema_provisioned_wallets


class Command(BaseCommand):
    help = ("Reconcile each Wema-provisioned wallet's ledger balance against the real "
            "NUBAN balance at the bank; page when the ledger exceeds the bank.")

    def add_arguments(self, parser):
        parser.add_argument(
            "--tolerance", default="0.00",
            help="Naira delta to treat as noise, e.g. rounding (default: 0.00).")
        parser.add_argument(
            "--fail-nonzero", action="store_true",
            help="Exit 1 on ANY divergence beyond tolerance (over or under) — for CI/manual audit.")
        parser.add_argument(
            "--fail-over", action="store_true",
            help="Exit 1 ONLY on the dangerous ledger>bank direction. Use on the cron: a "
                 "benign bank>ledger delta (an unswept deposit between sweeps) won't create "
                 "a false cron failure, but a real float divergence still surfaces.")

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
        under = []  # bank > ledger  (usually benign / transient)
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

        # Page on the dangerous direction only (ledger exceeds bank — a possible
        # float leak or double-credit). The benign direction self-heals on the next
        # funding sweep, so it is logged/audited but not paged.
        if over:
            alert("reconcile_balances: ledger exceeds bank NUBAN balance (possible float leak "
                  "or double-credit)", level="error",
                  wallets=checked, over=len(over), sample=over[:10])
        # Total outage (every balance read failed) is its own signal.
        if checked and unreachable == checked:
            alert(f"reconcile_balances: all {checked} NUBAN balance reads failed — Wema "
                  f"unreachable or auth rejected", level="error", wallets=checked)

        self.stdout.write(
            f"Balance recon: {checked} wallet(s), {len(over)} over / {len(under)} under / "
            f"{unreachable} unreachable (tolerance {tolerance})")
        if (over and options["fail_over"]) or ((over or under) and options["fail_nonzero"]):
            raise SystemExit(1)
