"""Use provider-issued routing codes; never reuse a retired rail's code map."""
import hashlib
from collections import defaultdict
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from banking.adapter import get_banks
from banking.client import configured
from transfers.models import Bank
from transfers.services import bank_name_keys


class Command(BaseCommand):
    help = "Replace active payout routing with the confirmed 9PSB bank directory."

    def add_arguments(self, parser):
        parser.add_argument("--if-configured", action="store_true")

    def handle(self, *args, **options):
        if not configured() and options["if_configured"]:
            # Empty directory is safer than routing through historical codes.
            Bank.objects.update(active=False)
            self.stdout.write("9PSB directory unavailable; bank transfers remain closed.")
            return
        result = get_banks()
        if not result.get("success") or not result.get("banks"):
            raise CommandError("The bank directory could not be confirmed; no routing changes applied")
        by_code = defaultdict(list)
        for row in result["banks"]:
            by_code[row["bank_code"]].append(row["bank_name"])
        with transaction.atomic():
            current = list(Bank.objects.select_for_update().all())
            Bank.objects.update(active=False)
            used, count = set(), 0
            for code, names in sorted(by_code.items()):
                if len({bank_name_keys(n)[0] for n in names}) > 1:
                    continue  # Conflicting provider rows require review, not guessing.
                name = names[0]
                keys = set(bank_name_keys(name)) - {None}
                matches = [b for b in current if b.pk not in used and keys.intersection(set(bank_name_keys(b.name)) - {None})]
                if len(matches) > 1:
                    continue
                slug = "9psb" if "alias:9psb" in keys else "n9-" + hashlib.sha256(code.encode()).hexdigest()[:12]
                if matches:
                    bank = matches[0]
                    used.add(bank.pk)
                    bank.bank_code, bank.active = code, True
                    bank.save(update_fields=["bank_code", "active"])
                else:
                    Bank.objects.update_or_create(code=slug, defaults={"name": name[:60], "bank_code": code, "active": True})
                count += 1
        self.stdout.write(f"9PSB bank directory: {count} confirmed routing entries active.")
