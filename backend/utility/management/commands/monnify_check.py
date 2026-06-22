"""Monnify connectivity self-test for ops (shell version).

Run it on the server to see *exactly* why Monnify calls fail:

    python manage.py monnify_check
    python manage.py monnify_check --account 0123456789 --bank 058

No shell? The same report is available in a browser at /monnify-diagnose once
MONNIFY_DIAG_TOKEN is set (see that endpoint). Reads only booleans for secrets —
it never prints a key.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Diagnose Monnify connectivity: config, OAuth login, and a sample name-enquiry."

    def add_arguments(self, parser):
        parser.add_argument("--account", default="0000000000",
                            help="A real 10-digit account number to test the name enquiry with.")
        parser.add_argument("--bank", default="058",
                            help="NIBSS bank code for the test account (default 058 = GTBank).")

    def handle(self, *args, **opts):
        from utility.providers import monnify_diagnostics

        d = monnify_diagnostics(opts["account"], opts["bank"])

        def row(k, v):
            self.stdout.write(f"  {k:<22} {v}")

        self.stdout.write(self.style.MIGRATE_HEADING("Monnify configuration"))
        row("BASE_URL", f"{d['base_url']}  ({d['base_url_kind']})")
        row("API_KEY set", d["api_key_set"])
        row("SECRET_KEY set", d["secret_key_set"])
        row("CONTRACT_CODE set", d["contract_code_set"])
        row("SOURCE_ACCOUNT set", d["source_account_set"])
        row("payments_live", d["payments_live"])

        if d["status"] == "keys_incomplete":
            self.stdout.write(self.style.ERROR(f"\nFAIL — keys incomplete. {d['hint']}"))
            return

        self.stdout.write(self.style.MIGRATE_HEADING("\nOAuth login (/api/v1/auth/login)"))
        if not d.get("auth_ok"):
            self.stdout.write(self.style.ERROR("  FAIL — no access token."))
            self.stdout.write("  " + d["hint"])
            return
        self.stdout.write(self.style.SUCCESS("  OK — access token acquired."))

        self.stdout.write(self.style.MIGRATE_HEADING(
            "\nSample name-enquiry (/api/v1/disbursements/account/validate)"))
        se = d["sample_enquiry"]
        if se["resolved"]:
            self.stdout.write(self.style.SUCCESS(f"  OK — resolved: {se['name']}"))
            self.stdout.write("\n" + d["hint"])
        else:
            self.stdout.write(self.style.WARNING(
                f"  Not resolved. Monnify: code={se['monnify_code']!r} "
                f"message={se['monnify_message']!r}"))
            self.stdout.write("  " + d["hint"])
