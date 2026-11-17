"""Write off a test or staff balance that no bank account holds.

``settlement_report`` pages "customer balances with no Wema account behind them"
for every wallet holding money without a Wema account. When that money is real
customer money it is owed, and ``prepare_settlement_funding`` puts it into the
customer's new account. When the wallet belongs to a test or staff user (the
four wallets detached from 9PSB on 30 Oct 2026 were confirmed as such on
17 Nov 2026), nobody is owed it: the balance is a ledger entry with no money
behind it, and the only correct action is to take it off the ledger, on the
record, with who approved it.

This command does exactly that and nothing more:

* Only a wallet with NO Wema account and a positive balance qualifies. A wallet
  with an account is reconciled against its bank statement instead.
* ``--amount`` must equal the balance at the moment it runs, so a balance that
  moved since it was reviewed is not written off unseen.
* A wallet with a Pending transaction is refused until that settles.
* One audited debit for the whole balance, keyed so it posts once; the audit row
  names the operator reference and where the balance came from.

Without ``--user-id`` it lists every qualifying wallet and the exact command for
each. Dry run by default; ``--apply`` posts. Output carries user ids and amounts
only.

    python manage.py write_off_unbacked_balance
    python manage.py write_off_unbacked_balance --user-id 27 --amount 459.00 \\
        --operator-reference "test account, approved by <name>" --apply
"""
import hashlib
import json
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from wallet.models import Transaction, Wallet
from wallet.services import InsufficientFunds, debit, wallet_expected_balance
from wallet.unbacked import unbacked_wallets
from whatsapp.ops import record_audit

SERVICE = "Test balance written off"
REFERENCE_PREFIX = "WRITEOFF-UNBACKED"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:10].upper()


def _origins(entry: dict) -> dict:
    return {k: str(v) for k, v in sorted(entry["origins"].items())}


class Command(BaseCommand):
    help = ("Write off a test/staff balance held in a wallet with no Wema account. "
            "Lists candidates without --user-id; dry run by default.")

    def add_arguments(self, parser):
        parser.add_argument("--user-id", type=int)
        parser.add_argument("--amount", default="",
                            help="The wallet's current balance, exactly, e.g. 459.00")
        parser.add_argument("--operator-reference", default="",
                            help="Why and who approved, e.g. 'test account, approved by <name>'")
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **opts):
        rows = {r["user_id"]: r for r in unbacked_wallets()}
        if not opts["user_id"]:
            if not rows:
                self.stdout.write("No wallet holds a balance without a Wema account.")
            for user_id, entry in rows.items():
                self.stdout.write(json.dumps({
                    "user_id": user_id, "balance": str(entry["balance"]),
                    "origins": _origins(entry),
                    "command": (f"python manage.py write_off_unbacked_balance --user-id {user_id} "
                                f"--amount {entry['balance']:.2f} --operator-reference "
                                "'test account, approved by <name>' --apply")}))
            return

        note = (opts["operator_reference"] or "").strip()
        if not note or len(note) > 160 or any(ord(char) < 32 for char in note):
            raise CommandError("A bounded --operator-reference (who approved, and why) is "
                               "required; nothing changed")
        try:
            amount = Decimal(str(opts["amount"]))
        except InvalidOperation as exc:
            raise CommandError("--amount must be the wallet's balance, e.g. 459.00; "
                               "nothing changed") from exc
        if not amount.is_finite() or amount <= 0 or amount != amount.quantize(Decimal("0.01")):
            raise CommandError("--amount must be the wallet's balance, e.g. 459.00; nothing changed")

        user_id = opts["user_id"]
        wallet = Wallet.objects.filter(user_id=user_id).select_related("user").first()
        if wallet is None:
            raise CommandError("No wallet for that user; nothing changed")
        reference = f"{REFERENCE_PREFIX}-{user_id}-{_digest(f'{amount}|{note}')}"
        if Transaction.objects.filter(reference=reference).exists():
            self.stdout.write(json.dumps({"user_id": user_id, "reference": reference,
                                          "already_posted": True}))
            return
        entry = rows.get(user_id)
        if entry is None:
            raise CommandError("That wallet has a Wema account or no balance, so it is not "
                               "an unbacked balance; reconcile it against its bank statement "
                               "instead. Nothing changed")
        if wallet.balance != amount:
            raise CommandError(f"The balance is now {wallet.balance:.2f}, not {amount:.2f}; "
                               "review it again. Nothing changed")
        if Transaction.objects.filter(user_id=user_id,
                                      transaction_status=Transaction.PENDING).exists():
            raise CommandError("This wallet has a Pending transaction; wait for it to settle. "
                               "Nothing changed")

        summary = {"user_id": user_id, "amount": str(amount), "service": SERVICE,
                   "origins": _origins(entry), "balance_before": str(wallet.balance),
                   "balance_after": "0.00", "reference": reference}
        if not opts["apply"]:
            self.stdout.write(json.dumps({**summary, "dry_run": True}))
            return
        try:
            with transaction.atomic():
                row = debit(wallet.user, amount, SERVICE,
                            meta={"write_off": "unbacked_test_balance",
                                  "operator_reference": note, "origins": summary["origins"]},
                            reference=reference, enforce_limits=False)
                row.transaction_status = Transaction.SUCCESS
                row.save(update_fields=["transaction_status"])
                record_audit("wallet.unbacked_balance_written_off", actor_type="system",
                             target=f"user:{user_id}",
                             before={"balance": summary["balance_before"]},
                             after={"amount": str(amount), "reference": reference,
                                    "origins": summary["origins"], "operator_reference": note})
        except InsufficientFunds as exc:
            raise CommandError("The balance moved below the amount; nothing changed") from exc
        except IntegrityError:
            self.stdout.write(json.dumps({**summary, "already_posted": True}))
            return
        wallet.refresh_from_db()
        self.stdout.write(json.dumps({
            **summary, "posted": True, "balance_after": str(wallet.balance),
            "ledger_agrees": wallet_expected_balance(user_id) == wallet.balance}))
