"""Provision one dedicated verified test user without identifiers in argv/logs."""
from getpass import getpass

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.management.base import BaseCommand, CommandError

from accounts.models import User
from wema_vas.enrollment import enroll_verified


class Command(BaseCommand):
    help = "Allocate a 711 validation account for a dedicated verified user, after explicit identity storage consent."

    def add_arguments(self, parser):
        parser.add_argument("--user-id", required=True, type=int)
        parser.add_argument("--identity-type", choices=("bvn", "nin"), required=True)
        parser.add_argument("--consent-reference", required=True,
                            help="Reference to the test user's explicit consent to encrypted storage and Wema sharing.")

    def handle(self, *args, **options):
        consent = options["consent_reference"].strip()
        if not consent or len(consent) > 160:
            raise CommandError("A consent evidence reference (at most 160 characters) is required.")
        try:
            user = User.objects.get(pk=options["user_id"])
            raw = getpass("Already verified identifier (hidden): ")
            account = enroll_verified(user, consent=True, validation=True,
                consent_reference=consent, **{options["identity_type"]: raw})
        except User.DoesNotExist:
            raise CommandError("User does not exist.") from None
        except (ValidationError, ImproperlyConfigured) as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(f"Validation account: {account.number}; no spendable balance created.")
