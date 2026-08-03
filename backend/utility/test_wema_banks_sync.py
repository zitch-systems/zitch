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
from utility.management.commands.wema_banks_sync import normalize

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
