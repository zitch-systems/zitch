"""Daily settlement statement — the WHOLE float position, not per-wallet drift.

This is the third and outermost of the three reconciliation checks, and the one the
25 July audit recorded as missing ("prove ledger-to-bank settlement … as a two-rail
accounting system"). The other two answer narrower questions:

  * ``integrity_check``      stored wallet balance vs that wallet's own ledger
                             (internal vs internal, per wallet)
  * ``reconcile_balances``   a wallet's ledger vs that wallet's NUBAN at the bank
                             (internal vs external, per wallet)
  * ``settlement_report``    everything we OWE vs everything we HOLD, in total,
                             across every asset rail — including the two places
                             per-wallet checks cannot look: the pool account and
                             the VTU provider wallet.

Why per-wallet checks cannot see the position
---------------------------------------------
Every per-wallet comparison can pass while the business is short, because not all
customer money sits in customer NUBANs:

  1. A customer funds ₦1,000 -> their NUBAN holds ₦1,000, we owe ₦1,000. Balanced.
  2. They buy ₦1,000 of airtime -> we owe ₦0, and VTU.ng debits OUR provider
     wallet by ₦1,000. Their NUBAN still holds ₦1,000.

After step 2 that wallet reconciles perfectly against its NUBAN (0 vs ... no: the
NUBAN holds 1,000 while the ledger says 0, which ``reconcile_balances`` classifies
as the benign "bank > ledger" direction and does not page). Meanwhile the provider
wallet is ₦1,000 down and nothing anywhere says that ₦1,000 must be swept from the
NUBAN to VTU.ng. Repeat a few thousand times and the VAS rail runs dry mid-day with
a bank account full of money — an outage with the funds sitting right there.

So the identity this command reports is:

    held (bank NUBANs + pool + VTU provider wallet)  -  owed (ledger liability)
    = position

  * position < 0  SHORTFALL. We owe customers more than we hold anywhere. This is
    a solvency signal and it pages, always.
  * position > 0  surplus. Expected, and it GROWS with VAS volume: it is the sweep
    backlog plus margin. Reported every run so the number is watched, and paged
    only when it crosses ``--max-surplus`` (an unexplained surplus is also a bug —
    e.g. debits that never reached a provider).

Read-only: it takes no money decisions and writes nothing but an AuditLog row.
Runs from a cron daily; see docs/settlement-operating-model.md for thresholds and
who owns the response.
"""
from decimal import Decimal, InvalidOperation

from django.conf import settings as dj_settings
from django.core.management.base import BaseCommand
from django.db.models import Sum
from django.utils import timezone

from utility import vtung, wema
from wallet.models import Transaction, Wallet
from wallet.services import is_bank_payout, wema_provisioned_wallets

# The label prefixes that identify which rail a day's movement went out on. These
# mirror common.http's daily-cap buckets; kept separate because this command
# classifies by ASSET rail (which pot the money left) rather than by spend cap.
_VAS_PREFIXES = ("Airtime", "Data", "Cable", "Electricity", "Betting", "Exam")


def _sum(qs) -> Decimal:
    return qs.aggregate(s=Sum("amount"))["s"] or Decimal("0")


class Command(BaseCommand):
    help = ("Daily settlement statement: total customer liability against total funds "
            "held across the bank NUBANs, the pool account and the VTU provider wallet.")

    def add_arguments(self, parser):
        parser.add_argument(
            "--max-shortfall", default="0.00",
            help="Naira of shortfall tolerated before paging (default 0.00 — any "
                 "shortfall pages). Raise only with a documented reason.")
        parser.add_argument(
            "--max-surplus", default="",
            help="Naira of surplus above which to page. Blank (default) never pages on "
                 "surplus. Set this once the normal sweep backlog is known, so an "
                 "abnormal surplus — debits that never reached a provider — surfaces.")
        parser.add_argument(
            "--fail-on-breach", action="store_true",
            help="Exit 1 when a threshold is breached, so the cron itself goes red.")

    # --- the two sides of the statement ------------------------------------

    def _owed(self) -> dict:
        """What we owe customers, from the append-only ledger.

        Aggregated in SQL over the whole ledger rather than by summing
        ``wallet_expected_balance`` per wallet: the per-wallet helper is the right
        tool for per-wallet drift, but N queries over every wallet is the wrong
        shape for a total, and the total must include users whose Wallet row and
        ledger disagree (which is exactly what integrity_check is for).
        """
        credits = _sum(Transaction.objects.filter(
            direction=Transaction.IN, transaction_status=Transaction.SUCCESS,
            currency="NGN"))
        debits = _sum(Transaction.objects.filter(
            direction=Transaction.OUT, currency="NGN",
            transaction_status__in=(Transaction.PENDING, Transaction.SUCCESS)))
        stored = (Wallet.objects.aggregate(s=Sum("balance"))["s"] or Decimal("0"))
        return {"ledger_liability": credits - debits, "stored_liability": stored}

    def _held(self) -> dict:
        """What we hold, per asset rail. Unreadable rails are reported as None and
        excluded from the total, and the total is then marked incomplete — a
        settlement position computed over a rail we could not read is worse than no
        position at all, because it looks authoritative."""
        nuban_total = Decimal("0")
        wallets = unreachable = 0
        for w in wema_provisioned_wallets():
            wallets += 1
            res = wema.get_balance(w.account_number)
            bank = res.get("balance_naira") if res.get("success") else None
            if bank is None:
                unreachable += 1
                continue
            nuban_total += bank

        pool_account = str(dj_settings.WEMA.get("SOURCE_ACCOUNT") or "").strip()
        pool = None
        if pool_account:
            res = wema.get_balance(pool_account)
            pool = res.get("balance_naira") if res.get("success") else None

        provider = vtung.provider_wallet_balance()

        return {"nuban_total": nuban_total, "nuban_wallets": wallets,
                "nuban_unreachable": unreachable,
                "pool_account_set": bool(pool_account), "pool_balance": pool,
                "provider_wallet": provider}

    def _day_movement(self) -> dict:
        """Today's outflow split by the asset rail it actually left from — the
        explanation for a change in the position, and the number a sweep is sized
        against."""
        start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
        today = Transaction.objects.filter(created__gte=start, currency="NGN").exclude(
            transaction_status=Transaction.FAILED)

        funded_in = _sum(today.filter(direction=Transaction.IN,
                                      transaction_status=Transaction.SUCCESS))
        out_rows = today.filter(direction=Transaction.OUT).only("amount", "meta", "service")
        # A bank payout leaves the NUBAN/pool; VAS spend leaves the provider wallet;
        # an internal transfer leaves neither (it moves liability between two of our
        # own users and nets to zero at every bank).
        bank_out = Decimal("0")
        vas_out = Decimal("0")
        internal_out = Decimal("0")
        other_out = Decimal("0")
        for r in out_rows:
            if is_bank_payout(r):
                bank_out += r.amount
            elif str(r.service or "").startswith(_VAS_PREFIXES):
                vas_out += r.amount
            elif str(r.service or "").startswith("Transfer to"):
                internal_out += r.amount
            else:
                other_out += r.amount
        return {"funded_in": funded_in, "bank_payouts_out": bank_out,
                "vas_out": vas_out, "internal_transfers_out": internal_out,
                "other_out": other_out,
                # What must move from the bank to VTU.ng to keep the VAS rail funded.
                "sweep_owed_to_provider": vas_out}

    # --- run ---------------------------------------------------------------

    def handle(self, *args, **options):
        from utility.alerts import alert
        from whatsapp.ops import record_audit

        def _money(raw, default="0.00"):
            try:
                return Decimal(raw or default)
            except (InvalidOperation, TypeError):
                return Decimal(default)

        max_shortfall = _money(options["max_shortfall"])
        max_surplus = _money(options["max_surplus"], "") if options["max_surplus"] else None

        owed = self._owed()
        movement = self._day_movement()

        if not wema.wema_live():
            # Mock get_balance returns 0.00 for every account, so a position computed
            # here would report the entire liability as a shortfall and page. The
            # liability and movement halves are still real and worth printing.
            self.stdout.write(
                "settlement_report: Wema not live (simulation/mock) — no real balances to "
                "hold against the ledger, so no position is computed.")
            self._print_owed(owed, movement)
            return

        held = self._held()
        rails = [held["nuban_total"], held["pool_balance"], held["provider_wallet"]]
        # A rail we could not read makes the position unsound, not merely imprecise.
        incomplete = (held["nuban_unreachable"] > 0
                      or (held["pool_account_set"] and held["pool_balance"] is None)
                      or held["provider_wallet"] is None)
        held_total = sum((r for r in rails if r is not None), Decimal("0"))
        position = held_total - owed["ledger_liability"]

        snapshot = {**owed, **held, **movement,
                    "held_total": held_total, "position": position,
                    "incomplete": incomplete}
        # Money is stored to a fixed 2dp so successive daily snapshots can be diffed
        # and compared directly; `5000 - 5000` would otherwise land as "0" one day
        # and "0.00" the next depending on the operands.
        snapshot = {k: (str(v.quantize(Decimal("0.01"))) if isinstance(v, Decimal) else v)
                    for k, v in snapshot.items()}
        record_audit("recon.settlement_report", actor_type="system", after=snapshot)

        self._print_owed(owed, movement)
        self.stdout.write(
            f"HELD    bank NUBANs ₦{held['nuban_total']:,.2f} over {held['nuban_wallets']} account(s)"
            + (f" ({held['nuban_unreachable']} unreachable)" if held["nuban_unreachable"] else ""))
        self.stdout.write(
            "        pool        " + (f"₦{held['pool_balance']:,.2f}" if held["pool_balance"] is not None
                                      else ("UNREADABLE" if held["pool_account_set"]
                                            else "not configured (WEMA_SOURCE_ACCOUNT)")))
        self.stdout.write(
            "        VTU wallet  " + (f"₦{held['provider_wallet']:,.2f}"
                                      if held["provider_wallet"] is not None else "UNREADABLE"))
        self.stdout.write(f"        total       ₦{held_total:,.2f}")
        self.stdout.write(
            f"POSITION {'+' if position >= 0 else '-'}₦{abs(position):,.2f} "
            f"({'surplus' if position >= 0 else 'SHORTFALL'})")

        breached = False
        if incomplete:
            # Not a solvency alert — an observability one. Say which rail.
            alert("settlement_report: position computed with an unreadable rail — treat it as "
                  "advisory until every rail reads", level="warning",
                  nuban_unreachable=held["nuban_unreachable"],
                  pool_readable=held["pool_balance"] is not None,
                  provider_readable=held["provider_wallet"] is not None)
            breached = True

        if position < 0 and -position > max_shortfall:
            alert("settlement_report: SHORTFALL — customer liability exceeds every asset rail "
                  "combined", level="error",
                  shortfall=str(-position), liability=str(owed["ledger_liability"]),
                  held=str(held_total), incomplete=incomplete)
            breached = True
        elif max_surplus is not None and position > max_surplus:
            alert("settlement_report: surplus above the configured ceiling — check for debits "
                  "that never reached a provider, or an overdue sweep", level="warning",
                  surplus=str(position), ceiling=str(max_surplus),
                  sweep_owed_to_provider=str(movement["sweep_owed_to_provider"]))
            breached = True

        if breached and options["fail_on_breach"]:
            raise SystemExit(1)

    def _print_owed(self, owed, movement):
        self.stdout.write(f"OWED    ledger liability ₦{owed['ledger_liability']:,.2f} "
                          f"(stored balances ₦{owed['stored_liability']:,.2f})")
        self.stdout.write(
            f"TODAY   in ₦{movement['funded_in']:,.2f} | bank out ₦{movement['bank_payouts_out']:,.2f} "
            f"| VAS out ₦{movement['vas_out']:,.2f} | internal ₦{movement['internal_transfers_out']:,.2f} "
            f"| other ₦{movement['other_out']:,.2f}")
        self.stdout.write(f"        sweep owed to VTU.ng today ₦{movement['sweep_owed_to_provider']:,.2f}")
