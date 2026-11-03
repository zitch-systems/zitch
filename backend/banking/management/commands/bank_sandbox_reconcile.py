"""Record a sandbox tester's bank balance in Zitch when no per-credit evidence exists.

The 9PSB sandbox sends no inflow notification for a float credit and returns an
empty transaction history, so a credit the bank applied but Zitch did not record
leaves no statement line to reconcile against. The bank's own balance enquiry is
then the only evidence there is. Credit the shortfall from that enquiry, and
record on the ledger row that this is what the credit was built from.
"""
import json
import secrets
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from banking.accounts import account_for, sandbox_tester, update_account
from banking.client import ProviderError, customer_enabled, sandbox
from banking.readiness import find_tester
from wallet.models import Transaction, Wallet
from wallet.services import credit


class Command(BaseCommand):
    help = ("Sandbox only: credit a tester's Zitch wallet up to the balance the bank confirms, "
            "for test funding the bank applied but Zitch never recorded. Reports without applying "
            "unless --apply is given.")

    def add_arguments(self, parser):
        parser.add_argument("--phone", required=True, help="The tester's verified phone number")
        parser.add_argument("--apply", action="store_true",
                            help="Write the credit. Without it the shortfall is only reported.")

    def handle(self, *args, **options):
        if not sandbox() or not customer_enabled():
            raise CommandError("Balance reconciliation is available only with the bank sandbox enabled")
        try:
            user = find_tester(mobile=options["phone"])
        except ValueError:
            raise CommandError("Enter a valid Nigerian mobile number") from None
        if user is None:
            raise CommandError("No Zitch user matches; register in the app first")
        if not sandbox_tester(user):
            raise CommandError("This user is not an enrolled sandbox tester")
        bank = account_for(user)
        if not bank or bank.status != "active":
            raise CommandError("This tester has no active bank account yet")

        # Never reconcile against a stored balance. A credit written from a stale
        # figure is indistinguishable from one the bank never made.
        try:
            update_account(bank)
            bank.refresh_from_db()
        except ProviderError as exc:
            raise CommandError(f"The bank did not confirm the balance ({exc.code}); nothing was "
                               f"recorded. Reconciliation needs a fresh enquiry.") from None

        reference = "ZSR" + secrets.token_hex(8).upper()
        with transaction.atomic():
            wallet = Wallet.objects.select_for_update().get(pk=bank.wallet_id)
            confirmed = Decimal(bank.available_balance)
            shortfall = confirmed - wallet.balance
            report = {"reference": reference, "bank_available_balance": str(confirmed),
                      "zitch_balance": str(wallet.balance), "shortfall": str(shortfall)}
            if shortfall < 0:
                # Zitch ahead of the bank is a different fault with a different
                # remedy. This command only ever adds what the bank confirms.
                self.stdout.write(json.dumps({**report, "applied": False,
                    "reason": "zitch_ahead_of_bank"}, sort_keys=True))
                raise CommandError("Zitch records more than the bank confirms; this needs review, "
                                   "and no debit is made here")
            if shortfall == 0 or not options["apply"]:
                self.stdout.write(json.dumps({**report, "applied": False,
                    "reason": "already_in_line" if shortfall == 0 else "dry_run"}, sort_keys=True))
                return
            ledger = credit(user, shortfall, "Sandbox balance reconciliation", reference=reference,
                meta={"provider": "9psb", "environment": bank.environment,
                      "account_number": bank.number, "sandbox_balance_reconciliation": reference,
                      "evidence": "bank_balance_enquiry", "bank_available_balance": str(confirmed),
                      "zitch_balance_before": str(wallet.balance)})
        self.stdout.write(json.dumps({**report, "applied": True, "transaction_id": ledger.pk,
            "nuban_last4": bank.number[-4:]}, sort_keys=True))
