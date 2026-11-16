"""List customer balances with no Wema account behind them, and how to resolve each.

Read-only. For every wallet holding money without a partner-bank account it
prints the balance, where that money came from (wallet.unbacked) and the
reviewed command that resolves it. Output carries user ids and amounts only.

    python manage.py unbacked_balances
    python manage.py unbacked_balances --fail-nonzero   # exit 1 while any remain
"""
from decimal import Decimal

from django.core.management.base import BaseCommand

from wallet.unbacked import summary_line, unbacked_wallets


class Command(BaseCommand):
    help = "List balances no bank account holds, with their origin and the command that resolves each."

    def add_arguments(self, parser):
        parser.add_argument("--fail-nonzero", action="store_true")

    def handle(self, *args, **opts):
        rows = unbacked_wallets()
        if not rows:
            self.stdout.write(self.style.SUCCESS("No customer balance is without a Wema account."))
            return
        total = sum((r["balance"] for r in rows), Decimal("0"))
        self.stdout.write(f"{len(rows)} wallet(s) hold {total:.2f} with no Wema account:")
        for r in rows:
            self.stdout.write("  " + summary_line(r))
        if opts["fail_nonzero"]:
            raise SystemExit(1)
