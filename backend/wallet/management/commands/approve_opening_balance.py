"""Lift the opening-balance spend hold on a reattached account, on evidence.

An account attached from a record the bank already held (for example after
``existing_customer`` reconnects a detached account) starts with
``BankHistoryCheckpoint.opening_review_required``: the account existed before
this wallet, so its balance is not yet proven to be the customer's ledger, and
spending is held. Reconciliation never clears it on its own. This command is the
review, and it clears the hold only when:

* the bank answers live (not simulated) with a balance for this exact account,
* that balance equals the customer's ledger to the kobo, checked again under
  the wallet lock so a deposit landing mid-check is not approved unseen,
* the stored wallet balance equals that ledger,
* no transaction is Pending,
* an operator reference names who approved it and why.

Nothing else changes: no credit, no debit, no statement coverage. Dry run by
default; ``--apply`` clears and writes one audit row carrying both balances.

    python manage.py approve_opening_balance --user-id 30 \\
        --operator-reference "ledger matches Wema balance, approved by <name>"
    python manage.py approve_opening_balance --user-id 30 \\
        --operator-reference "ledger matches Wema balance, approved by <name>" --apply
"""
import json

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from utility import wema
from wallet.models import BankHistoryCheckpoint, Transaction, Wallet
from wallet.services import wallet_expected_balance
from whatsapp.ops import record_audit


class Command(BaseCommand):
    help = ("Clear one wallet's opening-balance spend hold when the bank balance equals "
            "its ledger. Dry run by default.")

    def add_arguments(self, parser):
        parser.add_argument("--user-id", type=int, required=True)
        parser.add_argument("--operator-reference", default="",
                            help="Who approved, and why")
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **opts):
        note = (opts["operator_reference"] or "").strip()
        if not note or len(note) > 160 or any(ord(char) < 32 for char in note):
            raise CommandError("A bounded --operator-reference (who approved, and why) is "
                               "required; nothing changed")
        user_id = opts["user_id"]
        wallet = Wallet.objects.filter(user_id=user_id).first()
        if wallet is None or not wallet.account_number:
            raise CommandError("That wallet has no bank account attached; nothing changed")
        if not wema.wema_live():
            raise CommandError("The bank rail is not live, so there is no balance to compare; "
                               "nothing changed")
        account = wallet.account_number
        checkpoint = BankHistoryCheckpoint.objects.filter(
            wallet=wallet, account_number=account).first()
        if checkpoint is not None and not checkpoint.opening_review_required:
            self.stdout.write(json.dumps({"user_id": user_id, "already_cleared": True}))
            return
        if Transaction.objects.filter(user_id=user_id,
                                      transaction_status=Transaction.PENDING).exists():
            raise CommandError("This wallet has a Pending transaction; wait for it to settle. "
                               "Nothing changed")

        read = wema.get_balance(account)
        bank = read.get("balance_naira") if read.get("success") and not read.get("mock") else None
        if bank is None:
            raise CommandError("The bank did not return a live balance for this account; "
                               "nothing changed")
        ledger = wallet_expected_balance(user_id)
        summary = {"user_id": user_id, "account_suffix": account[-4:], "ledger": str(ledger),
                   "bank": str(bank), "wallet_balance": str(wallet.balance)}
        if ledger != bank:
            raise CommandError(f"The ledger ({ledger}) does not equal the bank balance ({bank}); "
                               "reconcile the difference first. Nothing changed")
        if wallet.balance != ledger:
            raise CommandError(f"The stored balance ({wallet.balance}) does not equal the ledger "
                               f"({ledger}); run integrity_check first. Nothing changed")
        if not opts["apply"]:
            self.stdout.write(json.dumps({**summary, "dry_run": True}))
            return

        with transaction.atomic():
            locked = Wallet.objects.select_for_update().get(pk=wallet.pk)
            if (locked.account_number != account or locked.balance != bank
                    or wallet_expected_balance(user_id) != bank):
                raise CommandError("The wallet changed during the check; run it again. "
                                   "Nothing changed")
            checkpoint, _ = BankHistoryCheckpoint.objects.select_for_update().get_or_create(
                wallet=locked, account_number=account)
            if not checkpoint.opening_review_required:
                self.stdout.write(json.dumps({"user_id": user_id, "already_cleared": True}))
                return
            checkpoint.opening_review_required = False
            checkpoint.save(update_fields=["opening_review_required", "updated"])
            record_audit("wallet.opening_balance_approved", actor_type="operator",
                         target=f"user:{user_id}",
                         before={"opening_review_required": True},
                         after={**summary, "operator_reference": note})
        self.stdout.write(json.dumps({**summary, "cleared": True}))
