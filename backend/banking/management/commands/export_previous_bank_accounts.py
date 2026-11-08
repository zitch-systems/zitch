"""Export the wallet -> former Wema account mapping before the archive is dropped.

Why this exists
---------------
The 9PSB migration moved every customer's Wema account into
``banking.PreviousBankArchive`` and issued them a new 9PSB NUBAN. Going back to
Wema reverses the code but NOT the data: a wallet's ``account_number`` holds the
9PSB number, and the only record of the Wema one it had before is this archive.

Dropping the banking app drops that table. Run this first. Without it, a
customer who gave a payer their old Wema account number cannot be reconnected to
it, and there is no way to reconcile an archived balance against the account it
came from.

The archive is immutable by design (``save``/``delete`` raise), so this only
reads. It is safe to run as many times as you like.

Usage
-----
    python manage.py export_previous_bank_accounts --out /secure/path/wema-accounts.csv

Keep the file somewhere you would keep a customer list: it contains account
numbers, holder names and balances. Do not commit it. The command refuses to
write inside the repository for that reason, and prints only counts -- never a
row -- so the output is safe in a deploy log.
"""
import csv
import os
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

#: Written for every row, in this order. Chosen so the file alone is enough to
#: reconnect a customer: who they are, which account was theirs, what it held,
#: and whether anyone had already flagged it for review.
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


class Command(BaseCommand):
    help = ("Export each wallet's former partner-bank account from "
            "PreviousBankArchive to a CSV, before that table is dropped.")

    def add_arguments(self, parser):
        parser.add_argument(
            "--out", required=True,
            help="Destination CSV path. Must be outside the repository.")
        parser.add_argument(
            "--force", action="store_true",
            help="Overwrite the destination if it already exists.")

    def handle(self, *args, **options):
        # Imported here, not at module scope: this command ships on the branch
        # that still has the banking app, and a missing import at load time
        # would break `manage.py help` for every other command.
        from banking.models import PreviousBankArchive

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

        rows = (PreviousBankArchive.objects
                .select_related("wallet", "wallet__user")
                .order_by("pk"))

        written = 0
        with_balance = 0
        needing_review = 0
        total = Decimal("0.00")
        # 0o600: the file is a customer list the moment it is written, so it is
        # never briefly world-readable on a shared host.
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(COLUMNS)
            for archive in rows.iterator(chunk_size=500):
                wallet = archive.wallet
                user = getattr(wallet, "user", None)
                writer.writerow([
                    getattr(user, "pk", ""),
                    getattr(user, "phone", "") or "",
                    getattr(user, "email", "") or "",
                    wallet.pk,
                    archive.account_number,
                    archive.account_name,
                    archive.bank_name,
                    archive.account_reference,
                    f"{archive.balance:.2f}",
                    archive.pending_transactions,
                    "yes" if archive.review_required else "no",
                    archive.created.isoformat(timespec="seconds"),
                    wallet.account_number or "",
                    wallet.bank_name or "",
                ])
                written += 1
                total += archive.balance
                if archive.balance:
                    with_balance += 1
                if archive.review_required:
                    needing_review += 1

        # Counts only. A row here would put account numbers in the deploy log.
        self.stdout.write(f"Wrote {written} archived account(s) to {out}")
        self.stdout.write(f"  with a non-zero balance: {with_balance}")
        self.stdout.write(f"  flagged for review:      {needing_review}")
        self.stdout.write(f"  total archived balance:  {total:.2f}")
        if not written:
            self.stdout.write(self.style.WARNING(
                "No archived accounts found. Either no wallet carried a Wema "
                "account into the 9PSB migration, or this is not the production "
                "database. Check before dropping the table."))
        else:
            self.stdout.write(self.style.SUCCESS(
                "Keep this file with your customer records. It is the only "
                "remaining link between a customer and their former Wema account."))
