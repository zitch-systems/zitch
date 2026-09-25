"""Produce a Wema endpoint checklist without exposing bank authentication secrets."""
import json
from urllib.parse import urlsplit

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ...models import VirtualAccount


class Command(BaseCommand):
    help = "Describe the five HTTPS URLs and three approved 711 samples; never prints Bearer credentials."

    def add_arguments(self, parser):
        parser.add_argument("--base-url", required=True)

    def handle(self, *args, **options):
        if settings.VAS_MODE != "validation" or not settings.VAS_ENABLED:
            raise CommandError("The isolated validation bank endpoints must be explicitly enabled")
        parsed = urlsplit(options["base_url"])
        if (parsed.scheme != "https" or not parsed.hostname or parsed.hostname not in settings.ALLOWED_HOSTS
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.username or parsed.password):
            raise CommandError("The HTTPS base URL must exactly match an allowed validation service hostname")
        numbers = list(VirtualAccount.objects.filter(
            mode="validation", active=True, number__startswith="711", verified_at__isnull=False
        ).exclude(encrypted_identity="").order_by("number").values_list("number", flat=True)[:3])
        if len(numbers) != 3:
            raise CommandError("Three approved and verified 711 sample accounts are required")
        base = options["base_url"].rstrip("/")
        routes = ("account-lookup", "transaction-notification", "mini-statement", "kyc-details", "block-account")
        self.stdout.write(json.dumps({
            "base_url": base, "account_type": "Static", "sample_accounts": numbers,
            "endpoints": {route: base + "/vas/" + route for route in routes},
            "note": "Technical URL checklist only. Verify identities and settlement access; supply the token through the approved bank channel.",
        }, sort_keys=True))
