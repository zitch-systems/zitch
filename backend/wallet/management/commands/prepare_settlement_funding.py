"""Back a customer's existing Zitch balance with money in their Wema account.

A balance the ledger already owes (for example one carried over from a retired
provider) must sit in the customer's own Partnership account for settlement to
pass. Zitch pays it in by ordinary bank transfer, but an ordinary deposit is
credited to the ledger, which would pay the customer twice. This command issues
a one-time token: quote it in the transfer narration and the deposit sweep
matches it (exact amount required) without crediting the ledger again.

Read-only by default: prints what each wallet is short. ``--apply`` issues the
token for one customer. Output carries user ids and account suffixes only.

Usage:
    python manage.py prepare_settlement_funding
    python manage.py prepare_settlement_funding --user-id 34 --apply \\
        --operator-reference "settle 9PSB-era balance, approved by <name>"
"""
import secrets
import string
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from utility import wema
from wallet.models import SettlementFunding, Wallet

_ALPHABET = string.ascii_uppercase + string.digits


def _shortfall(wallet):
    pending = sum((r.amount for r in SettlementFunding.objects.filter(
        wallet=wallet, account_number=wallet.account_number,
        status=SettlementFunding.PENDING)), Decimal("0"))
    read = wema.get_balance(wallet.account_number)
    if not read.get("success") or read.get("balance_naira") is None:
        return None, pending
    held = Decimal(str(read["balance_naira"]))
    return max(Decimal("0"), wallet.balance - held - pending), pending


class Command(BaseCommand):
    help = "Issue a settlement token so Zitch can back a customer's balance without double-crediting."

    def add_arguments(self, parser):
        parser.add_argument("--user-id", type=int)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--operator-reference", default="")

    def handle(self, *args, **opts):
        if not wema.wema_live():
            raise CommandError("Wema is not live; balances cannot be read.")
        wallets = Wallet.objects.filter(balance__gt=0).order_by("user_id")
        if opts["user_id"]:
            wallets = wallets.filter(user_id=opts["user_id"])
        for wallet in wallets:
            if not wallet.account_number:
                self.stdout.write(f"user {wallet.user_id}: ledger {wallet.balance} - NO WEMA ACCOUNT; "
                                  "open one first (customer replies 6).")
                continue
            short, pending = _shortfall(wallet)
            if short is None:
                self.stdout.write(f"user {wallet.user_id}: Wema balance unreadable; try again.")
                continue
            self.stdout.write(f"user {wallet.user_id}: ledger {wallet.balance}, short {short}, "
                              f"pending tokens {pending}, account ...{wallet.account_number[-4:]}")
            if not opts["apply"] or short <= 0:
                continue
            if not opts["user_id"]:
                raise CommandError("--apply needs --user-id.")
            if not opts["operator_reference"].strip():
                raise CommandError("--apply needs --operator-reference (who approved, why).")
            token = "ZSET" + "".join(secrets.choice(_ALPHABET) for _ in range(8))
            SettlementFunding.objects.create(
                wallet=wallet, account_number=wallet.account_number, amount=short,
                token=token, operator_reference=opts["operator_reference"].strip()[:160])
            from whatsapp.ops import record_audit
            record_audit("wallet.settlement_funding_issued", actor_type="operator",
                         after={"user_id": wallet.user_id, "amount": str(short), "token": token,
                                "account_suffix": wallet.account_number[-4:]})
            self.stdout.write(self.style.SUCCESS(
                f"Transfer EXACTLY {short} from Zitch's account to ...{wallet.account_number[-4:]} "
                f"with narration containing {token}. It will be matched, not credited again."))
