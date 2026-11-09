"""The export that must run before the 9PSB-era archive is dropped.

It is the only remaining link between a customer and the Wema account they held
before the 9PSB migration, so what is pinned here is that it writes every row,
writes the numbers accurately, says so loudly when there is nothing to write,
and cannot be talked into putting that file somewhere it would be committed.

Tested against a REAL synthetic archive table rather than a mocked cursor. The
banking app does not exist in this tree, so the table is created with raw DDL
and dropped again -- which is also the shape the command meets in production, a
table present in the database with no model behind it. A mocked cursor would
not catch a column named wrongly in the SELECT, which is the most likely way
this command breaks.
"""
import csv
import os
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.db import connection
from django.test import TestCase
from django.utils import timezone

from wallet.services import get_or_create_wallet

User = get_user_model()

ARCHIVE_DDL = """
CREATE TABLE banking_previousbankarchive (
    id integer PRIMARY KEY,
    wallet_id integer NOT NULL,
    account_number varchar(20) NOT NULL,
    account_name varchar(160) NOT NULL,
    bank_name varchar(80) NOT NULL,
    account_reference varchar(64) NOT NULL,
    balance numeric(14, 2) NOT NULL,
    account_metadata text NOT NULL,
    pending_transactions integer NOT NULL,
    review_required boolean NOT NULL,
    created timestamp NOT NULL
)
"""


class ExportPreviousBankAccountsTests(TestCase):

    def setUp(self):
        self.user = User.objects.create(
            username="export", phone="08077770001", email="export@zitch.test",
            first_name="Ada", last_name="Eze")
        self.wallet = get_or_create_wallet(self.user)
        # The 9PSB NUBAN the wallet carries now.
        self.wallet.account_number = "1234509876"
        self.wallet.bank_name = "9 Payment Service Bank"
        self.wallet.save(update_fields=["account_number", "bank_name"])
        self._archive_rows = 0

    def _create_archive(self):
        with connection.cursor() as cur:
            cur.execute(ARCHIVE_DDL)
        self.addCleanup(self._drop_archive)

    def _drop_archive(self):
        with connection.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS banking_previousbankarchive")

    def _archive(self, wallet_id, *, number="0987654321", name="ADA EZE",
                 balance="15250.75", pending=2, review=True):
        self._archive_rows += 1
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO banking_previousbankarchive (id, wallet_id, "
                "account_number, account_name, bank_name, account_reference, "
                "balance, account_metadata, pending_transactions, "
                "review_required, created) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [self._archive_rows, wallet_id, number, name, "Wema Bank",
                 f"ZITCH-WALLET-{wallet_id}", balance, "{}", pending, review,
                 timezone.now()])

    def _export(self, directory, name="wema.csv", **opts):
        out = Path(directory) / name
        buf = StringIO()
        call_command("export_previous_bank_accounts", out=str(out), stdout=buf, **opts)
        return out, buf.getvalue()

    def _rows(self, path):
        with open(path, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))

    def test_it_writes_the_former_account_and_the_current_one_side_by_side(self):
        """Both numbers, because the file's whole job is to map one to the other."""
        self._create_archive()
        self._archive(self.wallet.pk)
        with TemporaryDirectory() as tmp:
            out, said = self._export(tmp)
            rows = self._rows(out)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["previous_account_number"], "0987654321")
        self.assertEqual(row["previous_account_name"], "ADA EZE")
        self.assertEqual(row["previous_bank_name"], "Wema Bank")
        self.assertEqual(row["previous_account_reference"],
                         f"ZITCH-WALLET-{self.wallet.pk}")
        self.assertEqual(row["current_wallet_account_number"], "1234509876")
        self.assertEqual(row["current_wallet_bank_name"], "9 Payment Service Bank")
        self.assertEqual(row["archived_balance"], "15250.75")
        self.assertEqual(row["pending_transactions"], "2")
        self.assertEqual(row["review_required"], "yes")
        self.assertEqual(row["user_id"], str(self.user.pk))
        self.assertEqual(row["phone"], "08077770001")
        self.assertEqual(row["email"], "export@zitch.test")
        # The summary counts it without printing any of it.
        self.assertIn("Wrote 1 archived account", said)
        self.assertIn("15250.75", said)
        self.assertNotIn("0987654321", said)
        self.assertNotIn("ADA EZE", said)
        self.assertNotIn("08077770001", said)

    def test_every_archived_account_is_written(self):
        self._create_archive()
        self._archive(self.wallet.pk)
        for i in range(2, 12):
            user = User.objects.create(
                username=f"export{i}", phone=f"0807777{i:04d}",
                email=f"export{i}@zitch.test", first_name="H", last_name=f"{i}")
            wallet = get_or_create_wallet(user)
            self._archive(wallet.pk, number=f"09876{i:05d}", name=f"HOLDER {i}",
                          balance="100.00", pending=0, review=False)
        with TemporaryDirectory() as tmp:
            out, said = self._export(tmp)
            rows = self._rows(out)
        self.assertEqual(len(rows), 11)
        self.assertEqual(len({r["previous_account_number"] for r in rows}), 11)
        self.assertIn("Wrote 11 archived account", said)
        self.assertIn("with a non-zero balance: 11", said)
        self.assertIn("flagged for review:      1", said)

    def test_a_row_whose_wallet_is_missing_is_still_exported(self):
        """An archived balance is evidence of money whoever it belonged to, so a
        row the joins cannot resolve is exported with blanks rather than dropped.

        An inner join would have lost it silently, which is the one failure this
        file exists to prevent. The live schema should make this unreachable --
        the archive's wallet is a PROTECTed one-to-one and wallet.user_id is NOT
        NULL -- so what is pinned is the behaviour on a partial restore, which is
        exactly when someone reaches for this export.
        """
        self._create_archive()
        self._archive(999_999, number="0111111111", name="GONE HOLDER")
        with TemporaryDirectory() as tmp:
            out, said = self._export(tmp)
            rows = self._rows(out)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["previous_account_number"], "0111111111")
        self.assertEqual(rows[0]["previous_account_name"], "GONE HOLDER")
        self.assertEqual(rows[0]["archived_balance"], "15250.75")
        self.assertEqual(rows[0]["wallet_id"], "")
        self.assertEqual(rows[0]["user_id"], "")
        self.assertIn("have no readable wallet or user; exported anyway", said)
        self.assertIn("Investigate before dropping", said)

    def test_an_absent_archive_table_is_reported_not_silently_empty(self):
        """On a database that never carried 9PSB there is nothing to export, and
        saying so plainly is what stops it being read as 'nothing to keep'."""
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "wema.csv"
            buf = StringIO()
            call_command("export_previous_bank_accounts", out=str(out), stdout=buf)
            said = buf.getvalue()
        self.assertIn("does not exist on this database", said)
        self.assertIn("wrong database", said)
        self.assertFalse(out.exists())   # no misleading header-only file

    def test_an_empty_archive_table_warns_rather_than_succeeding_quietly(self):
        self._create_archive()
        with TemporaryDirectory() as tmp:
            out, said = self._export(tmp)
            with open(out, encoding="utf-8") as fh:
                self.assertEqual(len(fh.readlines()), 1)   # header only
        self.assertIn("exists but is empty", said)
        self.assertIn("not proof there was nothing to keep", said)

    def test_it_refuses_to_write_inside_the_repository(self):
        """The output is a customer list. It must not be committable by accident."""
        self._create_archive()
        here = Path(__file__).resolve().parent
        with self.assertRaises(CommandError) as ctx:
            call_command("export_previous_bank_accounts",
                         out=str(here / "accidental.csv"), stdout=StringIO())
        self.assertIn("inside the repository", str(ctx.exception))
        self.assertFalse((here / "accidental.csv").exists())

    def test_it_will_not_silently_overwrite(self):
        self._create_archive()
        self._archive(self.wallet.pk)
        with TemporaryDirectory() as tmp:
            out, _ = self._export(tmp)
            with self.assertRaises(CommandError):
                self._export(tmp)
            # ...but --force is honoured, so a re-run after review is one flag.
            self._export(tmp, force=True)
            self.assertEqual(len(self._rows(out)), 1)

    def test_the_file_is_not_world_readable(self):
        self._create_archive()
        self._archive(self.wallet.pk)
        with TemporaryDirectory() as tmp:
            out, _ = self._export(tmp)
            self.assertEqual(out.stat().st_mode & 0o077, 0)

    def test_it_writes_nothing_to_the_database(self):
        """Read-only, asserted rather than assumed: this runs on production."""
        self._create_archive()
        self._archive(self.wallet.pk)
        before = (self.wallet.account_number, self.wallet.bank_name)
        with TemporaryDirectory() as tmp:
            self._export(tmp)
        self.wallet.refresh_from_db()
        self.assertEqual((self.wallet.account_number, self.wallet.bank_name), before)
        with connection.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM banking_previousbankarchive")
            self.assertEqual(cur.fetchone()[0], 1)
