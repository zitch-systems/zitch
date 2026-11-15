"""Which bank a customer means: the words they typed, then the account number.

The messages here are the ones customers send. Two are from production: "Transfer
to access 1668720613 centrac enterprise 1800" was answered with "Which bank?" and
AAA Finance first, and the same message without the amount lost its amount
question. See transfers.bank_resolver for the rules.
"""
from django.test import TestCase

from transfers import bank_resolver
from transfers.bank_aliases import BANK_ALIASES
from transfers.models import Bank
from whatsapp import router

# slug, name, bank code, popular. The microfinance rows are what the rail adds
# beyond our seed, and three of them fit the screenshot's account number too.
BANKS = [
    ("access", "Access Bank", "044", True), ("gtb", "GTBank", "058", True),
    ("firstbank", "First Bank", "011", True), ("fcmb", "FCMB", "214", True),
    ("uba", "UBA", "033", True), ("zenith", "Zenith Bank", "057", True),
    ("wema", "Wema Bank", "035", True), ("opay", "OPay", "999992", True),
    ("palmpay", "PalmPay", "999991", True), ("hope", "Hope PSB", "120002", False),
    ("nip090000", "AAA FINANCE", "090000", False),
    ("nip090197", "ABU Microfinance bank", "090197", False),
    ("nip090483", "ADA MFB", "090483", False),
    ("nip090614", "Aella Microfinance Bank", "090614", False),
]

#: The production account number. Its check digit fits Access, First Bank, UBA,
#: PalmPay and the three microfinance banks above - so the words have to decide.
SCREENSHOT_ACCOUNT = "1668720613"


def nuban_for(bank_code: str, serial: str = "221794052") -> str:
    seq = bank_code + serial
    total = sum(int(c) * (3, 7, 3)[i % 3] for i, c in enumerate(seq))
    return serial + str((10 - total % 10) % 10)


class ResolverTestCase(TestCase):
    def setUp(self):
        self.banks = {}
        for code, name, bank_code, popular in BANKS:
            self.banks[code] = Bank.objects.create(code=code, name=name, bank_code=bank_code,
                                                   popular=popular, active=True, color="#000")

    def resolve(self, text, acct):
        return router._bank_resolution(text, acct)

    def names(self, banks):
        return [b.name for b in banks]


class SentenceTests(ResolverTestCase):

    def test_the_screenshot_sentence_names_access_bank(self):
        fits = self.names(router.nuban_bank_candidates(SCREENSHOT_ACCOUNT))
        self.assertIn("ABU Microfinance bank", fits)   # the check digit alone cannot tell
        for text in ("Transfer to access 1668720613 centrac enterprise 1800",
                     "Transfer to access 1668720613 centrac enterprise"):
            res = self.resolve(text, SCREENSHOT_ACCOUNT)
            self.assertEqual(res.bank, self.banks["access"], text)
            self.assertTrue(res.named)

    def test_every_short_name_for_gtbank(self):
        acct = nuban_for("058")
        for word in ("gt", "gtb", "tg", "gtco", "guaranty trust", "gt bank", "GTBank",
                     "guaranty trust bank"):
            res = self.resolve(f"send 5k to {acct} {word}", acct)
            self.assertEqual(res.bank, self.banks["gtb"], word)

    def test_first_after_the_account_number_is_first_bank(self):
        acct = nuban_for("011")
        self.assertEqual(self.resolve(f"send 5k to {acct} first", acct).bank,
                         self.banks["firstbank"])
        self.assertEqual(self.resolve(f"send 5k to first bank {acct}", acct).bank,
                         self.banks["firstbank"])

    def test_first_with_an_fcmb_account_number_offers_both(self):
        """"first" could be First City Monument Bank, and this number fits only it."""
        acct = nuban_for("214")
        res = self.resolve(f"send 5k to {acct} first", acct)
        self.assertIsNone(res.bank)
        self.assertEqual(self.names(res.choices), ["First Bank", "FCMB"])

    def test_first_city_is_fcmb(self):
        acct = nuban_for("214")
        self.assertEqual(self.resolve(f"{acct} first city 2000", acct).bank, self.banks["fcmb"])

    def test_first_as_an_ordinary_word_names_no_bank(self):
        acct = nuban_for("058")
        res = self.resolve(f"send 5k to my first son {acct} gtb", acct)
        self.assertEqual(res.bank, self.banks["gtb"])

    def test_a_recipient_called_hope_is_not_hope_psb(self):
        """Before the account number, an ordinary word is the person, not the bank."""
        acct = nuban_for("058")
        res = self.resolve(f"send 5k to Hope {acct}", acct)
        self.assertEqual(res.bank, self.banks["gtb"])       # read off the check digit
        self.assertFalse(res.named)
        self.assertEqual(self.resolve(f"{acct} hope psb 5000", acct).bank, self.banks["hope"])

    def test_a_misspelt_short_name_still_finds_the_bank(self):
        acct = nuban_for("044")
        self.assertEqual(self.resolve(f"pay 3000 to {acct} acess", acct).bank, self.banks["access"])
        acct = nuban_for("057")
        self.assertEqual(self.resolve(f"pay 3000 to {acct} zenit", acct).bank, self.banks["zenith"])

    def test_one_distinctive_word_of_a_microfinance_bank_is_asked_never_chosen(self):
        """"ada" is a bank's name and a first name. It goes to the top of the list
        the customer picks from; it never sends money by itself."""
        res = self.resolve(f"send 2k to ada {SCREENSHOT_ACCOUNT}", SCREENSHOT_ACCOUNT)
        self.assertIsNone(res.bank)
        self.assertEqual(res.choices[0], self.banks["nip090483"])
        self.assertEqual(self.resolve(f"{SCREENSHOT_ACCOUNT} ada mfb 2k", SCREENSHOT_ACCOUNT).bank,
                         self.banks["nip090483"])

    def test_no_bank_named_lists_the_banks_people_use_first(self):
        res = self.resolve(f"send 5k to {SCREENSHOT_ACCOUNT}", SCREENSHOT_ACCOUNT)
        self.assertIsNone(res.bank)
        self.assertEqual(self.names(res.choices[:4]),
                         ["Access Bank", "First Bank", "PalmPay", "UBA"])
        self.assertNotEqual(res.choices[0].name, "AAA FINANCE")

    def test_a_named_bank_stands_when_the_check_digit_disagrees(self):
        """The check digit is a hint; a bank the customer named is an instruction."""
        acct = nuban_for("058")
        self.assertEqual(self.resolve(f"{acct} wema 5000", acct).bank, self.banks["wema"])

    def test_an_eleven_digit_wallet_number_takes_the_named_bank(self):
        self.assertEqual(self.resolve("send 5000 to 08031234567 opay", "08031234567").bank,
                         self.banks["opay"])

    def test_a_bank_listed_twice_is_offered_once(self):
        """The rail's "ACCESS BANK", imported under its own code beside our row."""
        Bank.objects.create(code="nip000014", name="ACCESS BANK", bank_code="000014", active=True)
        self.assertEqual(self.names(router._match_banks("access")), ["Access Bank"])
        res = self.resolve(f"send 5k to access {SCREENSHOT_ACCOUNT}", SCREENSHOT_ACCOUNT)
        self.assertEqual(res.bank, self.banks["access"])


class AnswerTests(ResolverTestCase):
    """The answer to "which bank?": the form's Bank field and the chat's question."""

    def test_short_names(self):
        for typed, slug in (("access", "access"), ("first", "firstbank"), ("gt", "gtb"),
                            ("tg", "gtb"), ("first city", "fcmb"), ("hope", "hope"),
                            ("Wema", "wema"), ("aella", "nip090614")):
            self.assertEqual([b.code for b in router._match_banks(typed)], [slug], typed)

    def test_half_typed_and_generic(self):
        self.assertEqual(self.names(router._match_banks("zen")), ["Zenith Bank"])
        self.assertGreater(len(router._match_banks("bank")), 3)
        self.assertEqual(router._match_banks("not a real bank"), [])
        self.assertEqual(router._match_banks(""), [])


class TableTests(TestCase):

    def test_every_short_name_points_at_one_known_bank(self):
        for nick, slug in bank_resolver.NICKNAMES.items():
            self.assertIn(slug, bank_resolver.CANONICAL_NAMES, nick)

    def test_no_short_name_is_another_banks_alias(self):
        owner = {alias: slug for slug, (_s, names) in BANK_ALIASES.items() for alias in names}
        for nick, slug in bank_resolver.NICKNAMES.items():
            if nick in owner:
                self.assertEqual(owner[nick], slug, nick)

    def test_one_edit_is_a_typo_and_two_are_not(self):
        self.assertTrue(bank_resolver._within_one_edit("acess", "access"))
        self.assertTrue(bank_resolver._within_one_edit("zentih", "zenith"))   # swapped
        self.assertFalse(bank_resolver._within_one_edit("unity", "union"))
