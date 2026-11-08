"""Reconcile our payout bank codes against the rail's OWN bank list.

`transfers.Bank.bank_code` was seeded from a NIBSS/Paystack mirror (see
`seed_plans`, whose comment says to re-check it against the live payout provider
before go-live). The rail resolves a recipient by `(account_number, bank_code)`
in ITS code space, so any bank whose code differs there fails name enquiry — and
the gateway blames the account number ("account enquiry failed, confirm that the
account number is valid") rather than the code, which is what makes this hard to
spot from the app.

Read-only by default: it prints ours vs theirs and exits 1 when anything differs,
so it can gate a deploy. `--apply` writes the rail's code onto the matching rows.

The same comparison is available without a shell: read-only in the `bank_codes`
block of `POST /wema-diagnose`, and appliable from the Bank list in Django admin
("Sync bank codes from the payout rail"). The comparison itself lives in
transfers.services.compare_bank_codes so all three agree.
"""
from django.core.management.base import BaseCommand

from transfers.services import apply_bank_codes, compare_bank_codes
from utility import wema


class Command(BaseCommand):
    help = ("Compare transfers.Bank.bank_code with the payout rail's GetAllBanks list. "
            "Exits 1 if any code differs or cannot be matched. --apply fixes the codes.")

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true",
                            help="Write the rail's bank_code onto unambiguously matched rows.")
        parser.add_argument("--all", action="store_true",
                            help="Also list banks whose codes already agree.")

    def handle(self, *args, **options):
        res = wema.get_banks()
        if not res.get("success"):
            self.stderr.write(self.style.ERROR(
                f"Could not fetch the rail's bank list: {res.get('message') or 'request failed'}"))
            return self._exit(1)
        remote = [b for b in (res.get("banks") or []) if b.get("bank_code")]
        if not remote:
            self.stderr.write(self.style.ERROR("The rail returned an empty bank list."))
            return self._exit(1)
        if res.get("mock"):
            # The mock rail answers with a single stub bank, so every other bank we
            # carry would be reported "not on the rail" — 40-odd findings that are
            # artefacts of the stub. Refuse rather than print a comparison nobody
            # should act on (and never --apply off it).
            self.stderr.write(self.style.ERROR(
                "Rail is in MOCK mode — its bank list is a stub, not the real code space, "
                f"so there is nothing to compare ({len(remote)} stub row(s)). Set the live "
                "keys (WEMA_CHANNEL_ID + WEMA_WALLET_KEY) and run this where they are set."))
            return self._exit(1)

        cmp = compare_bank_codes(remote)

        if options["all"] and cmp["agree"]:
            self.stdout.write(self.style.SUCCESS(f"{len(cmp['agree'])} bank code(s) already agree:"))
            for row in cmp["agree"]:
                self.stdout.write(f"  ok    {row['name']:<28} {row['ours']}")
        for row in cmp["differ"]:
            self.stdout.write(self.style.WARNING(
                f"  DIFF  {row['name']:<28} ours={row['ours'] or '(blank)'} "
                f"rail={row['rail']}  ({row['rail_name']})"))
        for row in cmp["ambiguous"]:
            self.stdout.write(self.style.WARNING(
                f"  AMBIG {row['name']:<28} ours={row['ours'] or '(blank)'} "
                f"rail matches several: {', '.join(row['rail'])} — fix by hand"))
        for row in cmp["unmatched"]:
            self.stdout.write(self.style.WARNING(
                f"  MISS  {row['name']:<28} ours={row['ours'] or '(blank)'} "
                f"— no bank of that name on the rail; transfers to it will fail"))
            # A shortlist beats the rail's full leftover list: that is ~1000 rows
            # live, almost all microfinance banks we don't carry.
            for s in row.get("suggestions") or []:
                self.stdout.write(f"          maybe: {s['code']:<10} {s['name']}")
        if cmp["unmatched"]:
            self.stdout.write(
                f"\n  {cmp['rail_unmatched_count']} bank(s) on the rail matched nothing of "
                f"ours. Map a MISS by hand (Django admin -> Banks) or add the spelling to "
                f"transfers.services._ALIAS_GROUPS.")

        differing = len(cmp["differ"])
        if options["apply"] and differing:
            self.stdout.write(self.style.SUCCESS(
                f"Updated {apply_bank_codes(cmp['differ'])} bank code(s)."))
            differing = 0

        extra = cmp["remote_count"] - len(cmp["agree"]) - differing
        self.stdout.write(
            f"\n{len(cmp['agree']) + differing + len(cmp['ambiguous']) + len(cmp['unmatched'])} "
            f"active bank(s) here, {cmp['remote_count']} on the rail — {len(cmp['agree'])} agree, "
            f"{differing} differ, {len(cmp['ambiguous'])} ambiguous, {len(cmp['unmatched'])} unmatched"
            + (f", {extra} rail bank(s) not in our picker" if extra > 0 else ""))
        problems = differing + len(cmp["ambiguous"]) + len(cmp["unmatched"])
        if problems:
            self.stdout.write(self.style.WARNING(
                "Recipient resolution uses these codes, so a wrong one reads to the user as "
                "'account enquiry failed'. Re-run with --apply to take the rail's codes."))
        return self._exit(1 if problems else 0)

    def _exit(self, code: int):
        # SystemExit rather than sys.exit so call_command() in tests sees the status.
        if code:
            raise SystemExit(code)
        return None
