"""Fund a sandbox tester's bank wallet from the Zitch float, for testing only."""
import json
import re
import secrets
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from banking.accounts import account_for, sandbox_tester, update_account
from banking.client import Client, ProviderError, customer_enabled, money, sandbox
from banking.readiness import find_tester
from wallet.models import Transaction, Wallet
from wallet.services import credit

# Tier 1's single-deposit limit. Larger sandbox credits would be refused or put
# the test wallet under a restriction that hides what is being tested.
MAXIMUM = Decimal("50000.00")


def _code(value):
    """A bounded machine code only; never an echo of the bank's free text."""
    text = str(value if value is not None else "").strip().upper()
    return text if re.fullmatch(r"[A-Z0-9]{1,4}", text) else ""


def _word(value):
    text = str(value if value is not None else "").strip().upper()
    return text if re.fullmatch(r"[A-Z_]{1,24}", text) else ""


class Command(BaseCommand):
    help = ("Sandbox only: credit an enrolled tester's bank wallet from the Zitch float and "
            "record the deposit in Zitch, so test payments have a balance to spend.")

    def add_arguments(self, parser):
        parser.add_argument("--phone", required=True, help="The tester's verified phone number")
        parser.add_argument("--amount", required=True, help="Naira amount, up to 50,000")

    def handle(self, *args, **options):
        if not sandbox() or not customer_enabled():
            raise CommandError("Test funding is available only with the bank sandbox enabled")
        try:
            amount = Decimal(money(options["amount"]))
        except (InvalidOperation, ValueError):
            raise CommandError("Enter an amount such as 5000") from None
        if amount <= 0 or amount > MAXIMUM:
            raise CommandError("Enter an amount from 1 to 50,000")
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
            raise CommandError("This tester has no active bank account yet; finish account setup first")

        reference = "ZSF" + secrets.token_hex(8).upper()
        known_balance = bank.available_balance
        try:
            reply = Client().float_transfer("credit", reference, amount, bank.number, "Zitch sandbox test funding")
        except ProviderError as exc:
            # A lost reply to a submitted credit is not a refusal: the bank may
            # have applied it. Only a failure raised before the instruction left
            # (authentication, for one) establishes that no money moved.
            if exc.uncertain:
                raise self._unresolved(bank, reference, amount, exc.code, known_balance) from None
            raise CommandError(f"The bank did not accept the credit ({exc.code}); no money moved and nothing "
                               f"was recorded. Reference {reference} went unused; retry when the bank answers.") from None
        state = reply.payment_state()
        if state != "success":
            message = str(reply.body.get("message") or reply.body.get("responseMessage") or "")[:160]
            # An unconfirmed credit is decided by the reply's codes, not its
            # message. Report the bounded machine codes too, so an undecided
            # reply can be diagnosed without sending the credit a second time.
            data = reply.data if isinstance(reply.data, dict) else {}
            self.stdout.write(json.dumps({"state": state, "reference": reference, "bank_message": message,
                "http_status": reply.http_status,
                "response_code": _code(reply.body.get("responseCode", reply.body.get("statusCode"))),
                "bank_status": _word(reply.body.get("status")),
                "data_response_code": _code(data.get("responseCode")),
                "data_status": _word(data.get("transactionStatus", data.get("status"))),
                "recorded_in_zitch": False}, sort_keys=True))
            if state == "failed":
                raise CommandError("The bank refused the credit; nothing was recorded")
            raise CommandError("The bank has not confirmed the credit yet; nothing was recorded in Zitch. "
                               "Do not fund again; check bank_sandbox_reconcile first.")

        # The sandbox may deduct a charge from the credited amount. Record no
        # more than the requested credit or the fresh, bank-backed ledger gap.
        # An approved instruction with an unavailable/unchanged balance must
        # be reconciled later, never retried as another float credit.
        try:
            update_account(bank)
            bank.refresh_from_db()
            if bank.available_balance is None:
                raise ProviderError("account_balance_unconfirmed")
        except ProviderError as exc:
            self.stdout.write(json.dumps({"state": "success", "reference": reference,
                "requested_amount": str(amount), "recorded_in_zitch": False,
                "provider_error": exc.code}, sort_keys=True))
            raise CommandError("The bank approved the credit but its balance could not be confirmed. "
                               "Do not fund again; use bank_sandbox_reconcile to record the confirmed balance.") from None
        with transaction.atomic():
            wallet = Wallet.objects.select_for_update().get(pk=bank.wallet_id)
            backed_amount = min(amount, bank.available_balance - wallet.balance)
            ledger = Transaction.objects.filter(reference="9PSF" + reference).first()
            if ledger is None and backed_amount > 0:
                ledger = credit(user, backed_amount, "Test funding", reference="9PSF" + reference,
                meta={"provider": "9psb", "environment": bank.environment, "account_number": bank.number,
                      "sandbox_float_credit": reference, "requested_amount": str(amount),
                      "evidence": "bank_credit_and_balance_enquiry",
                      "bank_available_balance": str(bank.available_balance)})
        if ledger is None:
            self.stdout.write(json.dumps({"state": "success", "reference": reference,
                "requested_amount": str(amount), "recorded_in_zitch": False,
                "bank_available_balance": str(bank.available_balance)}, sort_keys=True))
            raise CommandError("The bank approved the credit but no additional balance is confirmed for Zitch. "
                               "Do not fund again; check bank_sandbox_reconcile.")
        self.stdout.write(json.dumps({"state": "success", "reference": reference, "amount": str(ledger.amount),
            "requested_amount": str(amount),
            "recorded_in_zitch": True, "transaction_id": ledger.pk, "nuban_last4": bank.number[-4:],
            "bank_available_balance": str(bank.available_balance)}, sort_keys=True))

    def _unresolved(self, bank, reference, amount, code, known_balance):
        """Describe a credit the bank never confirmed, without calling it failed.

        The amount may already be in the tester's wallet. Funding again would
        move it twice from the float, so report both balances and stop.
        """
        try:
            update_account(bank)
            bank.refresh_from_db()
            balance = str(bank.available_balance)
        except ProviderError:
            balance = "not refreshed"
        self.stdout.write(json.dumps({"state": "unknown", "reference": reference, "amount": str(amount),
            "provider_error": code, "recorded_in_zitch": False, "last_known_balance": str(known_balance),
            "bank_available_balance": balance}, sort_keys=True))
        return CommandError(
            f"The bank's reply was lost ({code}); the credit is unconfirmed and may already have been applied, "
            f"so nothing was recorded in Zitch. Do not fund this tester again until you know which: compare the "
            f"balances above and use bank_sandbox_reconcile to record a bank-confirmed shortfall. "
            f"Reference {reference}")
