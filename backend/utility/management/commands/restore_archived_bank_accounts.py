"""Give customers back the account number the migration took off their wallet.

The 9PSB migration moved each customer's existing account into
`banking_previousbankarchive` and blanked `wallet.account_number`. The account
did not stop being theirs; we simply stopped showing it to them. That archive
row is our own record, written by us at migration time, of which account each
wallet held -- `PreviousBankArchive.wallet` is a PROTECTed one-to-one, so it
names exactly one wallet. Restoring from it needs no provider confirmation,
because it is not a guess about ownership: it is the ownership record.

That is the difference from a phone lookup against the bank, which this command
deliberately does not do. A lookup returning "an account exists for this phone"
does not say WHICH account, and attaching on it could bind one customer's
account to another's wallet. The archive says which. No bank call is made here.

Safety, in order of how much damage each prevents:

- A wallet that already has an account number is never touched. This only
  fills a blank, so it cannot overwrite a newly issued account.
- An account number already attached to a DIFFERENT wallet is never attached
  again. Two wallets sharing a NUBAN would mix up two customers' money.
- Numbers that are not a valid, non-retired 10-digit NUBAN are skipped, using
  the same `partnership_account_number` guard the rest of the system uses.
- Rows carrying unreconciled money -- a non-zero archived balance, pending
  transactions, or an explicit review flag -- are skipped unless you pass
  `--include-flagged`. Those are the cases where someone should look at the
  money before the customer can transact on the account again.

Dry run by default: it reports what it would do and changes nothing. `--apply`
performs the writes, each one inside a transaction with an AuditLog entry.

Usage
-----
    python manage.py restore_archived_bank_accounts
    python manage.py restore_archived_bank_accounts --apply
    python manage.py restore_archived_bank_accounts --apply --include-flagged

Prints counts and wallet ids only -- never an account number or holder name --
so its output is safe in a deploy log. Run `export_previous_bank_accounts`
first if you want the numbers themselves on file before anything changes.
"""
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection, transaction

ARCHIVE_TABLE = "banking_previousbankarchive"

SQL = f'''
    SELECT a.wallet_id, a.account_number, a.account_name, a.bank_name,
           a.balance, a.pending_transactions, a.review_required
      FROM "{ARCHIVE_TABLE}" a
     WHERE a.account_number IS NOT NULL AND a.account_number <> ''
     ORDER BY a.wallet_id
'''


def _hold_reasons(balance, pending, review):
    """Why this row is not a routine restore, or an empty list.

    The same three conditions are what `previous_provider_cutover_blockers`
    tests. That matters: while any of them holds, the customer's funding account
    is blocked there too, so writing the number onto the wallet would not
    surface it. Naming the reasons lets an operator see what reconciling
    actually involves rather than reaching for --include-flagged.
    """
    reasons = []
    if review:
        reasons.append("review flag")
    try:
        if Decimal(str(balance or "0")) != Decimal("0"):
            reasons.append("archived balance")
    except (InvalidOperation, ValueError):
        reasons.append("unreadable balance")  # treated as unresolved
    if int(pending or 0) > 0:
        reasons.append("pending transactions")
    return reasons


class Command(BaseCommand):
    help = ("Restore each wallet's archived bank account number from the "
            "migration archive. Dry run unless --apply.")

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true",
                            help="Perform the writes (default is a dry run).")
        parser.add_argument("--include-flagged", action="store_true",
                            dest="include_flagged",
                            help="Also restore rows with an archived balance, "
                                 "pending transactions or a review flag.")
        parser.add_argument("--limit", type=int, default=500,
                            help="Maximum wallets to process (default 500).")

    def handle(self, *args, **options):
        from wallet.models import Wallet
        from wallet.partnership import partnership_account_number
        from whatsapp.models import AuditLog

        apply_changes = options["apply"]
        include_flagged = options["include_flagged"]
        limit = max(1, min(int(options["limit"] or 500), 5000))

        if ARCHIVE_TABLE not in set(connection.introspection.table_names()):
            self.stdout.write(self.style.WARNING(
                f"{ARCHIVE_TABLE} does not exist on this database, so there is no "
                "archived account to restore. Nothing was done. If you expected "
                "rows, you are pointed at the wrong database."))
            return

        try:
            with connection.cursor() as cur:
                cur.execute(SQL)
                rows = cur.fetchall()[:limit]
        except DatabaseError as exc:
            raise CommandError(f"Could not read {ARCHIVE_TABLE}: {exc}") from exc

        restored, already, flagged, invalid, taken, missing = [], [], [], [], [], []
        held_by_reason, held_total = {}, Decimal("0")

        for (wallet_id, number, name, bank, balance, pending, review) in rows:
            number = (number or "").strip()
            wallet = Wallet.objects.filter(pk=wallet_id).first()
            if wallet is None:
                missing.append(wallet_id)
                continue
            if wallet.account_number:
                # Already has an account: either this restore ran before, or a
                # new one was issued. Never overwrite either.
                already.append(wallet_id)
                continue
            if not partnership_account_number(number):
                invalid.append(wallet_id)
                continue
            if Wallet.objects.filter(account_number=number).exclude(pk=wallet.pk).exists():
                # Two wallets must never share a NUBAN; that mixes up two
                # customers' incoming money.
                taken.append(wallet_id)
                continue
            reasons = _hold_reasons(balance, pending, review)
            if reasons and not include_flagged:
                flagged.append(wallet_id)
                for reason in reasons:
                    held_by_reason[reason] = held_by_reason.get(reason, 0) + 1
                try:
                    held_total += Decimal(str(balance or "0"))
                except (InvalidOperation, ValueError):
                    pass
                continue

            if apply_changes:
                with transaction.atomic():
                    locked = Wallet.objects.select_for_update().get(pk=wallet.pk)
                    if locked.account_number:
                        already.append(wallet_id)
                        continue
                    if Wallet.objects.filter(account_number=number).exclude(
                            pk=locked.pk).exists():
                        taken.append(wallet_id)
                        continue
                    before = {"account_number": "", "account_name": locked.account_name,
                              "bank_name": locked.bank_name}
                    locked.account_number = number
                    locked.account_name = (name or locked.account_name or "").strip()
                    locked.bank_name = (bank or "Wema Bank").strip()
                    locked.save(update_fields=["account_number", "account_name", "bank_name"])
                    AuditLog.objects.create(
                        actor_type="system", actor_id="restore_archived_bank_accounts",
                        action="archived_bank_account_restored",
                        target=f"wallet:{locked.pk}",
                        before=before,
                        after={"restored_from": ARCHIVE_TABLE,
                               "archived_balance": str(balance or "0"),
                               "pending_transactions": int(pending or 0),
                               "review_required": bool(review)})
            restored.append(wallet_id)

        verb = "Restored" if apply_changes else "Would restore"
        self.stdout.write(f"{verb} {len(restored)} archived account(s) to their wallets.")
        if restored:
            self.stdout.write(f"  wallets: {', '.join(str(w) for w in restored[:50])}"
                              + (" ..." if len(restored) > 50 else ""))
        self.stdout.write(f"  already had an account number: {len(already)}")
        self.stdout.write(f"  skipped, number invalid or retired: {len(invalid)}")
        self.stdout.write(f"  skipped, number already on another wallet: {len(taken)}")
        self.stdout.write(f"  skipped, archive row has no wallet: {len(missing)}")
        self.stdout.write(f"  skipped, money needs review first: {len(flagged)}")
        if flagged:
            for reason, count in sorted(held_by_reason.items()):
                self.stdout.write(f"      {reason}: {count}")
            if held_total:
                self.stdout.write(f"      archived balance held: {held_total:.2f}")
            # --include-flagged is offered, but it is not a shortcut, and saying
            # so here is the difference between a real fix and a number nobody
            # can see.
            self.stdout.write(self.style.WARNING(
                "      These rows also block the customer's funding account through\n"
                "      previous_provider_cutover_blockers, which tests the SAME three\n"
                "      conditions. --include-flagged would write the number, but the\n"
                "      customer still would not see it until the row is reconciled.\n"
                "      Reconcile the archived rows; then they restore with no flag."))

        if taken:
            self.stdout.write(self.style.ERROR(
                f"{len(taken)} archived number(s) are already attached to a different "
                "wallet. That is a data conflict, not a routine skip: investigate "
                "before anyone transacts on them."))
        if not apply_changes and restored:
            self.stdout.write(self.style.WARNING(
                "Dry run: nothing was written. Re-run with --apply to restore them."))
        elif apply_changes and restored:
            self.stdout.write(self.style.SUCCESS(
                "Each restore is recorded in AuditLog as archived_bank_account_restored."))
