"""Kora (Korapay) connectivity self-test for ops (shell version).

Run it on the server to see *exactly* why Kora calls fail:

    python manage.py kora_check
    python manage.py kora_check --account 0123456789 --bank 058

Reads only booleans for secrets — it never prints a key.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Diagnose Kora connectivity: config, auth (balances read), and a sample name-enquiry."

    def add_arguments(self, parser):
        parser.add_argument("--account", default="0000000000",
                            help="A real 10-digit account number to test the name enquiry with.")
        parser.add_argument("--bank", default="058",
                            help="Bank code for the test account (default 058 = GTBank).")

    def handle(self, *args, **opts):
        from utility.kora import kora_diagnostics

        d = kora_diagnostics(opts["account"], opts["bank"])

        def row(k, v):
            self.stdout.write(f"  {k:<22} {v}")

        self.stdout.write(self.style.MIGRATE_HEADING("Kora configuration"))
        row("BASE_URL", d["base_url"])
        row("SECRET_KEY set", d["secret_key_set"])
        row("PUBLIC_KEY set", d["public_key_set"])
        row("kora_live", d["kora_live"])

        if d["status"] == "keys_incomplete":
            self.stdout.write(self.style.ERROR(f"\nFAIL — keys incomplete. {d['hint']}"))
            return

        self.stdout.write(self.style.MIGRATE_HEADING("\nAuth (GET /api/v1/balances)"))
        if not d.get("auth_ok"):
            self.stdout.write(self.style.ERROR("  FAIL — balances read did not authenticate."))
            self.stdout.write("  " + d["hint"])
            return
        self.stdout.write(self.style.SUCCESS("  OK — authenticated."))

        self.stdout.write(self.style.MIGRATE_HEADING(
            "\nSample name-enquiry (POST /api/v1/misc/banks/resolve)"))
        se = d["sample_enquiry"]
        if se["resolved"]:
            self.stdout.write(self.style.SUCCESS(f"  OK — resolved: {se['name']}"))
            self.stdout.write("\n" + d["hint"])
        else:
            self.stdout.write(self.style.WARNING(f"  Not resolved. Kora: {se['message']!r}"))
            self.stdout.write("  " + d["hint"])
