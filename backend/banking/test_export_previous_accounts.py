"""The export that has to run before PreviousBankArchive is dropped.

It is the only remaining link between a customer and the Wema account they held
before the 9PSB migration, so the things worth pinning are that it writes every
row, writes the numbers accurately, and cannot be talked into writing that file
somewhere it would be committed.
"""
import csv
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import CommandError, call_command
from django.test import TestCase

from banking.models import PreviousBankArchive
from wallet.services import get_or_create_wallet
from wallet.tests import make_user


class ExportPreviousBankAccountsTests(TestCase):

    def setUp(self):
        self.user, _ = make_user("08077770001", "export@zitch.test", tier=0,
                                 identity_verified=False)
        self.wallet = get_or_create_wallet(self.user)
        self.wallet.account_number = "1234509876"      # the 9PSB number it has now
        self.wallet.bank_name = "9 Payment Service Bank"
        self.wallet.save(update_fields=["account_number", "bank_name"])
        self.archive = PreviousBankArchive.objects.create(
            wallet=self.wallet,
            account_number="0987654321",               # the Wema number it had
            account_name="ADA EZE",
            bank_name="Wema Bank",
            account_reference="ZITCH-WALLET-1",
            balance=Decimal("15250.75"),
            pending_transactions=2,
            review_required=True,
        )

    def _export(self, directory, name="wema.csv", **opts):
        out = Path(directory) / name
        buf = StringIO()
        call_command("export_previous_bank_accounts", out=str(out), stdout=buf, **opts)
        return out, buf.getvalue()

    def test_it_writes_the_former_account_and_the_current_one_side_by_side(self):
        """Both numbers, because the file's job is to map one to the other."""
        with TemporaryDirectory() as tmp:
            out, said = self._export(tmp)
            with open(out, newline="", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["previous_account_number"], "0987654321")
        self.assertEqual(row["previous_account_name"], "ADA EZE")
        self.assertEqual(row["previous_bank_name"], "Wema Bank")
        self.assertEqual(row["current_wallet_account_number"], "1234509876")
        self.assertEqual(row["archived_balance"], "15250.75")
        self.assertEqual(row["pending_transactions"], "2")
        self.assertEqual(row["review_required"], "yes")
        self.assertEqual(row["user_id"], str(self.user.pk))
        self.assertEqual(row["phone"], "08077770001")
        # And the summary counts it without printing it.
        self.assertIn("Wrote 1 archived account", said)
        self.assertIn("15250.75", said)
        self.assertNotIn("0987654321", said)
        self.assertNotIn("ADA EZE", said)

    def test_it_refuses_to_write_inside_the_repository(self):
        """The file is a customer list. It must not be committable by accident."""
        here = Path(__file__).resolve().parent
        with self.assertRaises(CommandError) as ctx:
            call_command("export_previous_bank_accounts",
                         out=str(here / "accidental.csv"), stdout=StringIO())
        self.assertIn("inside the repository", str(ctx.exception))
        self.assertFalse((here / "accidental.csv").exists())

    def test_it_will_not_silently_overwrite(self):
        with TemporaryDirectory() as tmp:
            out, _ = self._export(tmp)
            with self.assertRaises(CommandError):
                self._export(tmp)
            # ...but --force is honoured, so a re-run after a review is one flag.
            self._export(tmp, force=True)
            self.assertTrue(out.exists())

    def test_the_file_is_not_world_readable(self):
        with TemporaryDirectory() as tmp:
            out, _ = self._export(tmp)
            self.assertEqual(out.stat().st_mode & 0o077, 0)

    def test_an_empty_archive_says_so_loudly_rather_than_writing_a_header(self):
        """Running this against the wrong database and getting a quiet success is
        how the table gets dropped on the strength of an empty file."""
        PreviousBankArchive.objects.all().delete()
        with TemporaryDirectory() as tmp:
            out, said = self._export(tmp)
            with open(out, encoding="utf-8") as fh:
                self.assertEqual(len(fh.readlines()), 1)   # header only
        self.assertIn("No archived accounts found", said)
        self.assertIn("Check before dropping", said)

    def test_every_archived_account_is_written(self):
        """Chunked iteration must not drop the tail."""
        for i in range(2, 12):
            user, _ = make_user(f"0807777{i:04d}", f"export{i}@zitch.test",
                                tier=0, identity_verified=False)
            wallet = get_or_create_wallet(user)
            PreviousBankArchive.objects.create(
                wallet=wallet, account_number=f"09876{i:05d}",
                account_name=f"HOLDER {i}", bank_name="Wema Bank",
                balance=Decimal("100.00"), pending_transactions=0,
                review_required=False)
        with TemporaryDirectory() as tmp:
            out, said = self._export(tmp)
            with open(out, newline="", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 11)
        self.assertEqual(len({r["previous_account_number"] for r in rows}), 11)
        self.assertIn("Wrote 11 archived account", said)
