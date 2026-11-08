"""Seed the payout bank directory so the transfer picker is populated.

Run: python manage.py seed_plans
Idempotent -- safe to run repeatedly.

It seeded airtime/data and cable plan catalogues too, until the VAS rail those
products were bought through was removed. The banks are all that is left.
"""
from django.core.management.base import BaseCommand

class Command(BaseCommand):
    help = "Seed the payout bank directory."

    def handle(self, *args, **options):

        # Payout banks — the full NIP list users expect in the picker (commercial
        # banks + the fintechs/PSBs Nigerians actually send to). `bank_code` is
        # the Paystack/NIBSS transfer code, verified against two independent
        # mirrors of Paystack's GET /bank (they agreed on every code). The rail
        # resolves recipients in ITS OWN code space, so reconcile these against
        # its live bank list before go-live: `manage.py sync_bank_directory` replaces
        # them with the bank's own codes. A stale code here surfaces to
        # the user as "account enquiry failed", never as a bank problem.
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
        # NOTE the split defaults. This command runs on EVERY deploy (build.sh), so
        # anything in `defaults` is rewritten every time. `bank_code` must NOT be:
        # the codes above are a NIBSS/Paystack mirror, while payouts resolve in the
        # rail's own code space, and `sync_bank_directory` (or the Django-admin action)
        # reconciles them against the rail's live list. Re-seeding those away on the
        # next deploy would silently reintroduce "account enquiry failed" for every
        # bank the rail codes differently — a self-healing bug that reappears at the
        # worst possible moment. So the seed value is a STARTING point, applied when
        # the row is new or its code is blank, and presentation fields (name, colour,
        # logo, popularity) stay authoritative here.
        bk = restored = 0
        for code, name, color, bank_code, logo, popular in BANKS:
            bank, created = Bank.objects.update_or_create(
                code=code,
                defaults={"name": name, "color": color,
                          "logo": logo, "popular": popular, "active": True},
            )
            if (created or not bank.bank_code) and bank.bank_code != bank_code:
                bank.bank_code = bank_code
                bank.save(update_fields=["bank_code"])
                restored += 1
            bk += 1
        if restored:
            # sync_bank_directory went with the 9PSB integration, so there is no
            # rail left to reconcile a bank_code against. Say that plainly rather
            # than naming a command that no longer exists.
            self.stdout.write(self.style.WARNING(
                f"Set a starting bank_code on {restored} bank(s) with none. No payout "
                f"rail is configured, so these cannot be reconciled until one is."))

        self.stdout.write(self.style.SUCCESS(f"Seeded {bk} banks."))
