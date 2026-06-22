"""Monnify connectivity self-test for ops.

Run it on the server (e.g. Render's Shell) to see *exactly* why Monnify calls
fail — it turns a "nothing works" report into a precise, one-line fix:

    python manage.py monnify_check
    python manage.py monnify_check --account 0123456789 --bank 058

It reports the configured keys/base-URL, whether the OAuth login actually
succeeds, and what a sample name-enquiry returns (with Monnify's real message).
Reads only booleans for secrets — it never prints a key.
"""
from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Diagnose Monnify connectivity: config, OAuth login, and a sample name-enquiry."

    def add_arguments(self, parser):
        parser.add_argument("--account", default="0000000000",
                            help="A real 10-digit account number to test the name enquiry with.")
        parser.add_argument("--bank", default="058",
                            help="NIBSS bank code for the test account (default 058 = GTBank).")

    def handle(self, *args, **opts):
        from utility import providers as P

        m = settings.MONNIFY
        live_url = "api.monnify.com" in m["BASE_URL"]

        def row(k, v):
            self.stdout.write(f"  {k:<22} {v}")

        self.stdout.write(self.style.MIGRATE_HEADING("Monnify configuration"))
        row("BASE_URL", m["BASE_URL"] + ("  (LIVE)" if live_url else "  (TEST/sandbox)"))
        row("API_KEY set", bool(m["API_KEY"]))
        row("SECRET_KEY set", bool(m["SECRET_KEY"]))
        row("CONTRACT_CODE set", bool(m["CONTRACT_CODE"]))
        row("SOURCE_ACCOUNT set", bool(m["SOURCE_ACCOUNT"]) or "(needed for payouts)")
        row("payments_live()", P.payments_live())

        if not P.payments_live():
            self.stdout.write(self.style.ERROR(
                "\nFAIL: keys incomplete. Set MONNIFY_API_KEY, MONNIFY_SECRET_KEY and "
                "MONNIFY_CONTRACT_CODE in the environment. Until all three are set, every "
                "Monnify call (add money, transfers, name lookup) fails."))
            return

        self.stdout.write(self.style.MIGRATE_HEADING("\nOAuth login (/api/v1/auth/login)"))
        token = P._monnify_token()
        if token:
            self.stdout.write(self.style.SUCCESS(f"  OK — access token acquired ({token[:6]}…)"))
        else:
            self.stdout.write(self.style.ERROR("  FAIL — no access token returned."))
            self.stdout.write(
                "  This breaks ALL Monnify features at once, and is almost always a\n"
                f"  base-URL / keys mismatch. Your BASE_URL is {'LIVE' if live_url else 'TEST/sandbox'}\n"
                f"  ({m['BASE_URL']}) — make sure the keys are the matching pair:\n"
                "    • LIVE keys  -> set MONNIFY_BASE_URL=https://api.monnify.com\n"
                "    • TEST keys  -> set MONNIFY_BASE_URL=https://sandbox.monnify.com\n"
                "  (The default is sandbox, so live keys with no MONNIFY_BASE_URL fail here.)")
            return

        self.stdout.write(self.style.MIGRATE_HEADING(
            "\nSample name-enquiry (/api/v1/disbursements/account/validate)"))
        res = P.disbursement_resolve_account(opts["account"], opts["bank"])
        if res.get("success"):
            self.stdout.write(self.style.SUCCESS(f"  OK — resolved: {res.get('name')}"))
            self.stdout.write("\nAll green. If users still report issues, capture the exact in-app "
                              "error and the server logs (monnify_reserve_failed / monnify_resolve_*).")
        else:
            raw = res.get("raw") or {}
            self.stdout.write(self.style.WARNING(
                f"  Not resolved. Monnify: code={raw.get('responseCode')!r} "
                f"message={raw.get('responseMessage') or res.get('message')!r}"))
            self.stdout.write(
                "  Auth works but this failed. If the account+bank are valid, the\n"
                "  Disbursement/Transfer product is most likely not enabled on your\n"
                "  contract (it powers both name lookup AND bank transfers). Enable it\n"
                "  in the Monnify dashboard, or contact Monnify support. Reserved\n"
                "  accounts (add money) are a separate product — confirm it's on too.")
