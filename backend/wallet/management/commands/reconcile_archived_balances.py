"""The 9PSB archive rows: money the three existing reconciliations cannot see.

The three checks in place all measure the CURRENT partner-bank rail:

  * ``integrity_check``     stored wallet balance vs that wallet's own ledger
  * ``reconcile_balances``  a wallet's ledger vs that wallet's NUBAN at the bank
  * ``settlement_report``   everything owed vs everything held, across rails

None of them can see a former-provider archive row. ``settlement_report`` says
why in its own docstring: that rail was removed. So a balance recorded against a
retired 9PSB account is measured by nothing — it is not in a NUBAN any check
reads, and it is only in the ledger liability if something credited it there.

This command answers the one question that decides what to do about each row:
**is this money on our books anywhere?** There are three answers.

  ``liability_on_book``  A hold is matched to the archived NUBAN's digest
                         AND balance amount, with no pending transactions.
                         There is a strong internal hold association, not
                         proof of external settlement. Bank evidence is still required.

  ``wallet_overlap_unverified`` The archived balance equals the stored wallet
                         balance, without an attributable funding hold. It may
                         already be carried as a customer liability, but its
                         provenance and bank settlement are unproven.

  ``hold_unattributed`` A hold exists for this customer but cannot be
                         linked to the archived bank number AND amount. Its
                         presence alone is NOT proof this row is on-book.
                         Investigate both internal funding evidence and the
                         former bank's closing statement.

  ``off_book``           The archive row carries money and there is NO hold.
                         Our books do not record owing it. If the customer is
                         in fact still owed it, that liability exists and is
                         not written down anywhere. This is the exposure, and
                         it is the only state that needs the bank.

  ``flag_only``          ``review_required`` with no balance and no pending
                         transactions. Nothing is at stake internally; the flag
                         by itself is not a liability.

It writes nothing, holds no locks, and reports ids, states and amounts only --
never an account number, holder name, phone or email -- so the output is safe
in a ticket or a deploy log.

A same-amount credit in the ledger is reported as a HINT and nothing more. The
precedent is ``reconciliation_evidence``, which says it outright: "reported
reference/amount matches are investigative hints, not settlement proof." Two
customers owed the same round figure is ordinary, so an amount match must never
be read as proof that this row was settled.

Usage
-----
    python manage.py reconcile_archived_balances
    python manage.py reconcile_archived_balances --json
"""
import json
import hashlib
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection
from django.db.models import Sum
from wallet.services import wallet_expected_balance

ARCHIVE_TABLE = "banking_previousbankarchive"

SQL = f'''
    SELECT wallet_id, balance, pending_transactions, review_required, created,
           account_number
      FROM "{ARCHIVE_TABLE}"
     ORDER BY balance DESC, wallet_id
'''


def _money(value):
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, ValueError):
        return None


class Command(BaseCommand):
    help = ("Read-only: classify each former-provider archive row by whether its "
            "money is recorded as owed on our books. Writes nothing.")

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json",
                            help="Machine-readable output (ids, states and amounts only).")

    def handle(self, *args, **options):
        from wallet.models import Transaction, UnsettledFunding, Wallet

        if ARCHIVE_TABLE not in set(connection.introspection.table_names()):
            self.stdout.write(self.style.WARNING(
                f"{ARCHIVE_TABLE} does not exist on this database, so there is no "
                "former-provider balance to reconcile. If you expected rows, you "
                "are pointed at the wrong database."))
            return

        try:
            with connection.cursor() as cur:
                cur.execute(SQL)
                archive_rows = cur.fetchall()
        except DatabaseError as exc:
            raise CommandError(f"Could not read {ARCHIVE_TABLE}: {exc}") from exc

        wallets = {w.pk: w for w in Wallet.objects.filter(
            pk__in=[r[0] for r in archive_rows])}

        rows = []
        for (wallet_id, balance, pending, review, created, archived_number) in archive_rows:
            wallet = wallets.get(wallet_id)
            archived = _money(balance)
            pending = int(pending or 0)
            has_money = archived is None or archived != Decimal("0") or pending > 0

            holds = {"count": 0, "amount": "0.00", "unsettled_count": 0}
            # A hold for this USER may relate to a different old bank account,
            # VAS destination, or a different amount. Its mere presence cannot
            # account for this archive row. Match the archived NUBAN's digest
            # and the precise amount; never print the number or digest.
            linked_hold_amount = None
            credit_hint = None
            expected_ledger = None
            if wallet is not None:
                expected_ledger = wallet_expected_balance(wallet.user_id)
                agg = UnsettledFunding.objects.filter(user_id=wallet.user_id).aggregate(
                    total=Sum("amount"))
                holds = {
                    "count": UnsettledFunding.objects.filter(
                        user_id=wallet.user_id).count(),
                    "amount": str(agg["total"] or Decimal("0.00")),
                    "unsettled_count": UnsettledFunding.objects.filter(
                        user_id=wallet.user_id, archive__isnull=True).count(),
                }
                if archived_number and isinstance(archived_number, str):
                    digest = hashlib.sha256(archived_number.strip().encode()).hexdigest()
                    linked_hold_amount = UnsettledFunding.objects.filter(
                        user_id=wallet.user_id, destination_digest=digest
                    ).values_list("amount", flat=True).first()
                if archived is not None and archived != Decimal("0"):
                    # A hint only. See the module docstring.
                    credit_hint = Transaction.objects.filter(
                        user_id=wallet.user_id, direction=Transaction.IN,
                        transaction_status=Transaction.SUCCESS,
                        amount=archived).count()

            if wallet is None:
                state = "no_wallet"
            elif not has_money:
                state = "flag_only"
            elif (linked_hold_amount is not None and archived is not None
                  and archived > 0 and linked_hold_amount == archived and pending == 0):
                # Strong INTERNAL association: same account digest and amount.
                # Still NOT evidence that the bank paid or closed the account.
                state = "liability_on_book"
            elif holds["count"]:
                state = "hold_unattributed"
            elif (archived is not None and archived > 0 and pending == 0
                  and wallet.balance == archived):
                # Matching stored balance is *possible* liability overlap, not
                # proof of archived bank-account provenance or settlement.
                state = "wallet_overlap_unverified"
            else:
                state = "off_book"

            rows.append({
                "wallet_id": wallet_id,
                "user_id": wallet.user_id if wallet else None,
                "archived_balance": str(archived) if archived is not None else "unreadable",
                "pending_transactions": pending,
                "review_required": bool(review),
                "archived_on": created.date().isoformat() if hasattr(created, "date") else None,
                "ledger_balance": str(wallet.balance) if wallet else None,
                "transaction_ledger_expected": (str(expected_ledger)
                                                 if expected_ledger is not None else None),
                "wallet_balance_matches_archive": bool(
                    wallet is not None and archived is not None
                    and archived > 0 and wallet.balance == archived),
                "transaction_ledger_matches_archive": bool(
                    expected_ledger is not None and archived is not None
                    and archived > 0 and expected_ledger == archived),
                "unsettled_funding_holds": holds,
                "linked_hold_amount": (str(linked_hold_amount)
                                       if linked_hold_amount is not None else None),
                "attribution": ("matching_destination_and_amount"
                                if state == "liability_on_book"
                                else "unverified_customer_hold" if state == "hold_unattributed"
                                else "none"),
                "same_amount_credits_in_ledger": credit_hint,
                "state": state,
            })

        off_book = [r for r in rows if r["state"] == "off_book"]
        on_book = [r for r in rows if r["state"] == "liability_on_book"]
        hold_unattributed = [r for r in rows if r["state"] == "hold_unattributed"]
        wallet_overlap = [r for r in rows if r["state"] == "wallet_overlap_unverified"]
        flag_only = [r for r in rows if r["state"] == "flag_only"]
        orphans = [r for r in rows if r["state"] == "no_wallet"]

        def _total(group):
            total = Decimal("0.00")
            for row in group:
                amount = _money(row["archived_balance"])
                if amount is not None:
                    total += amount
            return total

        # Answers "was review_required blanket-set at migration, or does it mark
        # specific suspicion?" -- a flag on rows with no money at all is not
        # evidence about those rows, it is a default.
        flagged = [r for r in rows if r["review_required"]]
        flagged_without_money = [r for r in flagged if r["state"] == "flag_only"]
        blanket = bool(rows) and len(flagged) == len(rows)

        payload = {
            "read_only": True,
            "archive_rows": len(rows),
            "off_book_rows": len(off_book),
            "off_book_amount": str(_total(off_book)),
            "liability_on_book_rows": len(on_book),
            "liability_on_book_amount": str(_total(on_book)),
            "hold_unattributed_rows": len(hold_unattributed),
            "hold_unattributed_amount": str(_total(hold_unattributed)),
            "wallet_overlap_unverified_rows": len(wallet_overlap),
            "wallet_overlap_unverified_amount": str(_total(wallet_overlap)),
            "flag_only_rows": len(flag_only),
            "orphan_rows": len(orphans),
            "review_required_rows": len(flagged),
            "review_required_rows_with_no_money": len(flagged_without_money),
            "review_flag_looks_blanket": blanket,
            "customers": rows,
        }

        if options["as_json"]:
            self.stdout.write(json.dumps(payload, sort_keys=True))
            return

        if not rows:
            self.stdout.write(self.style.SUCCESS(
                f"{ARCHIVE_TABLE} exists but is empty. Nothing to reconcile."))
            return

        self.stdout.write(f"{len(rows)} former-provider archive row(s). Read-only; "
                          "no account numbers, names or contact details below.\n")
        for row in rows:
            self.stdout.write(
                f"wallet {row['wallet_id']}  user={row['user_id'] or '-'}  "
                f"archived={row['archived_balance']}  pending={row['pending_transactions']}  "
                f"review={'Y' if row['review_required'] else 'n'}  "
                f"archived_on={row['archived_on'] or '-'}  "
                f"ledger={row['ledger_balance'] or '-'}  "
                f"state={row['state']}")
            holds = row["unsettled_funding_holds"]
            if holds["count"]:
                self.stdout.write(
                    f"    holds: {holds['count']} ({holds['unsettled_count']} unsettled), "
                    f"total {holds['amount']}")
            if row["same_amount_credits_in_ledger"]:
                self.stdout.write(
                    f"    hint: {row['same_amount_credits_in_ledger']} successful credit(s) "
                    "in the ledger for this exact amount. A HINT, not settlement proof.")
            self.stdout.write("")

        self.stdout.write("--- position ---")
        self.stdout.write(
            f"off book:            {len(off_book)} row(s), {_total(off_book):.2f} "
            "— not recorded as owed anywhere on our books")
        self.stdout.write(
            f"liability on book:   {len(on_book)} row(s), {_total(on_book):.2f} "
            "— linked by account digest and exact amount; bank settlement not proven")
        self.stdout.write(
            f"wallet overlap:      {len(wallet_overlap)} row(s), "
            f"{_total(wallet_overlap):.2f} — stored wallet balance matches "
            "archive; verify transaction ledger and bank statement")
        self.stdout.write(
            f"hold not attributable: {len(hold_unattributed)} row(s), "
            f"{_total(hold_unattributed):.2f} — customer has hold(s), but account "
            "and amount do not prove this specific archive liability")
        self.stdout.write(f"flag only, no money: {len(flag_only)} row(s)")
        if orphans:
            self.stdout.write(self.style.ERROR(
                f"archive row with no wallet: {len(orphans)} row(s) — investigate; "
                "an archived balance with nothing to attach it to is not a routine skip."))

        if blanket:
            self.stdout.write(self.style.WARNING(
                f"\nreview_required is set on ALL {len(rows)} row(s), including "
                f"{len(flagged_without_money)} with no balance and no pending "
                "transactions. A flag that is set on rows with nothing at stake is a "
                "migration default, not a finding about any particular customer: it "
                "cannot be read as suspicion. What decides each row is the state above."))
        elif flagged:
            self.stdout.write(
                f"\nreview_required is set on {len(flagged)} of {len(rows)} row(s), so it "
                "was applied selectively and is worth reading as a signal on those rows.")

        if off_book:
            self.stdout.write(self.style.ERROR(
                f"\n{len(off_book)} row(s) totalling {_total(off_book):.2f} are OFF BOOK. "
                "Our books do not record owing this money, and no reconciliation here can "
                "establish where it went: that needs the former provider's closing "
                "statement for those accounts. Until it arrives, do not clear the review "
                "flag and do not restore the number — the flag is the only thing currently "
                "stopping the balance being treated as settled."))
        if wallet_overlap:
            self.stdout.write(self.style.WARNING(
                f"{len(wallet_overlap)} row(s) totalling "
                f"{_total(wallet_overlap):.2f} have matching stored wallet "
                "balances but no attributable funding hold. NOT proven off-book: "
                "compare the append-only ledger and former bank's closing "
                "statement before adding new liabilities or credits."))
        if hold_unattributed:
            self.stdout.write(self.style.ERROR(
                f"{len(hold_unattributed)} row(s) totalling "
                f"{_total(hold_unattributed):.2f} have a customer hold but NO "
                "proven link to the archived account and amount. Do not call these "
                "settled or on-book: correlate the original funding reference and "
                "obtain the old bank's closing statement."))
        if on_book:
            self.stdout.write(self.style.WARNING(
                f"{len(on_book)} row(s) totalling {_total(on_book):.2f} are already held as "
                "UnsettledFunding. Those resolve through the evidenced settlement path "
                "(see settlement_report and the UnsettledFunding docstring); deletion is "
                "not settlement and this command cannot resolve them."))
        if not off_book and not on_book and not hold_unattributed and not wallet_overlap:
            self.stdout.write(self.style.SUCCESS(
                "\nNo archive row carries money. There is no former-provider liability to "
                "reconcile, and the review flags are not protecting any balance."))
