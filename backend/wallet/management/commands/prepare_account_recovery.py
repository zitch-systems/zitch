from django.core.management.base import BaseCommand, CommandError

from accounts.models import User
from wallet.account_recovery import prepare_account_recovery


class Command(BaseCommand):
    help = "Review one stale face issuance; --approve authorizes one fresh customer face check."

    def add_arguments(self, parser):
        parser.add_argument("--user-id", type=int, required=True)
        parser.add_argument("--identity-type", choices=["bvn", "nin"], required=True)
        parser.add_argument("--operator-reference", required=True)
        parser.add_argument("--approve", action="store_true")

    def handle(self, *args, **options):
        try:
            session, created = prepare_account_recovery(
                options["user_id"], kind=options["identity_type"],
                operator_reference=options["operator_reference"], approve=options["approve"])
        except (ValueError, User.DoesNotExist) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            f"user_id={options['user_id']} session_id={session.pk} "
            f"recovery_authorized={session.is_account_recovery} created={created} "
            f"expires_at={session.expires_at.isoformat()} "
            "bank_creation_submitted=False")
