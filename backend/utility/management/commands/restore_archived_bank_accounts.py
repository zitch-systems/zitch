"""Restore a previously archived Wema NUBAN only after a bank-confirmed review.

The migration archive is historical evidence of wallet association, NOT proof
that Wema still accepts the number as active or that all funds were settled.
Dry-run inventories remain read-only. Production writes require an exact,
single customer id and a bank-issued case/confirmation reference. A separate
bank confirmation must match the customer's archived NUBAN and holder details;
this command does not itself call a Wema ownership-verification endpoint.

Rows with an archived balance, pending transactions or a review flag can never
be restored by this command until separately reconciled. Existing wallet
numbers are never overwritten, and every accepted write is audited.

Usage:
    python manage.py restore_archived_bank_accounts                  # read-only
    python manage.py restore_archived_bank_accounts --user-id 25      # scoped dry-run
    python manage.py restore_archived_bank_accounts --apply \
      --user-id 25 --bank-confirmation-ref WEMA-CASE-REFERENCE
Never provide a generic or invented confirmation reference.
"""
from decimal import Decimal, InvalidOperation
import re

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


def _flagged(balance, pending, review):
    """Rows whose money is not accounted for are not routine restores."""
    try:
        has_balance = Decimal(str(balance or "0")) != Decimal("0")
    except (InvalidOperation, ValueError):
        has_balance = True  # unreadable balance is treated as unresolved
    return bool(review) or has_balance or int(pending or 0) > 0


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
        parser.add_argument("--user-id", type=int,
                            help="Only the specified customer; mandatory when applying writes.")
        parser.add_argument("--bank-confirmation-ref", default="",
                            help="Wema-issued confirmation that this customer's archived "
                                 "NUBAN and holder are current; mandatory for writes.")
        parser.add_argument("--limit", type=int, default=500,
                            help="Maximum wallets to process (default 500).")

    def handle(self, *args, **options):
        from wallet.models import Wallet
        from wallet.partnership import partnership_account_number
        from whatsapp.models import AuditLog

        apply_changes = options["apply"]
        include_flagged = options["include_flagged"]
        target_user_id = options.get("user_id")
        confirmation_ref = str(options.get("bank_confirmation_ref") or "").strip()
        limit = max(1, min(int(options["limit"] or 500), 5000))
        if target_user_id is not None and target_user_id < 1:
            raise CommandError("A positive --user-id is required.")
        if include_flagged:
            raise CommandError(
                "--include-flagged is disabled: reconcile balances, pending "
                "transactions and review flags separately before restoring a NUBAN.")
        if apply_changes and (target_user_id is None or
                              not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{7,127}",
                                               confirmation_ref)):
            raise CommandError(
                "Production restore requires --user-id and an actual Wema "
                "--bank-confirmation-ref for that specific archived account. "
                "Run a scoped dry-run first.")
        target_wallet_id = None
        if target_user_id is not None:
            target_wallet_id = Wallet.objects.filter(user_id=target_user_id).values_list(
                "pk", flat=True).first()
            if target_wallet_id is None:
                raise CommandError("This customer has no wallet to restore.")

        if ARCHIVE_TABLE not in set(connection.introspection.table_names()):
            self.stdout.write(self.style.WARNING(
                f"{ARCHIVE_TABLE} does not exist on this database, so there is no "
                "archived account to restore. Nothing was done. If you expected "
                "rows, you are pointed at the wrong database."))
            return

        try:
            with connection.cursor() as cur:
                if target_wallet_id is not None:
                    cur.execute(SQL.replace("ORDER BY a.wallet_id",
                                            "AND a.wallet_id = %s ORDER BY a.wallet_id"),
                                [target_wallet_id])
                else:
                    cur.execute(SQL)
                rows = cur.fetchall()[:limit]
        except DatabaseError as exc:
            raise CommandError(f"Could not read {ARCHIVE_TABLE}: {exc}") from exc

        restored, already, flagged, invalid, taken, missing = [], [], [], [], [], []

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
            if (not partnership_account_number(number) or
                    (bank or "").strip().casefold() not in
                    {"wema", "wema bank", "alat by wema"}):
                invalid.append(wallet_id)
                continue
            if Wallet.objects.filter(account_number=number).exclude(pk=wallet.pk).exists():
                # Two wallets must never share a NUBAN; that mixes up two
                # customers' incoming money.
                taken.append(wallet_id)
                continue
            if _flagged(balance, pending, review):
                flagged.append(wallet_id)
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
                               "review_required": bool(review),
                               "bank_confirmation_ref": confirmation_ref})
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
        self.stdout.write(
            f"  skipped, money needs review first: {len(flagged)}"
            + "  (bank/ledger reconciliation is required first)")

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
