"""Record bank transfer fees the bank already took but Zitch never debited.

Before wallet.transfer_fees, a transfer to another bank debited only the amount
sent while the bank also took its NIP fee (₦10 + ₦0.75 VAT) from the customer's
own account. That left the Zitch balance above the bank's by ₦10.75 per such
transfer. This posts one audited "Bank transfer fees" debit to bring a named
customer's ledger back in line with what their bank statement shows.

Dry run by default; --apply posts it. The operator reference keys the row, so
the same correction can never be posted twice.
"""
import json
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from wallet.models import Transaction, Wallet
from wallet.services import InsufficientFunds, debit, wallet_expected_balance
from whatsapp.ops import record_audit

SERVICE = "Bank transfer fees"


class Command(BaseCommand):
    help = "Audited debit of past bank transfer fees for one customer. Dry run by default."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--user-id", type=int, required=True)
        parser.add_argument("--amount", type=Decimal, required=True,
                            help="The fees the bank statement shows, e.g. 21.50")
        parser.add_argument("--operator-reference", required=True,
                            help="Why, e.g. 'fees on 13 Nov transfers per Wema statement'")

    def handle(self, *args, **opts):
        amount = opts["amount"]
        note = opts["operator_reference"].strip()
        if (opts["user_id"] < 1 or not amount.is_finite() or amount <= 0
                or amount > Decimal("100000")
                or amount != amount.quantize(Decimal("0.01"))
                or not note or len(note) > 160 or any(ord(char) < 32 for char in note)):
            raise CommandError("A customer, a positive exact amount (at most ₦100,000) "
                               "and a bounded operator reference are required")
        wallet = Wallet.objects.filter(user_id=opts["user_id"]).select_related("user").first()
        if wallet is None:
            raise CommandError("No wallet for that customer; nothing changed")
        reference = f"FEEFIX-{wallet.user_id}-{amount}"
        summary = {"user_id": wallet.user_id, "amount": str(amount),
                   "balance_before": str(wallet.balance),
                   "balance_after": str(wallet.balance - amount), "reference": reference}
        if Transaction.objects.filter(reference=reference).exists():
            self.stdout.write(json.dumps({**summary, "already_posted": True}))
            return
        if not opts["apply"]:
            self.stdout.write(json.dumps({**summary, "dry_run": True}))
            return
        try:
            with transaction.atomic():
                row = debit(wallet.user, amount, SERVICE,
                            meta={"transfer_fee_correction": True, "operator_reference": note},
                            reference=reference, enforce_limits=False)
                row.transaction_status = Transaction.SUCCESS
                row.save(update_fields=["transaction_status"])
                record_audit("wallet.transfer_fee_correction", actor_type="system",
                             target=f"user:{wallet.user_id}",
                             before={"balance": summary["balance_before"]},
                             after={"amount": str(amount), "reference": reference,
                                    "operator_reference": note})
        except InsufficientFunds as exc:
            raise CommandError("The balance is below the correction; nothing changed") from exc
        except IntegrityError:
            self.stdout.write(json.dumps({**summary, "already_posted": True}))
            return
        wallet.refresh_from_db()
        self.stdout.write(json.dumps({
            **summary, "posted": True, "balance_after": str(wallet.balance),
            "ledger_agrees": wallet_expected_balance(wallet.user_id) == wallet.balance}))
