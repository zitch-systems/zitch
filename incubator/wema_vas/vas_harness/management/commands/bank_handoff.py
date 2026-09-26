"""Produce a Wema endpoint checklist without exposing bank authentication secrets."""
import json
from urllib.parse import urlsplit

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ImproperlyConfigured

from ...identity import decrypt_identity
from ...models import VirtualAccount


class Command(BaseCommand):
    help = "Describe the five HTTPS URLs and three approved 711 samples; never prints Bearer credentials."

    def add_arguments(self, parser):
        parser.add_argument("--base-url", required=True)

    def handle(self, *args, **options):
        if settings.VAS_MODE != "validation" or not settings.VAS_ENABLED:
            raise CommandError("The isolated validation bank endpoints must be explicitly enabled")
        # Wema validates three temporary 711 accounts before assigning the live
        # prefix. Never export samples that the currently running API rejects.
        if settings.VAS_PREFIX != "711":
            raise CommandError("The initial Wema handoff requires the 711 test prefix")
        parsed = urlsplit(options["base_url"])
        if (parsed.scheme != "https" or not parsed.hostname or parsed.hostname not in settings.ALLOWED_HOSTS
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.username or parsed.password):
            raise CommandError("The HTTPS base URL must exactly match an allowed validation service hostname")
        samples = list(VirtualAccount.objects.filter(
            mode="validation", active=True, number__startswith="711", verified_at__isnull=False
        ).order_by("number")[:3])
        if len(samples) != 3:
            raise CommandError("Three approved and verified 711 sample accounts are required")
        for account in samples:
            if (not account.customer_reference or not account.display_name.startswith("Zitch/")
                    or not account.verification_reference
                    or not account.consent_reference or not account.encrypted_identity):
                raise CommandError("A sample account lacks verification or consent evidence")
            try:
                decrypt_identity(account.encrypted_identity)
            except ImproperlyConfigured as exc:
                raise CommandError("A sample account's encrypted identity is unavailable") from exc
        numbers = [account.number for account in samples]
        base = options["base_url"].rstrip("/")
        routes = ("account-lookup", "transaction-notification", "mini-statement", "kyc-details", "block-account")
        self.stdout.write(json.dumps({
            "base_url": base, "account_type": "Static", "sample_accounts": numbers,
            "endpoints": {route: base + "/vas/" + route for route in routes},
            "note": "Technical URL checklist only. Verify identities and settlement access; supply the token through the approved bank channel.",
        }, sort_keys=True))
