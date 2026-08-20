"""What people actually call the banks.

The rail's list, and our seed of it, carries one name per bank — usually the
short trading name ("GTBank", "UBA", "FCMB"). Customers use many more than one.
They write the legal name off a statement ("Guaranty Trust Bank"), the holding
company ("GTCO"), the old name a decade after a rebrand ("Diamond"), the parent
brand of a wallet ("Airtel" for SmartCash), or an initialism nobody outside
Nigeria would recognise ("SCB", "STB").

Matching on the stored name alone answers "Guaranty Trust Bank" with nothing at
all, which reads as the bank not existing. So the aliases live here, keyed by our
own `Bank.code` slug — ours, and therefore stable — rather than by the display
name, which the rail can change under us on any sync.

`short` is the badge the app puts beside the bank in its picker, so the customer
can see that GTBank is the one they call GT before they tap it.

Two rules for anything added here:

  * An alias must be UNAMBIGUOUS. It is matched exactly and wins outright, so an
    alias claimed by two banks would silently send money to whichever sorted
    first. "first" is deliberately absent for that reason — First Bank, FCMB and
    FairMoney all have a claim to it, and a customer who typed it has not yet
    said which they mean.
  * An alias is never a substring shortcut. Substring matching still runs after
    this and covers the partial words; these are for the names that share no
    letters with what we store.
"""

# slug -> (short badge, aliases)
BANK_ALIASES = {
    "access": ("Access", ["access bank plc", "accessbank", "diamond", "diamond bank"]),
    "gtb": ("GT", ["gt", "gtco", "gt bank", "gtbank plc", "guaranty", "guaranty trust",
                   "guaranty trust bank", "guarantee trust bank", "gtb plc"]),
    "zenith": ("Zenith", ["zenith bank plc", "zenithbank"]),
    "uba": ("UBA", ["united bank for africa", "united bank of africa", "uba plc",
                    "ubagroup"]),
    "firstbank": ("First", ["firstbank", "first bank of nigeria", "fbn", "fbn plc",
                            "first bank plc", "firstbank nigeria"]),
    "fcmb": ("FCMB", ["first city monument bank", "first city monument"]),
    "fidelity": ("Fidelity", ["fidelity bank plc", "fidelitybank"]),
    "wema": ("Wema", ["wema bank plc", "alat", "alat by wema", "wemabank"]),
    "opay": ("OPay", ["o pay", "opay digital", "paycom", "opay wallet"]),
    "palmpay": ("PalmPay", ["palm pay", "palmpay limited"]),
    "kuda": ("Kuda", ["kuda bank", "kuda mfb", "kudabank"]),
    "moniepoint": ("Moniepoint", ["moniepoint", "monie point", "moniepoint microfinance",
                                  "moniepoint bank", "teamapt"]),
    "sterling": ("Sterling", ["sterling bank plc", "sterlingbank"]),
    "union": ("Union", ["union bank of nigeria", "ubn", "unionbank"]),
    "stanbic": ("Stanbic", ["stanbic ibtc bank", "ibtc", "stanbic bank", "stanbicibtc"]),
    "ecobank": ("Ecobank", ["ecobank nigeria", "eco bank", "ecobank plc"]),
    "9psb": ("9PSB", ["9 psb", "9mobile psb", "9payment", "9 payment service bank",
                      "9payment service bank"]),
    "carbon": ("Carbon", ["carbon mfb", "one finance", "paylater"]),
    "citi": ("Citi", ["citibank", "citi bank", "citibank nigeria limited"]),
    "fairmoney": ("FairMoney", ["fair money", "fairmoney microfinance",
                                "fairmoney bank"]),
    "globus": ("Globus", ["globus bank limited"]),
    "gomoney": ("GoMoney", ["go money"]),
    "hope": ("Hope", ["hope psb", "hope payment service bank"]),
    "jaiz": ("Jaiz", ["jaiz bank plc"]),
    "keystone": ("Keystone", ["keystone bank limited"]),
    "lotus": ("Lotus", ["lotus bank limited"]),
    "mint": ("Mint", ["mint mfb", "mint microfinance", "mintyn"]),
    "momo": ("MoMo", ["momo", "momo psb", "mtn momo", "mtn", "mtn psb",
                      "momo payment service bank"]),
    "nova": ("Nova", ["nova merchant bank", "nova bank limited"]),
    "optimus": ("Optimus", ["optimus bank limited"]),
    "parallex": ("Parallex", ["parallex bank limited"]),
    "polaris": ("Polaris", ["polaris bank limited", "skye", "skye bank"]),
    "premiumtrust": ("PremiumTrust", ["premium trust", "premium trust bank",
                                      "premiumtrust"]),
    "providus": ("Providus", ["providus bank limited", "providusunity",
                              "providus unity"]),
    "unity": ("Unity", ["unity bank plc", "unitybank"]),
    "rubies": ("Rubies", ["rubies bank", "rubies microfinance", "highstreet mfb"]),
    "scb": ("StanChart", ["scb", "stanchart", "standard chartered bank",
                          "standard chartered nigeria"]),
    "signature": ("Signature", ["signature bank limited"]),
    "smartcash": ("SmartCash", ["smartcash", "smart cash", "airtel", "airtel money",
                                "airtel psb", "smartcash payment service bank"]),
    "sparkle": ("Sparkle", ["sparkle bank", "sparkle microfinance"]),
    "suntrust": ("SunTrust", ["sun trust", "suntrust bank nigeria", "stb"]),
    "taj": ("TAJ", ["taj bank limited", "tajbank"]),
    "titan": ("Titan", ["titan trust", "titan bank", "titan trust bank limited"]),
    "vbank": ("VBank", ["v bank", "vfd", "vfd mfb", "vfd microfinance", "vbank"]),
}


def short_name(slug: str) -> str:
    """The badge shown beside a bank, or "" when we have nothing better than the
    stored name."""
    entry = BANK_ALIASES.get(slug or "")
    return entry[0] if entry else ""


def aliases_for(slug: str) -> list:
    entry = BANK_ALIASES.get(slug or "")
    return list(entry[1]) if entry else []


def slug_for_alias(text: str) -> str:
    """The bank slug a phrase names EXACTLY, or "".

    Exact only. These strings decide where money is about to go, and a partial
    match against a list this size is how "united" becomes United Bank for Africa
    when the customer meant Unity. Partial words are left to the substring pass
    that runs afterwards, which returns every candidate rather than choosing.
    """
    t = " ".join(str(text or "").split()).lower()
    if not t:
        return ""
    for slug, (_short, names) in BANK_ALIASES.items():
        if t in names:
            return slug
    return ""
