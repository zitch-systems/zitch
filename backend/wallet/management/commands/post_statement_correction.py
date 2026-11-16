"""Bring one customer's ledger in line with their bank statement, for one cause.

Settlement work on the accounts detached on 30 Oct 2026 found ledgers above the
bank for reasons ``post_transfer_fee_correction`` does not cover. Each kind here
is one such reason, posted as one audited debit the customer is alerted about:

  bank-charges              Charges the bank took from the customer's own account
                            that Zitch never recorded (SMS alerts, maintenance).
                            Keyed on the operator reference, so a different
                            month's charges can be posted under a new reference.
  reversed-deposit          A "Wallet funding" credit the bank later reversed or
                            never settled. --original-reference names that credit;
                            the amount must equal it; posted once per credit.
  charged-failed-purchase   A purchase Zitch marked Failed that the bank charged
                            anyway, where the customer DID receive it. If they did
                            not, Zitch owes them: put the money back in their
                            account and post nothing here. --original-reference
                            names the Failed row; the amount must equal it.

Every kind is a debit. Nothing here can credit a wallet: money Zitch owes a
customer is paid into their bank account, which the deposit sweep then records.

Dry run by default; --apply posts it.
"""
import hashlib
import json
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from wallet.models import Transaction, Wallet
from wallet.services import InsufficientFunds, debit, wallet_expected_balance
from whatsapp.ops import record_audit

KINDS = {
    # kind: (reference code, needs an original row)
    "bank-charges": ("CHG", False),
    "reversed-deposit": ("REVDEP", True),
    "charged-failed-purchase": ("FAILCHG", True),
}
MAX_AMOUNT = Decimal("100000")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:10].upper()


class Command(BaseCommand):
    help = "Audited debit that matches one customer's ledger to their bank statement. Dry run by default."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--user-id", type=int, required=True)
        parser.add_argument("--kind", choices=sorted(KINDS), required=True)
        parser.add_argument("--amount", type=Decimal, required=True,
                            help="Exactly what the bank statement shows, e.g. 18.00")
        parser.add_argument("--original-reference", default="",
                            help="The ledger reference this corrects (reversed-deposit, "
                                 "charged-failed-purchase)")
        parser.add_argument("--operator-reference", required=True,
                            help="Why and who approved, e.g. 'SMS charges Sep-Oct per Wema statement, approved by <name>'")

    def handle(self, *args, **opts):
        kind, amount = opts["kind"], opts["amount"]
        note = opts["operator_reference"].strip()
        original_ref = opts["original_reference"].strip()
        code, linked = KINDS[kind]
        if (opts["user_id"] < 1 or not amount.is_finite() or amount <= 0 or amount > MAX_AMOUNT
                or amount != amount.quantize(Decimal("0.01"))
                or not note or len(note) > 160 or any(ord(char) < 32 for char in note)):
            raise CommandError("A customer, a positive exact amount (at most ₦100,000) "
                               "and a bounded operator reference are required")
        if linked != bool(original_ref):
            raise CommandError(f"--original-reference is {'required' if linked else 'not used'} "
                               f"for {kind}; nothing changed")
        wallet = Wallet.objects.filter(user_id=opts["user_id"]).select_related("user").first()
        if wallet is None:
            raise CommandError("No wallet for that customer; nothing changed")

        original = None
        if linked:
            original = Transaction.objects.filter(user_id=wallet.user_id, reference=original_ref).first()
            if original is None:
                raise CommandError("That reference is not on this customer's ledger; nothing changed")
            if kind == "reversed-deposit" and not (
                    original.direction == Transaction.IN
                    and original.transaction_status == Transaction.SUCCESS
                    and str(original.service or "").startswith("Wallet funding")):
                raise CommandError("Only a successful 'Wallet funding' credit can be reversed; nothing changed")
            if kind == "charged-failed-purchase" and not (
                    original.direction == Transaction.OUT
                    and original.transaction_status == Transaction.FAILED):
                raise CommandError("Only a Failed purchase can be recorded as charged; nothing changed")
            if original.amount != amount:
                raise CommandError(f"The amount must equal the original row (₦{original.amount}); nothing changed")

        # A linked correction is keyed on the row it corrects, so it posts once
        # however it is worded. Bank charges are keyed on the reference given.
        key = _digest(original_ref if linked else f"{amount}|{note}")
        reference = f"STMTFIX-{code}-{wallet.user_id}-{key}"
        if kind == "reversed-deposit":
            service = "Deposit reversed by bank"
        elif kind == "charged-failed-purchase":
            service = f"{original.service} (charged by bank)"[:80]
        else:
            service = "Bank charges"
        summary = {"user_id": wallet.user_id, "kind": kind, "amount": str(amount),
                   "service": service, "original_reference": original_ref or None,
                   "balance_before": str(wallet.balance),
                   "balance_after": str(wallet.balance - amount), "reference": reference}
        if Transaction.objects.filter(reference=reference).exists():
            self.stdout.write(json.dumps({**summary, "already_posted": True}))
            return
        if not opts["apply"]:
            self.stdout.write(json.dumps({**summary, "dry_run": True}))
            return
        meta = {"statement_correction": kind, "operator_reference": note}
        if original is not None:
            meta["corrects"] = original.reference
        try:
            with transaction.atomic():
                row = debit(wallet.user, amount, service, meta=meta,
                            reference=reference, enforce_limits=False)
                row.transaction_status = Transaction.SUCCESS
                row.save(update_fields=["transaction_status"])
                record_audit("wallet.statement_correction", actor_type="system",
                             target=f"user:{wallet.user_id}",
                             before={"balance": summary["balance_before"]},
                             after={"kind": kind, "amount": str(amount), "reference": reference,
                                    "corrects": original_ref or None, "operator_reference": note})
        except InsufficientFunds as exc:
            raise CommandError("The balance is below the correction; nothing changed") from exc
        except IntegrityError:
            self.stdout.write(json.dumps({**summary, "already_posted": True}))
            return
        wallet.refresh_from_db()
        self.stdout.write(json.dumps({
            **summary, "posted": True, "balance_after": str(wallet.balance),
            "ledger_agrees": wallet_expected_balance(wallet.user_id) == wallet.balance}))
