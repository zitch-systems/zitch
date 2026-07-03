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

        # Exam PIN products (WAEC / NECO / JAMB / NABTEB).
        from exams.models import ExamProduct
        EXAMS = [
            ("waec", "WAEC", "Result Checker PIN", 3500, "waec-registration"),
            ("neco", "NECO", "Result Token", 1300, "neco-result"),
            ("jamb", "JAMB", "UTME / DE PIN", 6200, "jamb"),
            ("nabteb", "NABTEB", "Result Checker", 1000, "nabteb"),
        ]
        e = 0
        for code, name, desc, price, service_id in EXAMS:
            ExamProduct.objects.update_or_create(
                code=code,
                defaults={"name": name, "description": desc, "price": price,
                          "service_id": service_id, "active": True},
            )
            e += 1

        # Betting platforms.
        from betting.models import BettingPlatform
        BETTING = [
            ("bet9ja", "Bet9ja", "#0B7A3B"),
            ("sporty", "SportyBet", "#E1241B"),
            ("onexbet", "1xBet", "#1A6BB5"),
            ("betking", "BetKing", "#1B1B1B"),
            ("nairabet", "NairaBet", "#1E8B45"),
            ("msport", "MSport", "#E8530E"),
        ]
        b = 0
        for code, name, color in BETTING:
            BettingPlatform.objects.update_or_create(
                code=code,
                defaults={"name": name, "color": color, "service_id": code, "active": True},
            )
            b += 1

        # Payout banks — the full NIP list users expect in the picker (commercial
        # banks + the fintechs/PSBs Nigerians actually send to). `bank_code` is
        # the Paystack/NIBSS transfer code, verified against two independent
        # mirrors of Paystack's GET /bank (they agreed on every code); re-check
        # against the live payout provider's own bank list before go-live.
        # Heritage Bank is deliberately absent (licence revoked June 2024).
        # Logos are served from the ichtrojan/nigerian-banks repo (the backing
        # store of nigerianbanks.xyz); every referenced file was verified to
        # exist. Blank logo -> the app renders a colored monogram instead.
        # `popular` marks the high-volume banks the auto-detect name-enquiry
        # sweep probes (see transfers.services.detect_account_banks) — sweeping
        # all ~40 banks per typed account would be slow and costly.
        from transfers.models import Bank
        LOGO = "https://raw.githubusercontent.com/ichtrojan/nigerian-banks/master/logos/{}.png".format
        BANKS = [
            # code, name, color, bank_code, logo, popular
            ("access", "Access Bank", "#F68B1F", "044", LOGO("access-bank"), True),
            ("gtb", "GTBank", "#DD4F05", "058", LOGO("guaranty-trust-bank"), True),
            ("zenith", "Zenith Bank", "#E31B23", "057", LOGO("zenith-bank"), True),
            ("uba", "UBA", "#DA291C", "033", LOGO("united-bank-for-africa"), True),
            ("firstbank", "First Bank", "#003B65", "011", LOGO("first-bank-of-nigeria"), True),
            ("fcmb", "FCMB", "#5C2D91", "214", LOGO("first-city-monument-bank"), True),
            ("fidelity", "Fidelity Bank", "#232E83", "070", LOGO("fidelity-bank"), True),
            ("wema", "Wema Bank", "#990D81", "035", LOGO("wema-bank"), True),
            ("opay", "OPay", "#1DCF9F", "999992", LOGO("paycom"), True),
            ("palmpay", "PalmPay", "#6C25D9", "999991", LOGO("palmpay"), True),
            ("kuda", "Kuda", "#40196D", "50211", LOGO("kuda-bank"), True),
            ("moniepoint", "Moniepoint MFB", "#0357EE", "50515", LOGO("moniepoint-mfb-ng"), True),
            ("sterling", "Sterling Bank", "#D6001C", "232", LOGO("sterling-bank"), True),
            ("union", "Union Bank", "#009FDF", "032", LOGO("union-bank-of-nigeria"), True),
            ("stanbic", "Stanbic IBTC", "#0033A1", "221", LOGO("stanbic-ibtc-bank"), True),
            ("ecobank", "Ecobank", "#0066B3", "050", LOGO("ecobank-nigeria"), True),
            ("9psb", "9PSB", "#00A5B5", "120001", "", False),
            ("carbon", "Carbon", "#5E5CE6", "565", "", False),
            ("citi", "Citibank Nigeria", "#004685", "023", LOGO("citibank-nigeria"), False),
            ("fairmoney", "FairMoney MFB", "#5A31F4", "51318", "", False),
            ("globus", "Globus Bank", "#0082CA", "00103", LOGO("globus-bank"), False),
            ("gomoney", "GoMoney", "#00C6A2", "100022", "", False),
            ("hope", "Hope PSB", "#009E49", "120002", "", False),
            ("jaiz", "Jaiz Bank", "#009A49", "301", "", False),
            ("keystone", "Keystone Bank", "#FDB913", "082", LOGO("keystone-bank"), False),
            ("lotus", "Lotus Bank", "#005647", "303", LOGO("lotus-bank"), False),
            ("mint", "Mint MFB", "#00B8A9", "50304", "", False),
            ("momo", "MoMo PSB (MTN)", "#FFCC00", "120003", "", False),
            ("nova", "Nova Bank", "#1B3764", "561", "", False),
            ("optimus", "Optimus Bank", "#003057", "107", "", False),
            ("parallex", "Parallex Bank", "#003E7E", "104", "", False),
            ("polaris", "Polaris Bank", "#93268F", "076", LOGO("polaris-bank"), False),
            ("premiumtrust", "PremiumTrust Bank", "#1B2A5C", "105", "", False),
            # Providus & Unity merged into ProvidusUnity (June 2026); both codes
            # still resolve on NIP during the transition, so both stay listed.
            ("providus", "Providus Bank", "#FDB515", "101", "", False),
            ("unity", "Unity Bank", "#8CC63F", "215", "", False),
            ("rubies", "Rubies MFB", "#C4112F", "125", "", False),
            ("scb", "Standard Chartered", "#0473EA", "068", LOGO("standard-chartered-bank"), False),
            ("signature", "Signature Bank", "#0E4D2C", "106", "", False),
            ("smartcash", "SmartCash PSB (Airtel)", "#ED1C24", "120004", "", False),
            ("sparkle", "Sparkle MFB", "#FF2E57", "51310", LOGO("sparkle-microfinance-bank"), False),
            ("suntrust", "SunTrust Bank", "#F26522", "100", "", False),
            ("taj", "TAJ Bank", "#522E91", "302", LOGO("taj-bank"), False),
            ("titan", "Titan Trust Bank", "#002855", "102", "", False),
            ("vbank", "V Bank (VFD MFB)", "#F42F4B", "566", "", False),
        ]
        bk = 0
        for code, name, color, bank_code, logo, popular in BANKS:
            Bank.objects.update_or_create(
                code=code,
                defaults={"name": name, "color": color, "bank_code": bank_code,
                          "logo": logo, "popular": popular, "active": True},
            )
            bk += 1

        self.stdout.write(self.style.SUCCESS(
            f"Seeded {d} data plans, {c} cable plans, {e} exam products, "
            f"{b} betting platforms, {bk} banks."))
