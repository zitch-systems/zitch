"""Remove isolated retired balances from active wallets, preserving what is owed."""
import json
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from wallet.models import RetiredFundingArchive, Transaction, UnsettledFunding, Wallet
from wallet.services import wallet_expected_balance
from whatsapp.models import AuditLog


class Command(BaseCommand):
    help = "Archive retired-only wallet balances; no bank payment, refund or debt cancellation."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--expected-count", type=int, required=True)
        parser.add_argument("--expected-amount", type=Decimal, required=True)
        parser.add_argument("--operator-reference", required=True)

    @transaction.atomic
    def handle(self, *args, **opts):
        reference = opts["operator_reference"].strip()
        if not reference or len(reference) > 160:
            raise CommandError("A bounded operator reference is required")
        user_ids = list(UnsettledFunding.objects.filter(archive__isnull=True)
                        .values_list("user_id", flat=True))
        wallets = {w.user_id: w for w in Wallet.objects.select_for_update()
                   .filter(user_id__in=user_ids).order_by("pk")}
        # Lock the hold table rows without an outer join (unsupported FOR UPDATE
        # on PostgreSQL). Recheck archive state after acquiring the wallet locks.
        holds = [h for h in UnsettledFunding.objects.select_for_update()
                 .filter(user_id__in=user_ids).order_by("pk")
                 if not RetiredFundingArchive.objects.filter(funding_id=h.pk).exists()]
        if not holds:
            self.stdout.write(json.dumps({"archived": 0, "already_empty": True}))
            return
        total = sum((h.amount for h in holds), Decimal("0"))
        if len(holds) != opts["expected_count"] or total != opts["expected_amount"]:
            raise CommandError("Inventory changed; review before archiving")
        if len({h.user_id for h in holds}) != len(holds):
            raise CommandError("Multiple retired balances need separate attribution review")
        for hold in holds:
            wallet = wallets.get(hold.user_id)
            if wallet is None or wallet.account_number or wallet.bank_accounts:
                raise CommandError("Only an isolated wallet without a Partnership account can be archived")
            if wallet.balance != hold.amount or wallet_expected_balance(hold.user_id) != hold.amount:
                raise CommandError("Wallet and retired balance disagree; mixed funds cannot be archived")
            evidence = hold.evidence.get("credits", [])
            ids = [r.get("transaction_id") for r in evidence]
            rows = list(Transaction.objects.select_for_update().filter(user_id=hold.user_id))
            if (not ids or len(ids) != len(set(ids)) or set(ids) != {r.pk for r in rows}
                    or any(r.direction != Transaction.IN or r.transaction_status != Transaction.SUCCESS
                           or r.currency != "NGN" for r in rows)
                    or sum((r.amount for r in rows), Decimal("0")) != hold.amount):
                raise CommandError("Original credits do not prove the whole retired-only balance")
            amounts = {r.pk: str(r.amount) for r in rows}
            if any(str(r.get("amount")) != amounts.get(r.get("transaction_id")) for r in evidence):
                raise CommandError("Retired credit evidence amounts disagree")
        result = {"wallets": len(holds), "amount": str(total), "apply": opts["apply"],
                  "bank_payment": False, "debt_forgiven": False}
        if opts["apply"]:
            for hold in holds:
                row = Transaction.objects.create(
                    user_id=hold.user_id, direction=Transaction.OUT, amount=hold.amount,
                    currency="NGN", transaction_status=Transaction.SUCCESS,
                    reference=f"RETIRED-ARCHIVE-{hold.pk}",
                    idempotency_key=f"retired-archive-{hold.pk}", service="Retired funding archive",
                    meta={"internal_evidence": True, "retired_funding_archive": hold.pk,
                          "bank_payment": False, "operator_reference": reference})
                RetiredFundingArchive.objects.create(funding=hold, transaction=row,
                                                     operator_reference=reference)
                wallet = wallets[hold.user_id]
                wallet.balance = Decimal("0.00")
                wallet.save(update_fields=["balance", "updated"])
            AuditLog.objects.create(actor_type="admin", actor_id=reference,
                action="retired_balances_archived", target="retired_funding",
                before={"active_wallet_balance": str(total)},
                after={**result, "active_wallet_balance": "0.00",
                       "archived_liability": str(total)})
        self.stdout.write(json.dumps(result))
