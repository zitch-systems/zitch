"""Restoring the account the migration took off a wallet.

The archive row is our own ownership record, so restoring from it needs no
provider confirmation. What these tests pin is that it stays a restore and
cannot become something worse: it never overwrites an account a wallet already
has, never puts one number on two wallets, never silently promotes money that
has not been reconciled, and does nothing at all until asked.
"""
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase
from django.utils import timezone

from wallet.models import Wallet
from wallet.services import get_or_create_wallet
from whatsapp.models import AuditLog

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

THEIR_ACCOUNT = "0123456789"
HOLDER = "ADA EZE"


class RestoreArchivedBankAccountsTests(TestCase):

    def setUp(self):
        self.user = User.objects.create(
            username="ada", phone="08011110001", email="ada@zitch.test",
            first_name="Ada", last_name="Eze")
        self.wallet = get_or_create_wallet(self.user)
        self.wallet.account_number = ""
        self.wallet.save(update_fields=["account_number"])
        self._rows = 0
        with connection.cursor() as cur:
            cur.execute(ARCHIVE_DDL)
        self.addCleanup(self._drop)

    def _drop(self):
        with connection.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS banking_previousbankarchive")

    def _archive(self, wallet_id, *, number=THEIR_ACCOUNT, name=HOLDER,
                 balance="0.00", pending=0, review=False, reference=None):
        self._rows += 1
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO banking_previousbankarchive (id, wallet_id, "
                "account_number, account_name, bank_name, account_reference, "
                "balance, account_metadata, pending_transactions, "
                "review_required, created) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [self._rows, wallet_id, number, name, "Wema Bank",
                 reference or f"REF-{wallet_id}", balance, "{}", pending, review, timezone.now()])

    def _other_wallet(self, suffix="2"):
        user = User.objects.create(
            username=f"other{suffix}", phone=f"0801111000{suffix}",
            email=f"other{suffix}@zitch.test", first_name="O", last_name="Ther")
        wallet = get_or_create_wallet(user)
        wallet.account_number = ""
        wallet.save(update_fields=["account_number"])
        return wallet

    def _run(self, **opts):
        buf = StringIO()
        if opts.get("apply"):
            opts.setdefault("user_id", self.user.pk)
            opts.setdefault("bank_confirmation_ref", "WEMA-TEST-CASE-001")
        call_command("restore_archived_bank_accounts", stdout=buf, **opts)
        return buf.getvalue()

    def test_it_gives_the_customer_their_account_back(self):
        self._archive(self.wallet.pk)
        out = self._run(apply=True)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.account_number, THEIR_ACCOUNT)
        self.assertEqual(self.wallet.account_name, HOLDER)
        self.assertEqual(self.wallet.bank_name, "Wema Bank")
        self.assertIn("Restored 1 archived account", out)

    def test_a_dry_run_changes_nothing(self):
        """The default must be safe to run on production out of curiosity."""
        self._archive(self.wallet.pk)
        out = self._run()
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.account_number, "")
        self.assertFalse(AuditLog.objects.exists())
        self.assertIn("Would restore 1", out)
        self.assertIn("nothing was written", out)

    def test_old_provider_reference_cannot_be_restored_under_wema_label(self):
        self._archive(self.wallet.pk, reference=f"9PSB-{self.user.pk}")
        self._run(apply=True)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.account_number, "")
        self.assertFalse(AuditLog.objects.exists())

    def test_it_never_overwrites_an_account_the_wallet_already_has(self):
        """A newly issued account must survive this command untouched."""
        self.wallet.account_number = "9999999999"
        self.wallet.save(update_fields=["account_number"])
        self._archive(self.wallet.pk)
        out = self._run(apply=True)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.account_number, "9999999999")
        self.assertIn("already had an account number: 1", out)

    def test_one_number_is_never_attached_to_two_wallets(self):
        """Two wallets sharing a NUBAN would mix up two customers' money."""
        other = self._other_wallet()
        other.account_number = THEIR_ACCOUNT
        other.save(update_fields=["account_number"])
        self._archive(self.wallet.pk, number=THEIR_ACCOUNT)
        out = self._run(apply=True)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.account_number, "")
        self.assertIn("already on another wallet: 1", out)
        self.assertIn("data conflict", out)

    def test_a_retired_number_is_not_restored(self):
        self._archive(self.wallet.pk, number="7110000001")
        self._run(apply=True)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.account_number, "")

    def test_a_malformed_number_is_not_restored(self):
        self._archive(self.wallet.pk, number="12345")
        out = self._run(apply=True)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.account_number, "")
        self.assertIn("invalid or retired: 1", out)

    def test_unreconciled_money_waits_for_a_human_by_default(self):
        for kwargs in ({"balance": "1500.00"}, {"pending": 2}, {"review": True}):
            with self.subTest(**kwargs):
                Wallet.objects.filter(pk=self.wallet.pk).update(account_number="")
                with connection.cursor() as cur:
                    cur.execute("DELETE FROM banking_previousbankarchive")
                self._archive(self.wallet.pk, **kwargs)
                out = self._run(apply=True)
                self.wallet.refresh_from_db()
                self.assertEqual(self.wallet.account_number, "")
                self.assertIn("money needs review first: 1", out)

    def test_flagged_rows_cannot_be_restored_even_with_unsafe_override(self):
        self._archive(self.wallet.pk, balance="1500.00", review=True)
        with self.assertRaisesMessage(CommandError, "disabled"):
            self._run(apply=True, include_flagged=True)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.account_number, "")

    def test_every_restore_is_audited_with_bank_confirmation(self):
        self._archive(self.wallet.pk)
        self._run(apply=True)
        entry = AuditLog.objects.get(action="archived_bank_account_restored")
        self.assertEqual(entry.target, f"wallet:{self.wallet.pk}")
        self.assertEqual(entry.before["account_number"], "")
        self.assertEqual(Decimal(entry.after["archived_balance"]), Decimal("0.00"))
        self.assertEqual(entry.after["pending_transactions"], 0)
        self.assertFalse(entry.after["review_required"])
        self.assertEqual(entry.after["bank_confirmation_ref"], "WEMA-TEST-CASE-001")

    def test_running_it_twice_restores_once(self):
        self._archive(self.wallet.pk)
        self._run(apply=True)
        out = self._run(apply=True)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.account_number, THEIR_ACCOUNT)
        self.assertEqual(AuditLog.objects.filter(
            action="archived_bank_account_restored").count(), 1)
        self.assertIn("Restored 0", out)

    def test_it_prints_no_account_number_or_holder_name(self):
        self._archive(self.wallet.pk)
        out = self._run(apply=True)
        self.assertNotIn(THEIR_ACCOUNT, out)
        self.assertNotIn(HOLDER, out)
        self.assertIn(str(self.wallet.pk), out)

    def test_an_archive_row_with_no_wallet_is_reported_not_crashed(self):
        self._archive(999_999)
        out = self._run()
        self.assertIn("archive row has no wallet: 1", out)

    def test_a_database_without_the_archive_is_said_plainly(self):
        self._drop()
        out = self._run(apply=True)
        self.assertIn("does not exist on this database", out)
        self.assertIn("wrong database", out)


    def test_unscoped_apply_is_refused_without_any_writes(self):
        self._archive(self.wallet.pk)
        with self.assertRaisesMessage(CommandError, "requires --user-id"):
            call_command("restore_archived_bank_accounts", apply=True, stdout=StringIO())
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.account_number, "")
        self.assertFalse(AuditLog.objects.exists())

    def test_missing_bank_reference_is_refused_even_for_one_customer(self):
        self._archive(self.wallet.pk)
        with self.assertRaisesMessage(CommandError, "--bank-confirmation-ref"):
            call_command("restore_archived_bank_accounts", apply=True,
                         user_id=self.user.pk, stdout=StringIO())
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.account_number, "")

    def test_pending_previous_provider_account_blocks_recovery(self):
        self._archive(self.wallet.pk)
        from unittest.mock import patch
        with patch("wallet.partnership.previous_provider_cutover_blockers",
                   return_value=["previous_provider_account_review"]):
            out = self._run(apply=True)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.account_number, "")
        self.assertIn("money needs review first: 1", out)
        self.assertFalse(AuditLog.objects.exists())

    def test_only_selected_customer_can_be_restored(self):
        second = self._other_wallet()
        self._archive(self.wallet.pk)
        self._archive(second.pk, number="0222222222")
        self._run(apply=True)
        self.wallet.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(self.wallet.account_number, THEIR_ACCOUNT)
        self.assertEqual(second.account_number, "")

    def test_archive_from_another_bank_is_not_mislabelled_as_wema(self):
        self._archive(self.wallet.pk)
        with connection.cursor() as cur:
            cur.execute("UPDATE banking_previousbankarchive SET bank_name = %s",
                        ["Another Bank"])
        out = self._run(apply=True)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.account_number, "")
        self.assertIn("invalid or retired: 1", out)
