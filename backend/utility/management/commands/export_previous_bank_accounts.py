"""Export the wallet -> former Wema account mapping before that evidence is dropped.

Companion to `wema_cutover_audit`, which counts the same 9PSB-era state and
refuses a cutover while any of it is unresolved. This one writes down the part a
count cannot preserve: WHICH Wema account each wallet held.

The 9PSB migration moved every customer's Wema account into
banking_previousbankarchive and issued them a new 9PSB NUBAN. Restoring the Wema
code does not restore that association -- a wallet's account_number still holds
the 9PSB number, and the archive row is the only remaining record of the Wema
one. Run this before any step that drops those tables. Without the file, a
customer who gave a payer their old Wema number cannot be reconnected to it and
an archived balance cannot be tied back to the account it came from.

Reads the schema directly, never the ORM. The banking app does not exist in this
code tree: its models and migrations are intentionally absent, so there is
nothing to import. Every table is checked for existence first, exactly as
`wema_cutover_audit` does, so this runs without error on a database that never
carried 9PSB at all.

Strictly read-only. It opens no account, copies no number onto a wallet, clears
nothing and sends no bank request.

Usage
-----
    python manage.py export_previous_bank_accounts --out /secure/wema-accounts.csv

Keep the file where you would keep a customer list: it contains account numbers,
holder names and balances. Do not commit it -- the command refuses to write
inside the repository for that reason, and prints only counts, never a row, so
its output is safe in a deploy log.
"""
import csv
import os
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

#: Written for every row, in this order. Chosen so the file alone is enough to
#: reconnect a customer: who they are, which account was theirs, what it held,
#: whether anyone had already flagged it, and what their wallet carries NOW --
#: without that last pair the file says what was lost but not what replaced it.
COLUMNS = [
    "user_id",
    "phone",
    "email",
    "wallet_id",
    "previous_account_number",
    "previous_account_name",
    "previous_bank_name",
    "previous_account_reference",
    "archived_balance",
    "pending_transactions",
    "review_required",
    "archived_at",
    "current_wallet_account_number",
    "current_wallet_bank_name",
]

ARCHIVE_TABLE = "banking_previousbankarchive"

#: Joined out to the wallet and the user so a row names a person rather than an
#: opaque id -- but LEFT JOIN on BOTH, deliberately.
#:
#: An inner join would silently drop an archive row whose wallet is missing from
#: the database, and that is the one failure this file exists to prevent: the row
#: is evidence that a customer held an account with a balance, and it is worth
#: more with a blank wallet column than it is absent. The live schema should make
#: that impossible -- PreviousBankArchive.wallet is a PROTECTed one-to-one and
#: wallet.user_id is NOT NULL -- so this is defence against a partial restore or
#: a future schema change, not against today's constraints.
SQL = f'''
    SELECT a.id,
           a.account_number, a.account_name, a.bank_name, a.account_reference,
           a.balance, a.pending_transactions, a.review_required, a.created,
           w.id, w.account_number, w.bank_name,
           u.id, u.phone, u.email
      FROM "{ARCHIVE_TABLE}" a
      LEFT JOIN wallet_wallet w ON w.id = a.wallet_id
      LEFT JOIN accounts_user u ON u.id = w.user_id
     ORDER BY a.id
'''


class Command(BaseCommand):
    help = ("Export each wallet's former Wema account from the 9PSB-era archive "
            "to a CSV, before that table is dropped. Read-only.")

    def add_arguments(self, parser):
        parser.add_argument(
            "--out", required=True,
            help="Destination CSV path. Must be outside the repository.")
        parser.add_argument(
            "--force", action="store_true",
            help="Overwrite the destination if it already exists.")

    def handle(self, *args, **options):
        out = Path(options["out"]).expanduser().resolve()
        repo = Path(__file__).resolve().parents[4]
        if repo == out or repo in out.parents:
            raise CommandError(
                f"{out} is inside the repository ({repo}). This file holds account "
                "numbers and holder names; write it somewhere it cannot be committed.")
        if out.exists() and not options["force"]:
            raise CommandError(f"{out} already exists. Pass --force to overwrite it.")
        if not out.parent.is_dir():
            raise CommandError(f"{out.parent} is not a directory.")

        if ARCHIVE_TABLE not in set(connection.introspection.table_names()):
            # Not an error: a database that never carried 9PSB has nothing to
            # export. Said plainly so it cannot be mistaken for an empty export.
            self.stdout.write(self.style.WARNING(
                f"{ARCHIVE_TABLE} does not exist on this database, so there is no "
                "former-bank mapping to export. Nothing was written. If you expected "
                "rows, you are pointed at the wrong database."))
            return

        written = 0
        with_balance = 0
        needing_review = 0
        orphaned = 0
        total = Decimal("0.00")
        # 0o600: this is a customer list the moment it is written, so it is never
        # briefly world-readable on a shared host.
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(COLUMNS)
            with connection.cursor() as cur:
                cur.execute(SQL)
                for row in cur:
                    (archive_id, prev_number, prev_name, prev_bank, prev_ref,
                     balance, pending, review, created,
                     wallet_id, cur_number, cur_bank,
                     user_id, phone, email) = row
                    balance = Decimal(str(balance or "0"))
                    writer.writerow([
                        user_id if user_id is not None else "",
                        phone or "",
                        email or "",
                        wallet_id if wallet_id is not None else "",
                        prev_number or "",
                        prev_name or "",
                        prev_bank or "",
                        prev_ref or "",
                        f"{balance:.2f}",
                        pending if pending is not None else 0,
                        "yes" if review else "no",
                        created.isoformat(timespec="seconds") if created else "",
                        cur_number or "",
                        cur_bank or "",
                    ])
                    written += 1
                    total += balance
                    if balance:
                        with_balance += 1
                    if review:
                        needing_review += 1
                    if wallet_id is None or user_id is None:
                        orphaned += 1

        # Counts only. A row here would put account numbers in the deploy log.
        self.stdout.write(f"Wrote {written} archived account(s) to {out}")
        self.stdout.write(f"  with a non-zero balance: {with_balance}")
        self.stdout.write(f"  flagged for review:      {needing_review}")
        self.stdout.write(f"  total archived balance:  {total:.2f}")
        if orphaned:
            self.stdout.write(self.style.WARNING(
                f"  {orphaned} row(s) have no readable wallet or user; exported "
                "anyway, since an archived balance is evidence of money whoever it "
                "belonged to. Investigate before dropping anything."))
        if not written:
            self.stdout.write(self.style.WARNING(
                "The archive table exists but is empty. Either no wallet carried a "
                "Wema account into the 9PSB migration, or this is not the production "
                "database. Check before dropping the table -- a header-only file is "
                "not proof there was nothing to keep."))
        else:
            self.stdout.write(self.style.SUCCESS(
                "Keep this file with your customer records. It is the only remaining "
                "link between a customer and their former Wema account."))
