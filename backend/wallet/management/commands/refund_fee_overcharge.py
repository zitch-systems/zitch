"""Refund the transfer fees charged with VAT added twice (13 November 2026).

Between #631 and #636 the transfer fee added 7.5% VAT to the bank's own
figure, which already included it: ₦11.56 for the bank's ₦10.75, ₦28.90 for
₦26.88, ₦57.78 for ₦53.75. The bank took its real fee, so each such transfer
left the customer's ledger that much below their bank balance. That is the
"bank NUBAN balance exceeds ledger" the balance check has reported since
(₦1.62 = two transfers at ₦0.81).

For every successful fee row at one of those amounts, made before ``--before``,
this credits the difference back as a "Transfer fee refund". The refund's
reference is derived from the fee's, so a fee is never refunded twice however
often this runs. If the transfer is later reversed and its whole fee returned,
the refund is taken back (wallet.transfer_fees).

Dry run by default; --apply posts the refunds.
"""
import json
from datetime import datetime
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction
from django.utils import timezone

from wallet.models import Transaction, Wallet
from wallet.services import credit, wallet_expected_balance
from wallet.transfer_fees import (
    DOUBLE_VAT_FEES, FEE_FOR_KEY, FEE_SERVICE, REFUND_FOR_KEY, REFUND_SERVICE,
    overcharge_refund_reference,
)
from whatsapp.ops import record_audit

#: #636 corrected the fee on 13 November 2026; nothing after this could carry it.
DEFAULT_BEFORE = "2026-11-14"


class Command(BaseCommand):
    help = "Refund transfer fees that carried VAT twice. Dry run by default."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Post the refunds.")
        parser.add_argument("--user-id", type=int, help="Only this customer.")
        parser.add_argument("--before", default=DEFAULT_BEFORE,
                            help=f"Only fees made before this Lagos date (default {DEFAULT_BEFORE}).")

    def handle(self, *args, **opts):
        try:
            day = datetime.strptime(opts["before"], "%Y-%m-%d")
        except ValueError:
            raise CommandError("--before must be a date like 2026-11-14") from None
        before = timezone.make_aware(day)
        fees = (Transaction.objects
                .filter(service=FEE_SERVICE, direction=Transaction.OUT,
                        transaction_status=Transaction.SUCCESS, meta__has_key=FEE_FOR_KEY,
                        amount__in=list(DOUBLE_VAT_FEES), created__lt=before)
                .select_related("user").order_by("created"))
        if opts["user_id"]:
            fees = fees.filter(user_id=opts["user_id"])

        rows, users = [], set()
        for fee in fees:
            refund = fee.amount - DOUBLE_VAT_FEES[fee.amount]
            reference = overcharge_refund_reference(fee.reference)
            row = {"user_id": fee.user_id, "fee_reference": fee.reference,
                   "charged": str(fee.amount), "bank_fee": str(DOUBLE_VAT_FEES[fee.amount]),
                   "refund": str(refund), "refund_reference": reference}
            if Transaction.objects.filter(reference=reference).exists():
                rows.append({**row, "already_refunded": True})
                continue
            if opts["apply"]:
                row["posted"] = self._post(fee, refund, reference)
                users.add(fee.user_id)
            rows.append(row)

        total = sum(Decimal(r["refund"]) for r in rows if not r.get("already_refunded"))
        summary = {"dry_run": not opts["apply"], "fees": len(rows), "to_refund": str(total)}
        if opts["apply"]:
            summary["ledger_agrees"] = {
                str(user_id): wallet_expected_balance(user_id)
                == Wallet.objects.get(user_id=user_id).balance
                for user_id in sorted(users)}
        self.stdout.write(json.dumps({"summary": summary, "fees": rows}, indent=2))

    def _post(self, fee, refund, reference):
        try:
            with transaction.atomic():
                credit(fee.user, refund, REFUND_SERVICE, reference=reference,
                       meta={REFUND_FOR_KEY: fee.reference, "suppress_transaction_alert": True,
                             "reason": "VAT was added twice to the bank's transfer fee"})
                record_audit("wallet.transfer_fee_overcharge_refund", actor_type="system",
                             target=f"user:{fee.user_id}",
                             after={"fee_reference": fee.reference, "charged": str(fee.amount),
                                    "refund": str(refund), "reference": reference})
        except IntegrityError:
            return False   # refunded by a run that raced this one
        return True
