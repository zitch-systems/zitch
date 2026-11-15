"""The rail's whole bank list, added to the banks customers can pay.

Every microfinance and mortgage bank on NIP has to be a Bank row before anyone can
send to it, and before the chat can recognise its name. The command adds what is
missing and never touches what exists: matching existing codes is wema_banks_sync's
job.
"""
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from transfers.models import Bank

_GET_BANKS = "utility.wema.get_banks"

RAIL = [
    {"bank_name": "ACCESS BANK", "bank_code": "044"},                 # ours by code
    {"bank_name": "GUARANTY TRUST BANK", "bank_code": "000013"},      # ours by name
    {"bank_name": "AELLA MICROFINANCE BANK", "bank_code": "090614"},  # new
    {"bank_name": "  LAPO   MFB ", "bank_code": "090177"},            # new, spacing
    {"bank_name": "AELLA MICROFINANCE BANK", "bank_code": "090614"},  # listed twice
    {"bank_name": "NO CODE MFB", "bank_code": ""},                     # unusable
    {"bank_name": "BAD CODE MFB", "bank_code": "12-34"},               # unusable
]


def _run(remote, *args, mock_rail=False):
    out = StringIO()
    code = 0
    payload = {"success": True, "banks": remote, **({"mock": True} if mock_rail else {})}
    with mock.patch(_GET_BANKS, return_value=payload):
        try:
            call_command("wema_banks_import", *args, stdout=out, stderr=out)
        except SystemExit as exc:
            code = exc.code
    return out.getvalue(), code


class WemaBanksImportTests(TestCase):
    def setUp(self):
        Bank.objects.create(code="access", name="Access Bank", bank_code="044", active=True)
        Bank.objects.create(code="gtb", name="GTBank", bank_code="058", active=True)

    def test_lists_what_is_missing_and_writes_nothing(self):
        out, code = _run(RAIL)
        self.assertEqual(code, 0)
        self.assertIn("AELLA MICROFINANCE BANK", out)
        self.assertIn("LAPO MFB", out)
        self.assertIn("2 are not", out)
        self.assertEqual(Bank.objects.count(), 2)

    def test_apply_adds_them_as_payable_banks(self):
        _run(RAIL, "--apply")
        aella = Bank.objects.get(bank_code="090614")
        self.assertEqual((aella.code, aella.name, aella.active, aella.popular),
                         ("nip090614", "AELLA MICROFINANCE BANK", True, False))
        self.assertEqual(Bank.objects.get(bank_code="090177").name, "LAPO MFB")
        self.assertEqual(Bank.objects.count(), 4)

    def test_existing_banks_are_never_changed_or_duplicated(self):
        _run(RAIL, "--apply")
        _run(RAIL, "--apply")
        self.assertEqual(Bank.objects.get(code="gtb").bank_code, "058")
        self.assertEqual(Bank.objects.filter(name__icontains="guaranty").count(), 0)
        self.assertEqual(Bank.objects.count(), 4)

    def test_the_chat_recognises_an_imported_bank(self):
        from whatsapp import router

        _run(RAIL, "--apply")
        self.assertEqual([b.bank_code for b in router._match_banks("aella")], ["090614"])
        self.assertEqual([b.bank_code for b in router._match_banks("lapo mfb")], ["090177"])

    def test_a_mock_rail_is_refused(self):
        out, code = _run([{"bank_name": "Wema Bank", "bank_code": "035"}], "--apply", mock_rail=True)
        self.assertEqual(code, 1)
        self.assertIn("MOCK", out)
        self.assertEqual(Bank.objects.count(), 2)

    def test_a_failed_fetch_exits_non_zero(self):
        out = StringIO()
        with mock.patch(_GET_BANKS, return_value={"success": False, "message": "timeout"}):
            with self.assertRaises(SystemExit) as exit_:
                call_command("wema_banks_import", stdout=out, stderr=out)
        self.assertEqual(exit_.exception.code, 1)
        self.assertIn("timeout", out.getvalue())

    def test_a_code_listed_without_its_leading_zero_is_one_bank(self):
        out, _code = _run([
            {"bank_name": "5TT MFB", "bank_code": "090832"},
            {"bank_name": "5TT MFB", "bank_code": "90832"},
            {"bank_name": "BANK OF AGRICULTURE", "bank_code": "90367"},
            {"bank_name": "BANK OF AGRICULTURE", "bank_code": "090367"},
        ], "--apply")
        self.assertEqual(sorted(Bank.objects.filter(code__startswith="nip")
                                .values_list("bank_code", flat=True)), ["090367", "090832"])
        self.assertIn("2 are not", out)

    def test_a_short_code_we_already_carry_is_not_added_again(self):
        Bank.objects.create(code="boa", name="BOA", bank_code="090367", active=True)
        _run([{"bank_name": "Bank Of Agric", "bank_code": "90367"}], "--apply")
        self.assertFalse(Bank.objects.filter(code="nip90367").exists())

    def test_two_rail_rows_with_the_same_name_add_one_bank(self):
        out, _code = _run([
            {"bank_name": "ASCENSIA FINANCE COMPANY LIMITED", "bank_code": "050040"},
            {"bank_name": "Ascensia Finance Company Limited", "bank_code": "050099"},
        ], "--apply")
        self.assertEqual(Bank.objects.filter(name__iexact="ascensia finance company limited").count(), 1)
        self.assertIn("1 row(s) repeating another bank's name were left out", out)

