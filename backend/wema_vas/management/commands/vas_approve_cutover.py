"""Record reviewed bank cutover evidence; never move or reset money."""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import User
from wallet.models import Transaction, Wallet
from wema_vas.models import MigrationApproval, VirtualAccount


class Command(BaseCommand):
    help = "Record a reviewed zero-balance Partnership cutover; requires bank evidence and an operator reference."

    def add_arguments(self, parser):
        parser.add_argument("--user-id", required=True, type=int)
        parser.add_argument("--legacy-account", required=True)
        parser.add_argument("--evidence-reference", required=True)
        parser.add_argument("--reviewer-reference", required=True)

    @transaction.atomic
    def handle(self, *args, **options):
        if not options["evidence_reference"].strip() or not options["reviewer_reference"].strip():
            raise CommandError("Bank evidence and reviewer references are required.")
        if any(len(options[key]) > 160 for key in ("evidence_reference", "reviewer_reference")):
            raise CommandError("References must be at most 160 characters.")
        wallet = Wallet.objects.select_for_update().filter(user_id=options["user_id"]).first()
        if not wallet or not wallet.account_number or wallet.account_number != options["legacy_account"]:
            raise CommandError("The reviewed legacy account does not match the retained wallet.")
        if wallet.balance != 0 or Transaction.objects.filter(user_id=wallet.user_id, transaction_status=Transaction.PENDING).exists():
            raise CommandError("Reconcile the balance and pending transactions before recording approval.")
        if VirtualAccount.objects.filter(user_id=wallet.user_id).exists():
            raise CommandError("This user already has a VAS account; no retrospective approval is allowed.")
        if MigrationApproval.objects.filter(user_id=wallet.user_id).exists():
            raise CommandError("This user already has immutable approval evidence.")
        MigrationApproval.objects.create(user_id=wallet.user_id,
            legacy_account_number=wallet.account_number,
            reference=options["evidence_reference"].strip(), approved_by=options["reviewer_reference"].strip())
        self.stdout.write(self.style.SUCCESS("Cutover evidence recorded. No balance, bank account or KYC status changed."))
