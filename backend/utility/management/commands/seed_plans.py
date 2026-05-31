"""Seed sample data + cable plans so the app's pickers are populated.

Run: python manage.py seed_plans
Idempotent — safe to run repeatedly. Replace these with the real plan catalogue
from your aggregator before go-live.
"""
from django.core.management.base import BaseCommand

from utility.models import CablePlan, DataPlan

DATA = {
    # network: plan_type: [(name, validity, code, price)]
    "1": {  # MTN
        "1": [("1.5GB", "30 days", "mtn-sme-1500", 1000), ("3GB", "30 days", "mtn-sme-3000", 1500),
              ("6GB", "30 days", "mtn-sme-6000", 2500), ("11GB", "30 days", "mtn-sme-11000", 4500)],
        "3": [("2GB", "30 days", "mtn-gift-2000", 1400), ("40GB", "30 days", "mtn-gift-40000", 11000)],
    },
    "2": {  # GLO
        "1": [("2GB", "30 days", "glo-sme-2000", 1000), ("5.8GB", "30 days", "glo-sme-5800", 2000)],
    },
    "3": {  # Airtel
        "1": [("1.5GB", "30 days", "airtel-sme-1500", 1000), ("10GB", "30 days", "airtel-sme-10000", 4000)],
    },
    "4": {  # 9mobile
        "1": [("1GB", "30 days", "9mobile-sme-1000", 1000), ("11GB", "30 days", "9mobile-sme-11000", 5000)],
    },
}

CABLE = {
    "1": [("GOtv Smallie", "30 days", "gotv-smallie", 1575), ("GOtv Jolli", "30 days", "gotv-jolli", 3950),
          ("GOtv Max", "30 days", "gotv-max", 5700)],
    "2": [("DStv Padi", "30 days", "dstv-padi", 4400), ("DStv Yanga", "30 days", "dstv-yanga", 6000),
          ("DStv Compact", "30 days", "dstv-compact", 19000)],
    "3": [("StarTimes Nova", "30 days", "startimes-nova", 1900), ("StarTimes Basic", "30 days", "startimes-basic", 4200)],
}


class Command(BaseCommand):
    help = "Seed sample data and cable plans."

    def handle(self, *args, **options):
        d = c = 0
        for net, types in DATA.items():
            for ptype, plans in types.items():
                for name, validity, code, price in plans:
                    _, created = DataPlan.objects.update_or_create(
                        plan_code=code,
                        defaults={"network": net, "plan_type": ptype, "name": name,
                                  "validity": validity, "price": price, "active": True},
                    )
                    d += 1
        for prov, plans in CABLE.items():
            for name, validity, code, price in plans:
                CablePlan.objects.update_or_create(
                    cable_plan_code=code,
                    defaults={"provider": prov, "name": name, "validity": validity,
                              "price": price, "active": True},
                )
                c += 1
        self.stdout.write(self.style.SUCCESS(f"Seeded {d} data plans and {c} cable plans."))
