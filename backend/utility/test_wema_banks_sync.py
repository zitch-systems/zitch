"""Tests for the bank-code reconciliation command.

Our bank_codes were seeded from a NIBSS/Paystack mirror; the rail resolves in its
OWN code space, and a wrong code surfaces to the user as "account enquiry failed"
rather than as a bank problem. The command has to spot that, and must never guess
a code for a name that matches more than one bank on the rail.
"""
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from transfers.models import Bank
from transfers.services import normalize_bank_name as normalize

_GET_BANKS = "utility.wema.get_banks"


def _run(remote, *args):
    out = StringIO()
    code = 0
    with mock.patch(_GET_BANKS, return_value={"success": True, "banks": remote}):
        try:
            call_command("wema_banks_sync", *args, stdout=out, stderr=out)
        except SystemExit as exc:
            code = exc.code
    return out.getvalue(), code


class NormalizeTests(TestCase):
    def test_drops_generic_words_and_ordering(self):
        self.assertEqual(normalize("Moniepoint MFB"), normalize("Moniepoint Microfinance Bank"))
        self.assertEqual(normalize("GTBank Plc"), normalize("gtbank"))
        self.assertEqual(normalize("Bank of Africa Nigeria"), normalize("Africa of Bank"))

    def test_keeps_distinct_banks_distinct(self):
        self.assertNotEqual(normalize("First Bank"), normalize("Fidelity Bank"))
        self.assertNotEqual(normalize("Union Bank"), normalize("Unity Bank"))

    def test_a_wholly_generic_name_still_normalizes(self):
        # Every token is noise — fall back to the words rather than an empty key,
        # which would collide with every other all-noise name.
        self.assertEqual(normalize("Bank PLC"), "bank plc")


class BanksSyncTests(TestCase):
    def setUp(self):
        Bank.objects.create(code="gtb", name="GTBank", bank_code="058")
        Bank.objects.create(code="kuda", name="Kuda", bank_code="50211")

    def test_matching_codes_pass(self):
        out, code = _run([{"bank_name": "GTBank", "bank_code": "058"},
                          {"bank_name": "Kuda Bank", "bank_code": "50211"}], "--all")
        self.assertEqual(code, 0)
        self.assertIn("2 agree", out)

    def test_a_differing_code_is_reported_and_fails(self):
        out, code = _run([{"bank_name": "GTBank", "bank_code": "000013"},
                          {"bank_name": "Kuda Bank", "bank_code": "50211"}])
        self.assertEqual(code, 1)
        self.assertIn("DIFF", out)
        self.assertIn("000013", out)
        # Read-only without --apply.
        self.assertEqual(Bank.objects.get(code="gtb").bank_code, "058")

    def test_apply_writes_the_rails_code(self):
        _, code = _run([{"bank_name": "GTBank", "bank_code": "000013"},
                        {"bank_name": "Kuda Bank", "bank_code": "50211"}], "--apply")
        self.assertEqual(code, 0)
        self.assertEqual(Bank.objects.get(code="gtb").bank_code, "000013")

    def test_an_ambiguous_name_is_never_auto_applied(self):
        out, code = _run([{"bank_name": "GTBank Nigeria", "bank_code": "000013"},
                          {"bank_name": "GTBank Plc", "bank_code": "058999"},
                          {"bank_name": "Kuda Bank", "bank_code": "50211"}], "--apply")
        self.assertEqual(code, 1)
        self.assertIn("AMBIG", out)
        self.assertEqual(Bank.objects.get(code="gtb").bank_code, "058")

    def test_a_bank_the_rail_does_not_list_is_flagged(self):
        out, code = _run([{"bank_name": "GTBank", "bank_code": "058"}])
        self.assertEqual(code, 1)
        self.assertIn("MISS", out)
        self.assertIn("Kuda", out)

    def test_a_mock_rail_refuses_to_compare(self):
        # The mock rail answers with one stub bank; comparing against it would report
        # every other bank as missing and could --apply the stub's code.
        out = StringIO()
        code = 0
        stub = {"success": True, "mock": True, "banks": [{"bank_name": "Wema Bank", "bank_code": "035"}]}
        with mock.patch(_GET_BANKS, return_value=stub):
            try:
                call_command("wema_banks_sync", "--apply", stdout=out, stderr=out)
            except SystemExit as exc:
                code = exc.code
        self.assertEqual(code, 1)
        self.assertIn("MOCK mode", out.getvalue())
        self.assertNotIn("MISS", out.getvalue())
        self.assertEqual(Bank.objects.get(code="gtb").bank_code, "058")

    def test_an_unreachable_rail_fails_rather_than_wiping_codes(self):
        out = StringIO()
        code = 0
        with mock.patch(_GET_BANKS, return_value={"success": False, "message": "gateway down"}):
            try:
                call_command("wema_banks_sync", "--apply", stdout=out, stderr=out)
            except SystemExit as exc:
                code = exc.code
        self.assertEqual(code, 1)
        self.assertEqual(Bank.objects.get(code="gtb").bank_code, "058")


class TradeNameMatchingTests(TestCase):
    """The first live run left GTBank, First Bank, UBA and Citibank unmatched — the
    rail lists them under legal names that share few or no words with the trade
    names in our picker, so their (wrong) codes were left in place."""

    def _sync(self, ours, rail_name, rail_code):
        Bank.objects.all().delete()
        Bank.objects.create(code="x", name=ours, bank_code="OLD")
        _run([{"bank_name": rail_name, "bank_code": rail_code}], "--apply")
        return Bank.objects.get(code="x").bank_code

    def test_legal_name_variants_match(self):
        self.assertEqual(self._sync("First Bank", "First Bank of Nigeria", "011x"), "011x")
        self.assertEqual(self._sync("GTBank", "Guaranty Trust Bank", "058x"), "058x")
        self.assertEqual(self._sync("UBA", "United Bank for Africa", "033x"), "033x")
        self.assertEqual(self._sync("FCMB", "First City Monument Bank", "214x"), "214x")
        self.assertEqual(self._sync("V Bank (VFD MFB)", "VFD Microfinance Bank", "566x"), "566x")
        self.assertEqual(self._sync("9PSB", "9 Payment Service Bank", "120x"), "120x")

    def test_one_word_vs_two_word_spellings_match(self):
        self.assertEqual(self._sync("PremiumTrust Bank", "Premium Trust Bank", "105x"), "105x")
        self.assertEqual(self._sync("Citibank Nigeria", "Citi Bank", "023x"), "023x")
        # Our picker name carries the parent brand the rail's legal name omits.
        self.assertEqual(
            self._sync("SmartCash PSB (Airtel)", "SmartCash Payment Service Bank", "120x"), "120x")
        self.assertEqual(
            self._sync("MoMo PSB (MTN)", "MoMo Payment Service Bank", "120y"), "120y")

    def test_an_unforeseen_spelling_is_left_for_a_human_not_guessed(self):
        # No subset rule: {first} is a subset of {first, city, monument}, so one
        # would route First Bank's payouts to FCMB. An unmatched pair surfaces in
        # rail_unmatched instead, to be mapped by hand or added to _ALIAS_GROUPS.
        self.assertEqual(self._sync("First Bank", "First City Monument Bank", "214x"), "OLD")

    def test_every_bank_the_first_live_run_missed_now_matches(self):
        """Pins the actual regression: these are the banks the live sync left
        unmatched, against the legal names a Nigerian bank list carries for them.
        Each one that stays unmatched keeps a wrong code and fails every transfer
        to that bank."""
        for ours, rail in (
            ("9PSB", "9 Payment Service Bank"),
            ("Citibank Nigeria", "Citibank Nigeria Limited"),
            ("First Bank", "First Bank of Nigeria"),
            ("GTBank", "Guaranty Trust Bank"),
            ("Mint MFB", "Mint Microfinance Bank"),
            ("MoMo PSB (MTN)", "MoMo Payment Service Bank"),
            ("Nova Bank", "Nova Merchant Bank"),
            ("PremiumTrust Bank", "Premium Trust Bank"),
            ("SmartCash PSB (Airtel)", "SmartCash Payment Service Bank"),
            ("UBA", "United Bank for Africa"),
            ("V Bank (VFD MFB)", "VFD Microfinance Bank"),
        ):
            with self.subTest(bank=ours):
                self.assertEqual(self._sync(ours, rail, "NEW"), "NEW")

    def test_distinct_banks_still_do_not_match(self):
        # The looser rules must not start matching banks that merely look alike.
        self.assertEqual(self._sync("Unity Bank", "Union Bank of Nigeria", "032x"), "OLD")
        self.assertEqual(self._sync("First Bank", "Fidelity Bank", "070x"), "OLD")
        self.assertEqual(self._sync("Sterling Bank", "Stanbic IBTC Bank", "221x"), "OLD")

    def test_an_exact_match_wins_over_a_looser_one(self):
        Bank.objects.all().delete()
        Bank.objects.create(code="citi", name="Citibank Nigeria", bank_code="OLD")
        _run([{"bank_name": "Citi Bank", "bank_code": "WRONG"},
              {"bank_name": "Citibank Nigeria", "bank_code": "RIGHT"}], "--apply")
        self.assertEqual(Bank.objects.get(code="citi").bank_code, "RIGHT")

    def test_the_rail_writes_9psb_as_one_word(self):
        self.assertEqual(self._sync("9PSB", "9PAYMENT SERVICE BANK", "120001"), "120001")

    def test_mint_maps_to_the_licensed_entity_the_rail_lists(self):
        # The last unmatched bank of the live run: our picker says "Mint MFB", the
        # rail lists "MINT-FINEX MFB" (090281).
        self.assertEqual(self._sync("Mint MFB", "MINT-FINEX MFB", "090281"), "090281")

    def test_an_unmatched_bank_gets_a_shortlist_not_the_whole_rail(self):
        """The live rail returns ~1000 rows, nearly all microfinance banks we do not
        carry. Dumping them buried the one name that mattered, so each MISS now
        carries only the rail names that plausibly are it."""
        Bank.objects.all().delete()
        Bank.objects.create(code="sparkle", name="Sparkle MFB", bank_code="51310")
        noise = [{"bank_name": f"{n} MICROFINANCE BANK", "bank_code": f"0904{i:02d}"}
                 for i, n in enumerate(("ABBEY", "BOJI BOJI", "CANAAN", "DAYLIGHT", "EYOWO"))]
        out, code = _run(noise + [{"bank_name": "SPARKLEX MICROFINANCE BANK",
                                   "bank_code": "090999"}])
        self.assertEqual(code, 1)
        self.assertIn("MISS", out)
        # The plausible one is named…
        self.assertIn("SPARKLEX MICROFINANCE BANK", out)
        self.assertIn("090999", out)
        # …and the unrelated leftovers are counted, not listed. The count is 6, not
        # 5: the suggested row is itself unclaimed until a human maps it.
        self.assertNotIn("BOJI BOJI", out)
        self.assertIn("6 bank(s) on the rail matched nothing", out)

    def test_a_suggestion_is_never_applied_on_its_own(self):
        # "Plausibly the same bank" is not good enough to route money on: a
        # suggestion needs a human, unlike an alias someone has confirmed.
        Bank.objects.all().delete()
        Bank.objects.create(code="sparkle", name="Sparkle MFB", bank_code="51310")
        _run([{"bank_name": "SPARKLEX MICROFINANCE BANK", "bank_code": "090999"}], "--apply")
        self.assertEqual(Bank.objects.get(code="sparkle").bank_code, "51310")


class ReseedDoesNotUndoReconciliationTests(TestCase):
    """build.sh runs seed_plans on EVERY deploy. It used to rewrite bank_code from
    its hardcoded NIBSS/Paystack table, so the next deploy would silently undo a
    sync against the rail and bring "account enquiry failed" back."""

    def test_a_reconciled_code_survives_a_reseed(self):
        call_command("seed_plans", stdout=StringIO())
        gtb = Bank.objects.get(code="gtb")
        seeded = gtb.bank_code
        gtb.bank_code = "000013"      # what a sync against the rail wrote
        gtb.save(update_fields=["bank_code"])

        call_command("seed_plans", stdout=StringIO())
        self.assertEqual(Bank.objects.get(code="gtb").bank_code, "000013",
                         f"the redeploy reset the reconciled code back to {seeded}")

    def test_a_blank_code_is_still_filled_in(self):
        call_command("seed_plans", stdout=StringIO())
        Bank.objects.filter(code="gtb").update(bank_code="")
        call_command("seed_plans", stdout=StringIO())
        self.assertTrue(Bank.objects.get(code="gtb").bank_code)

    def test_presentation_fields_stay_seed_authoritative(self):
        call_command("seed_plans", stdout=StringIO())
        Bank.objects.filter(code="gtb").update(name="Wrong", color="#000000", active=False)
        call_command("seed_plans", stdout=StringIO())
        gtb = Bank.objects.get(code="gtb")
        self.assertEqual(gtb.name, "GTBank")
        self.assertTrue(gtb.active)


class BankCodesInProbeTests(TestCase):
    """The same comparison, read-only, for a deploy whose operator has no shell."""

    def setUp(self):
        Bank.objects.create(code="gtb", name="GTBank", bank_code="058")

    def _probe(self, banks):
        # Only the bank-list call matters here; the rest of the probe is stubbed so
        # the test doesn't depend on every other rail endpoint.
        with mock.patch(_GET_BANKS, return_value=banks), \
             mock.patch("utility.wema.wema_live", return_value=True), \
             mock.patch("utility.wema.get_data_plans", return_value={"success": True}):
            from utility.wema import wema_probe

            return wema_probe()

    def test_a_differing_code_is_reported_with_a_fix_hint(self):
        out = self._probe({"success": True, "banks": [{"bank_name": "GTBank", "bank_code": "000013"}]})
        self.assertFalse(out["bank_codes"]["ok"])
        self.assertEqual(out["bank_codes"]["differ"][0]["rail"], "000013")
        self.assertIn("account enquiry failed", out["bank_codes"]["hint"])

    def test_matching_codes_report_ok(self):
        out = self._probe({"success": True, "banks": [{"bank_name": "GTBank Plc", "bank_code": "058"}]})
        self.assertTrue(out["bank_codes"]["ok"])
        self.assertEqual(out["bank_codes"]["agree"], 1)

    def test_mock_mode_reports_no_comparison_at_all(self):
        out = self._probe({"success": True, "mock": True,
                           "banks": [{"bank_name": "Wema Bank", "bank_code": "035"}]})
        self.assertNotIn("bank_codes", out)


class BankAdminSyncTests(TestCase):
    """The browser path — same comparison, applied from the Bank changelist."""

    def setUp(self):
        from django.contrib.admin.sites import AdminSite

        from transfers.admin import BankAdmin

        Bank.objects.create(code="gtb", name="GTBank", bank_code="058")
        self.admin = BankAdmin(Bank, AdminSite())
        self.messages = []
        self.admin.message_user = lambda request, msg, level=None: self.messages.append(msg)

    def _run(self, banks):
        with mock.patch(_GET_BANKS, return_value=banks):
            self.admin.sync_bank_codes(None, Bank.objects.none())

    def test_applies_the_rails_code_ignoring_the_selection(self):
        # An empty selection still syncs: a partial sync would leave the picker half
        # in one code space and half in the other.
        self._run({"success": True, "banks": [{"bank_name": "GTBank", "bank_code": "000013"}]})
        self.assertEqual(Bank.objects.get(code="gtb").bank_code, "000013")
        self.assertIn("Updated 1 bank code(s)", " ".join(self.messages))

    def test_a_mock_rail_changes_nothing(self):
        self._run({"success": True, "mock": True,
                   "banks": [{"bank_name": "Wema Bank", "bank_code": "035"}]})
        self.assertEqual(Bank.objects.get(code="gtb").bank_code, "058")
        self.assertIn("MOCK mode", " ".join(self.messages))

    def test_an_unreachable_rail_changes_nothing(self):
        self._run({"success": False, "message": "gateway down"})
        self.assertEqual(Bank.objects.get(code="gtb").bank_code, "058")
        self.assertIn("Could not reach the payout rail", " ".join(self.messages))
