"""Cancel one operator-authorized archived liability without forging settlement."""
import json
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from wallet.models import FundingHoldResolution, RetiredFundingArchive, Transaction, UnsettledFunding, Wallet
from wallet.services import wallet_expected_balance
from whatsapp.models import AuditLog


class Command(BaseCommand):
    help = "Audited operator write-off of one isolated archived liability. Dry run by default."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--user-id", type=int, required=True)
        parser.add_argument("--expected-amount", type=Decimal, required=True)
        parser.add_argument("--operator-reference", required=True)

    @transaction.atomic
    def handle(self, *args, **opts):
        amount = opts["expected_amount"]
        reference = opts["operator_reference"].strip()
        if (opts["user_id"] < 1 or not amount.is_finite() or amount <= 0
                or amount > Decimal("999999999999.99")
                or amount != amount.quantize(Decimal("0.01"))
                or not reference or len(reference) > 160
                or any(ord(char) < 32 for char in reference)):
            raise CommandError("A customer, positive exact amount and bounded operator reference are required")
        wallet = Wallet.objects.select_for_update().filter(user_id=opts["user_id"]).first()
        holds = list(UnsettledFunding.objects.select_for_update().filter(user_id=opts["user_id"]))
        if wallet is None or len(holds) != 1 or holds[0].amount != amount:
            raise CommandError("Inventory does not match the exact customer and amount; nothing changed")
        hold = holds[0]
        if FundingHoldResolution.objects.filter(funding=hold).exists():
            self.stdout.write(json.dumps({"already_resolved": True, "user_id": wallet.user_id,
                                          "amount": str(amount), "bank_payment": False}))
            return
        archive = RetiredFundingArchive.objects.select_for_update().filter(funding=hold).first()
        if (archive is None or wallet.account_number or wallet.bank_accounts or wallet.balance != 0
                or wallet_expected_balance(wallet.user_id) != 0
                or Transaction.objects.filter(user_id=wallet.user_id,
                                               transaction_status=Transaction.PENDING).exists()):
            raise CommandError("Only an isolated, zero-balance archived liability may be written off")
        debit = Transaction.objects.select_for_update().get(pk=archive.transaction_id)
        if (debit.user_id != hold.user_id or debit.amount != amount or debit.currency != "NGN"
                or debit.direction != Transaction.OUT or debit.transaction_status != Transaction.SUCCESS
                or not isinstance(debit.meta, dict)
                or debit.meta.get("retired_funding_archive") != hold.pk
                or debit.meta.get("bank_payment") is not False):
            raise CommandError("Archive provenance does not match the isolated liability")
        result = {"user_id": wallet.user_id, "amount": str(amount), "apply": opts["apply"],
                  "kind": "operator_write_off", "bank_payment": False,
                  "bank_settlement_verified": False, "wallet_balance_changed": False,
                  "source_evidence_preserved": True}
        if opts["apply"]:
            resolution = FundingHoldResolution.objects.create(
                funding=hold, operator_reference=reference)
            AuditLog.objects.create(actor_type="admin", actor_id=reference,
                action="retired_funding_written_off", target=f"funding:{hold.pk}",
                before={"outstanding_liability": str(amount)},
                after={**result, "outstanding_liability": "0.00", "resolution_id": resolution.pk,
                       "operator_reference": reference})
        self.stdout.write(json.dumps(result, sort_keys=True))
