"""Sandbox only: attach a test wallet the bank already opened for a tester."""
import json
import re

from django.core.management.base import BaseCommand, CommandError

from banking.accounts import account_for, complete_account, sandbox_tester
from banking.client import ProviderError, customer_enabled, sandbox
from banking.models import IdentityChallenge
from banking.readiness import find_tester


class Command(BaseCommand):
    help = ("Sandbox only: when the test bank says 'A Wallet Already Exists' for a tester, attach "
            "that wallet by its account number to the tester's latest verified identity check.")

    def add_arguments(self, parser):
        parser.add_argument("--phone", required=True, help="The tester's verified phone number")
        parser.add_argument("--account", required=True, help="The 10-digit test wallet account number")

    def handle(self, *args, **options):
        if not sandbox() or not customer_enabled():
            raise CommandError("This is available only with the bank sandbox enabled")
        number = options["account"].strip()
        if not re.fullmatch(r"[0-9]{10}", number):
            raise CommandError("Enter a 10-digit account number")
        try:
            user = find_tester(mobile=options["phone"])
        except ValueError:
            raise CommandError("Enter a valid Nigerian mobile number") from None
        if user is None or not sandbox_tester(user):
            raise CommandError("No enrolled sandbox tester matches that phone number")
        if account_for(user):
            raise CommandError("This tester already has a bank account attached")
        challenge = (IdentityChallenge.objects.filter(user=user, verified_at__isnull=False,
                     state__in=["verified", "opening"]).exclude(encrypted_input="").order_by("-created").first())
        if not challenge:
            raise CommandError("Verify the BVN in the app first (send and enter the code), then run this again")
        try:
            result = complete_account(challenge, known_number=number)
        except (ProviderError, ValueError) as exc:
            raise CommandError(f"Not attached: {getattr(exc, 'code', exc)}") from None
        attached = account_for(user)
        self.stdout.write(json.dumps({"attached": bool(attached), "nuban_last4": attached.number[-4:] if attached else "",
            "message": str(result.get("message", ""))[:160]}, sort_keys=True))
        if not attached:
            raise CommandError("The bank did not confirm that account for this tester")
