"""Generate the non-secret portion of Wema's validation submission."""
import json
from urllib.parse import urlsplit

from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import DatabaseError

from wema_vas.config import config, validate_configuration
from .vas_preflight import sample_readiness


class Command(BaseCommand):
    help = "Print a non-secret onboarding package for exactly three existing 711 accounts. Does not send messages."

    def add_arguments(self, parser):
        parser.add_argument("--base-url", required=True)
        parser.add_argument("--service-email", required=True)
        parser.add_argument("--account", action="append", required=True)

    def handle(self, *args, **options):
        if not config().get("ENABLED") or config().get("MODE") != "validation":
            raise CommandError("Enable validation mode before preparing the 711 submission.")
        try:
            validate_configuration()
        except (ImproperlyConfigured, TypeError, ValueError):
            raise CommandError("Validation authentication, encryption or database configuration is incomplete.") from None
        base = options["base_url"].rstrip("/")
        parsed = urlsplit(base)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
            raise CommandError("An HTTPS origin without credentials, path or query is required.")
        try:
            validate_email(options["service_email"])
        except ValidationError:
            raise CommandError("A valid service email is required.") from None
        numbers = options["account"]
        if len(set(numbers)) != 3 or len(numbers) != 3:
            raise CommandError("Supply exactly three distinct validation accounts.")
        try:
            rows, ready = sample_readiness(numbers, mode="validation", prefix="711")
        except DatabaseError:
            raise CommandError("Validation sample evidence is temporarily unavailable.") from None
        if len(rows) != 3 or not ready:
            raise CommandError("Samples need active users, active 711 accounts and matching verified identity/consent evidence.")
        self.stdout.write(json.dumps({
            "vendor": "Zitch", "account_type": "static", "base_url": base,
            "service_email": options["service_email"], "sample_accounts": sorted(numbers),
            "endpoints": {name: base + "/vas/" + path for name, path in {
                "Account Lookup": "account-lookup", "Transaction Notification": "transaction-notification",
                "Mini Statement": "mini-statement", "KYC Details": "kyc-details", "Block Account": "block-account"}.items()},
            "authentication": "Bearer token delivered separately through an approved secure channel; never in this file.",
            "validation_notice": "711 notifications are validation receipts, never spendable customer credits.",
        }, indent=2))
