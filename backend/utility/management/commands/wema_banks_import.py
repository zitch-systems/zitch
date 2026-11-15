"""Carry every bank the payout rail can send to, not only the ones we seeded.

`seed_plans` lists about 45 banks: the commercial banks and the fintechs most
people send to. The rail's GetAllBanks returns about a thousand, every
microfinance bank, mortgage bank and payment service bank on NIP. A bank we do not
carry cannot be paid at all, and the chat cannot recognise its name ("aella mfb",
"lapo") because there is nothing to recognise it as.

Read-only by default: it lists what it would add. --apply creates the missing
rows, active and not popular, under a slug made from the rail's bank code
("nip090614"). A rail row counts as already carried when one of ours has the same
bank code, or a name that reads the same (transfers.services.bank_name_keys: "ACCESS
BANK" is our "Access Bank"). Reconciling the codes of those is wema_banks_sync's
job; this command never changes a row that exists.

The rail lists some banks twice: once under the six-digit NIP code and once with
its leading zero dropped ("090832" and "90832", both 5TT MFB). Those are one
bank, carried once, under the six-digit code. Two rail rows whose names read the
same are also one bank: only the first is added, so the bank list never shows a
customer the same name twice.

    python manage.py wema_banks_import            # list what would be added
    python manage.py wema_banks_import --apply    # add them
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from transfers.models import Bank
from transfers.services import bank_name_keys
from utility import wema

NAME_MAX = Bank._meta.get_field("name").max_length
CODE_MAX = Bank._meta.get_field("bank_code").max_length
SLUG_MAX = Bank._meta.get_field("code").max_length


def nip_code(code):
    """The six-digit NIP code a rail code stands for: "90832" is "090832"."""
    return code.zfill(6) if len(code) == 5 else code


class Command(BaseCommand):
    help = ("Add the payout rail's banks (GetAllBanks) that transfers.Bank does not carry yet. "
            "Read-only unless --apply.")

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Create the missing banks.")
        parser.add_argument("--show", type=int, default=30,
                            help="How many of the missing banks to list (default 30).")

    def handle(self, *args, **options):
        res = wema.get_banks()
        if not res.get("success"):
            self.stderr.write(self.style.ERROR(
                f"Could not fetch the rail's bank list: {res.get('message') or 'request failed'}"))
            raise SystemExit(1)
        if res.get("mock"):
            # The mock rail answers with one stub bank. Importing it would add a bank
            # that does not exist, so refuse rather than write anything.
            self.stderr.write(self.style.ERROR(
                "Rail is in MOCK mode, so its bank list is a stub. Run this where the live "
                "Wema keys are set."))
            raise SystemExit(1)

        rail = {}       # six-digit code -> (code as listed, name), in rail order
        skipped_shape = 0
        for row in res.get("banks") or []:
            code = str(row.get("bank_code") or "").strip()
            name = " ".join(str(row.get("bank_name") or "").split())
            if not code or not name:
                continue
            if not code.isdigit() or len(code) > CODE_MAX:
                skipped_shape += 1
                continue
            key = nip_code(code)
            listed = rail.get(key)
            # Keep the six-digit spelling when the rail lists both.
            if listed is None or (len(code) > len(listed[0])):
                rail[key] = (code, name[:NAME_MAX]) if listed is None else (code, listed[1])
        rail = list(rail.values())
        if not rail:
            self.stderr.write(self.style.ERROR("The rail returned no usable banks."))
            raise SystemExit(1)

        ours_codes = {nip_code(c) for c in Bank.objects.values_list("bank_code", flat=True) if c}
        ours_keys = set()
        for name in Bank.objects.values_list("name", flat=True):
            ours_keys.update(k for k in bank_name_keys(name) if k)
        ours_slugs = set(Bank.objects.values_list("code", flat=True))
        batch_keys = set()

        missing, same_name = [], 0
        for code, name in rail:
            if nip_code(code) in ours_codes:
                continue
            keys = [k for k in bank_name_keys(name) if k]
            if any(k in ours_keys for k in keys):
                continue
            slug = f"nip{code}"[:SLUG_MAX]
            if slug in ours_slugs:
                continue
            if any(k in batch_keys for k in keys):
                same_name += 1          # an earlier rail row already carries this name
                continue
            batch_keys.update(keys)
            missing.append((slug, code, name))

        carried = len(rail) - len(missing) - same_name
        self.stdout.write(
            f"The rail lists {len(rail)} bank(s); {carried} are already carried here and "
            f"{len(missing)} are not." + (f" ({skipped_shape} row(s) with an unusable code "
                                          "were ignored.)" if skipped_shape else "")
            + (f" ({same_name} row(s) repeating another bank's name were left out.)"
               if same_name else ""))
        for slug, code, name in sorted(missing, key=lambda m: m[2].lower())[:max(0, options["show"])]:
            self.stdout.write(f"  add   {code:<10} {name}")
        if len(missing) > options["show"] > 0:
            self.stdout.write(f"  ... and {len(missing) - options['show']} more")

        if not missing:
            self.stdout.write(self.style.SUCCESS("Nothing to add."))
            return
        if not options["apply"]:
            self.stdout.write("Run again with --apply to add them.")
            return
        created = 0
        with transaction.atomic():
            for slug, code, name in missing:
                _bank, made = Bank.objects.get_or_create(
                    code=slug,
                    defaults={"name": name, "bank_code": code, "active": True,
                              "popular": False, "color": ""})
                created += int(made)
        self.stdout.write(self.style.SUCCESS(
            f"Added {created} bank(s). Customers can now pay them, and the chat "
            "recognises their names."))
