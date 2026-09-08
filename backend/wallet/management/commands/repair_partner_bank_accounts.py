from django.core.management.base import BaseCommand, CommandError

from wallet.services import repair_missing_funding_accounts


class Command(BaseCommand):
    help = "Read back and attach missing partner-bank funding accounts for verified BVNs."

    def add_arguments(self, parser):
        parser.add_argument("--email", default="", help="Repair one customer by email address.")
        parser.add_argument("--limit", type=int, default=20, help="Maximum customers to check.")

    def handle(self, *args, **options):
        email = (options.get("email") or "").strip()
        if not email and int(options.get("limit") or 0) > 100:
            raise CommandError("Use --limit 100 or fewer for a bulk repair run.")
        result = repair_missing_funding_accounts(email=email, limit=options.get("limit") or 20)
        self.stdout.write(
            "partner-bank account repair: "
            f"checked={result['checked']} repaired={result['repaired']} failed={result['failed']}"
        )
