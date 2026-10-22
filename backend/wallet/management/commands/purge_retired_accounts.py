"""Delete retired account records without deleting the money owed to customers."""
import hashlib
import json
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from legacy_funding.models import MigrationApproval, Receipt, VirtualAccount
from wallet.legacy_funding import account_balance
from wallet.models import BillFundingBinding, UnsettledFunding, Wallet, Transaction
from whatsapp.models import AuditLog


class Command(BaseCommand):
    help = "Preview or explicitly purge retired accounts, preserving ledger liabilities."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--expected-accounts", type=int, required=True)
        parser.add_argument("--expected-receipts", type=int, required=True)
        parser.add_argument("--expected-unsettled", type=Decimal, required=True)
        parser.add_argument("--operator-reference", required=True)

    @transaction.atomic
    def handle(self, *args, **opts):
        reference = opts["operator_reference"].strip()
        if not reference or len(reference) > 160:
            raise CommandError("An operator reference of at most 160 characters is required")
        # Match the spend path's lock order. Do not lock or mutate unrelated wallets.
        owner_ids = list(VirtualAccount.objects.values_list("user_id", flat=True))
        list(Wallet.objects.select_for_update().filter(user_id__in=owner_ids).order_by("pk"))
        accounts = list(VirtualAccount.objects.select_for_update().order_by("pk"))
        if not accounts:
            self.stdout.write(json.dumps({"accounts_deleted": 0, "already_empty": True}))
            return
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("LOCK TABLE wema_vas_virtualaccount, wema_vas_receipt, "
                               "wema_vas_migrationapproval IN EXCLUSIVE MODE")
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        if (len(accounts) != opts["expected_accounts"]
                or VirtualAccount.objects.count() != len(accounts)
                or Receipt.objects.count() != opts["expected_receipts"]):
            raise CommandError("Inventory changed; review counts before deletion")
        if MigrationApproval.objects.exists() or BillFundingBinding.objects.filter(vas_account__isnull=False).exists():
            raise CommandError("Linked approvals or bill funding require separate evidence migration")
        if Wallet.objects.filter(account_number__in=[a.number for a in accounts]).exists():
            raise CommandError("A wallet still uses a retired destination")
        prepared = []
        for account in accounts:
            receipts = list(account.receipts.select_related("transaction").order_by("pk"))
            balance = account_balance(account)
            if not account.active or account.block_reason or balance < 0:
                raise CommandError("Restricted or negative account requires separate review")
            if any(r.state != Receipt.CREDITED for r in receipts):
                raise CommandError("Uncredited receipt requires separate evidence review")
            if account.mode == VirtualAccount.VALIDATION and (balance or receipts):
                raise CommandError("Non-empty validation account requires separate review")
            if any(r.transaction.user_id != account.user_id or r.transaction.amount != r.amount
                   or r.transaction.direction != Transaction.IN
                   or r.transaction.transaction_status != Transaction.SUCCESS
                   or r.transaction.currency != "NGN" for r in receipts):
                raise CommandError("Receipt and ledger evidence disagree")
            if receipts and balance == 0:
                raise CommandError("Settled receipt history requires separate evidence review")
            prepared.append((account, balance, receipts))
        total = sum((balance for _, balance, _ in prepared), Decimal("0"))
        if total != opts["expected_unsettled"]:
            raise CommandError("Unsettled amount changed; deletion stopped")
        result = {"accounts": len(accounts), "receipts": Receipt.objects.count(),
                  "unsettled_amount": str(total), "applied": opts["apply"]}
        if not opts["apply"]:
            self.stdout.write(json.dumps(result))
            return
        for account, balance, receipts in prepared:
            if balance:
                UnsettledFunding.objects.create(
                    user_id=account.user_id, amount=balance,
                    destination_digest=hashlib.sha256(account.number.encode()).hexdigest(),
                    operator_reference=reference,
                    evidence={"currency": "NGN", "bank_settlement_confirmed": False,
                              "credits": [{"transaction_id": r.transaction_id,
                                  "reference": str(r.reference), "session_id": r.session_id,
                                  "payment_reference": r.payment_reference, "fingerprint": r.fingerprint,
                                  "amount": str(r.amount), "source_account": r.source_account,
                                  "source_bank": r.source_bank, "occurred_at": r.occurred_at.isoformat(),
                                  "received_at": r.received_at.isoformat()} for r in receipts]})
        # Only these two retired-table triggers are suspended, inside the same
        # atomic transaction and exclusive table locks. Ledger/audit protections
        # remain enabled. Any failure rolls back holds, deletion and trigger DDL.
        triggers = (("wema_vas_receipt", "wema_vas_receipt_immutable"),
                    ("wema_vas_virtualaccount", "wema_vas_account_immutable"))
        with connection.cursor() as cursor:
            if connection.vendor == "postgresql":
                for table, trigger in triggers:
                    cursor.execute(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}")
            Receipt.objects.all().delete()
            VirtualAccount.objects.all().delete()
            if connection.vendor == "postgresql":
                for table, trigger in triggers:
                    cursor.execute(f"ALTER TABLE {table} ENABLE TRIGGER {trigger}")
        AuditLog.objects.create(actor_type="admin", actor_id=reference, action="retired_accounts_purged",
                                target="retired_funding", before=result,
                                after={"accounts": 0, "receipts": 0, "unsettled_amount": str(total),
                                       "ledger_changed": False, "bank_accounts_closed": False})
        self.stdout.write(json.dumps(result))
