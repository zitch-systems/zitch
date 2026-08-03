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

Matching is by normalized bank name (case, punctuation and the generic words
"bank/plc/nigeria/limited/microfinance…" removed). An ambiguous name — one that
matches more than one remote row — is NEVER auto-applied; it is reported for a
human, because writing the wrong code here misroutes real money.
"""
import re

from django.core.management.base import BaseCommand

from transfers.models import Bank
from utility import wema

# Generic words that carry no identity: dropping them lets "Moniepoint MFB" match
# "Moniepoint Microfinance Bank" while keeping "First Bank" != "Fidelity Bank".
_NOISE = {"bank", "banks", "plc", "ltd", "limited", "nigeria", "nigerian", "ng",
          "mfb", "microfinance", "finance", "psb", "payment", "service", "services",
          "digital", "company", "co"}


def normalize(name: str) -> str:
    """Identity-bearing tokens of a bank name, sorted and joined."""
    words = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower()).split()
    kept = [w for w in words if w not in _NOISE]
    return " ".join(sorted(kept or words))


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
            self.stdout.write(self.style.WARNING(
                "Rail is in MOCK mode — this list is a stub, not the real code space. "
                "Set the live keys before trusting this comparison."))

        by_name: dict[str, list] = {}
        for row in remote:
            by_name.setdefault(normalize(row["bank_name"]), []).append(row)

        agree, differ, ambiguous, unmatched = [], [], [], []
        for bank in Bank.objects.filter(active=True).order_by("name"):
            hits = by_name.get(normalize(bank.name)) or []
            if not hits:
                unmatched.append(bank)
            elif len({h["bank_code"] for h in hits}) > 1:
                ambiguous.append((bank, hits))
            elif hits[0]["bank_code"] == bank.bank_code:
                agree.append(bank)
            else:
                differ.append((bank, hits[0]))

        if options["all"] and agree:
            self.stdout.write(self.style.SUCCESS(f"{len(agree)} bank code(s) already agree:"))
            for bank in agree:
                self.stdout.write(f"  ok    {bank.name:<28} {bank.bank_code}")

        for bank, hit in differ:
            self.stdout.write(self.style.WARNING(
                f"  DIFF  {bank.name:<28} ours={bank.bank_code or '(blank)'} "
                f"rail={hit['bank_code']}  ({hit['bank_name']})"))
        for bank, hits in ambiguous:
            codes = ", ".join(sorted({f"{h['bank_code']} ({h['bank_name']})" for h in hits}))
            self.stdout.write(self.style.WARNING(
                f"  AMBIG {bank.name:<28} ours={bank.bank_code or '(blank)'} "
                f"rail matches several: {codes} — fix by hand"))
        for bank in unmatched:
            self.stdout.write(self.style.WARNING(
                f"  MISS  {bank.name:<28} ours={bank.bank_code or '(blank)'} "
                f"— no bank of that name on the rail; transfers to it will fail"))

        if options["apply"] and differ:
            for bank, hit in differ:
                bank.bank_code = hit["bank_code"]
                bank.save(update_fields=["bank_code"])
            self.stdout.write(self.style.SUCCESS(f"Updated {len(differ)} bank code(s)."))
            differ = []

        extra = len(remote) - len(agree) - len(differ)
        self.stdout.write(
            f"\n{Bank.objects.filter(active=True).count()} active bank(s) here, "
            f"{len(remote)} on the rail — {len(agree)} agree, {len(differ)} differ, "
            f"{len(ambiguous)} ambiguous, {len(unmatched)} unmatched"
            + (f", {extra} rail bank(s) not in our picker" if extra > 0 else ""))
        problems = len(differ) + len(ambiguous) + len(unmatched)
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
