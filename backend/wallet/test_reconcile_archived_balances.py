"""Whether a former-provider balance is recorded as owed on our books.

The three existing reconciliations measure the current rail, so an archived
9PSB balance is measured by nothing. What is pinned here is the distinction
that decides what to do about each row -- a row whose money is already held as
UnsettledFunding resolves through the evidenced settlement path, and a row with
money and no hold is off book and needs the bank -- plus the two properties
that make the output usable: it changes nothing, and it carries no identifying
detail, because it runs against production while customers are waiting.
"""
from decimal import Decimal
import hashlib
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.utils import timezone

from wallet.models import Transaction, UnsettledFunding
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

PHONE = "08033330001"
EMAIL = "ada@zitch.test"
OLD_ACCOUNT = "0987654321"
HOLDER = "ADA EZE"


class ReconcileArchivedBalancesTests(TestCase):

    def setUp(self):
        self.user = User.objects.create(
            username="ada", phone=PHONE, email=EMAIL,
            first_name="Ada", last_name="Eze")
        self.wallet = get_or_create_wallet(self.user)
        self._rows = 0
        with connection.cursor() as cur:
            cur.execute(ARCHIVE_DDL)
        self.addCleanup(self._drop)

    def _drop(self):
        with connection.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS banking_previousbankarchive")

    def _archive(self, wallet_id=None, *, balance="0.00", pending=0, review=False):
        self._rows += 1
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO banking_previousbankarchive (id, wallet_id, "
                "account_number, account_name, bank_name, account_reference, "
                "balance, account_metadata, pending_transactions, "
                "review_required, created) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [self._rows, self.wallet.pk if wallet_id is None else wallet_id,
                 OLD_ACCOUNT, HOLDER, "Wema Bank", f"REF-{self._rows}", balance,
                 "{}", pending, review, timezone.now()])

    def _hold(self, amount="500.00", *, settled=False, destination=OLD_ACCOUNT):
        hold = UnsettledFunding.objects.create(
            user=self.user, amount=Decimal(amount),
            destination_digest=hashlib.sha256(destination.encode()).hexdigest(),
            operator_reference="migration-audit")
        if settled:
            from wallet.models import RetiredFundingArchive
            txn = Transaction.objects.create(
                user=self.user, service="reclassification", amount=Decimal(amount),
                direction=Transaction.OUT, transaction_status=Transaction.SUCCESS,
                reference=f"recl-{self._rows}")
            RetiredFundingArchive.objects.create(
                funding=hold, transaction=txn, operator_reference="migration-audit")
        return hold

    def _run(self, **opts):
        buf = StringIO()
        call_command("reconcile_archived_balances", stdout=buf, **opts)
        return buf.getvalue()

    def test_money_with_no_hold_is_off_book(self):
        """The exposure: a balance our books do not record owing anywhere."""
        self._archive(balance="2826.00", review=True)
        out = self._run()
        self.assertIn("state=off_book", out)
        self.assertIn("OFF BOOK", out)
        self.assertIn("2826.00", out)
        self.assertIn("closing statement", out)
        # It must not tell an operator to clear the flag or restore the number.
        self.assertIn("do not clear the review flag", out)

    def test_money_already_held_is_not_reported_as_off_book(self):
        """An UnsettledFunding hold means settlement_report already counts it."""
        self._archive(balance="500.00", review=True)
        self._hold("500.00")
        out = self._run()
        self.assertIn("state=liability_on_book", out)
        self.assertNotIn("state=off_book", out)
        self.assertIn("evidenced settlement path", out)

    def test_matching_stored_wallet_amount_is_not_claimed_off_book(self):
        """The production ₦2,826 scenario: wallet balances mirror archives."""
        import json
        self._archive(balance="2826.00", review=True)
        from wallet.models import Wallet
        Wallet.objects.filter(pk=self.wallet.pk).update(balance=Decimal("2826.00"))
        report = json.loads(self._run(as_json=True))
        self.assertEqual(report["off_book_amount"], "0.00")
        self.assertEqual(report["wallet_overlap_unverified_amount"], "2826.00")
        self.assertEqual(report["wallet_overlap_unverified_rows"], 1)
        row = report["customers"][0]
        self.assertEqual(row["state"], "wallet_overlap_unverified")
        self.assertTrue(row["wallet_balance_matches_archive"])
        self.assertFalse(row["transaction_ledger_matches_archive"])
        self.assertEqual(row["transaction_ledger_expected"], "0")
        self.assertIn("NOT proven off-book", self._run())

    def test_matching_stored_wallet_and_transaction_ledger_still_need_bank_evidence(self):
        import json
        self._archive(balance="1000.00", review=True)
        from wallet.models import Wallet
        Wallet.objects.filter(pk=self.wallet.pk).update(balance=Decimal("1000.00"))
        Transaction.objects.create(
            user=self.user, service="historical_funding",
            amount=Decimal("1000.00"), direction=Transaction.IN,
            transaction_status=Transaction.SUCCESS, reference="ledger-overlap-test")
        payload = json.loads(self._run(as_json=True))
        row = payload["customers"][0]
        self.assertTrue(row["wallet_balance_matches_archive"])
        self.assertTrue(row["transaction_ledger_matches_archive"])
        self.assertEqual(row["transaction_ledger_expected"], "1000.00")
        self.assertEqual(row["state"], "wallet_overlap_unverified")
        self.assertEqual(payload["liability_on_book_rows"], 0)

    def test_a_settled_hold_still_counts_as_on_book(self):
        """Archiving a hold reclassifies it; the docstring says it stays owed."""
        self._archive(balance="500.00")
        self._hold("500.00", settled=True)
        out = self._run()
        self.assertIn("state=liability_on_book", out)

    def test_unrelated_account_hold_never_suppresses_off_book_exposure(self):
        """Same customer is insufficient: the hold can be for another rail."""
        self._archive(balance="2826.00", review=True)
        self._hold("2826.00", destination="0666666666")
        import json
        result = json.loads(self._run(as_json=True))
        self.assertEqual(result["hold_unattributed_rows"], 1)
        self.assertEqual(result["hold_unattributed_amount"], "2826.00")
        self.assertEqual(result["liability_on_book_rows"], 0)
        self.assertEqual(result["customers"][0]["state"], "hold_unattributed")
        self.assertEqual(result["customers"][0]["attribution"], "unverified_customer_hold")

    def test_same_account_different_amount_is_not_fully_accounted_for(self):
        """A ₦10 hold cannot establish a ₦2,826 liability."""
        self._archive(balance="2826.00")
        self._hold("10.00")
        import json
        result = json.loads(self._run(as_json=True))
        self.assertEqual(result["hold_unattributed_rows"], 1)
        self.assertEqual(result["liability_on_book_rows"], 0)
        self.assertEqual(result["customers"][0]["linked_hold_amount"], "10.00")

    def test_pending_legacy_transactions_require_separate_proof(self):
        self._archive(balance="500.00", pending=1)
        self._hold("500.00")
        import json
        result = json.loads(self._run(as_json=True))
        self.assertEqual(result["hold_unattributed_rows"], 1)
        self.assertEqual(result["liability_on_book_rows"], 0)

    def test_exact_account_and_amount_matches_are_attributed_internally(self):
        self._archive(balance="500.00")
        self._hold("500.00")
        import json
        result = json.loads(self._run(as_json=True))
        self.assertEqual(result["liability_on_book_rows"], 1)
        self.assertEqual(result["hold_unattributed_rows"], 0)
        self.assertEqual(result["customers"][0]["attribution"],
                         "matching_destination_and_amount")
        self.assertNotIn(OLD_ACCOUNT, self._run(as_json=True))

    def test_a_flag_with_no_money_is_not_a_liability(self):
        self._archive(balance="0.00", review=True)
        out = self._run()
        self.assertIn("state=flag_only", out)
        self.assertNotIn("OFF BOOK", out)

    def test_pending_transactions_alone_are_money_at_stake(self):
        """A zero balance with transactions in flight is not 'nothing at stake'."""
        self._archive(balance="0.00", pending=2)
        out = self._run()
        self.assertIn("state=off_book", out)

    def test_a_blanket_review_flag_is_named_as_a_default_not_a_finding(self):
        """The question the operator actually has about review_required."""
        self._archive(balance="0.00", review=True)
        self._archive(balance="0.00", review=True)
        out = self._run()
        self.assertIn("migration default", out)
        self.assertIn("cannot be read as suspicion", out)

    def test_a_selective_review_flag_is_reported_as_a_signal(self):
        self._archive(balance="0.00", review=False)
        self._archive(balance="0.00", review=True)
        out = self._run()
        self.assertIn("applied selectively", out)
        self.assertNotIn("migration default", out)

    def test_a_same_amount_credit_is_labelled_a_hint_not_proof(self):
        """Two customers owed the same round figure is ordinary."""
        self._archive(balance="1000.00")
        Transaction.objects.create(
            user=self.user, service="funding", amount=Decimal("1000.00"),
            direction=Transaction.IN, transaction_status=Transaction.SUCCESS,
            reference="credit-1")
        out = self._run()
        self.assertIn("hint:", out)
        self.assertIn("not settlement proof", out)
        # The hint must not change the row's state.
        self.assertIn("state=off_book", out)

    def test_an_archive_row_with_no_wallet_is_escalated_not_crashed(self):
        self._archive(wallet_id=999_999, balance="100.00")
        out = self._run()
        self.assertIn("state=no_wallet", out)
        self.assertIn("investigate", out)

    def test_a_database_without_the_archive_is_said_plainly(self):
        self._drop()
        out = self._run()
        self.assertIn("does not exist on this database", out)
        self.assertIn("wrong database", out)

    def test_an_empty_archive_is_distinguished_from_a_missing_one(self):
        out = self._run()
        self.assertIn("is empty", out)

    def test_it_reports_no_identifying_detail(self):
        """This output belongs in tickets and deploy logs."""
        self._archive(balance="2826.00", review=True)
        out = self._run()
        for secret in (PHONE, EMAIL, OLD_ACCOUNT, HOLDER, "Ada", "Eze"):
            self.assertNotIn(secret, out)

    def test_json_output_is_ids_states_and_amounts_only(self):
        import json

        self._archive(balance="2826.00", review=True)
        payload = json.loads(self._run(as_json=True))
        self.assertTrue(payload["read_only"])
        self.assertEqual(payload["off_book_rows"], 1)
        self.assertEqual(Decimal(payload["off_book_amount"]), Decimal("2826.00"))
        self.assertTrue(payload["review_flag_looks_blanket"])
        blob = json.dumps(payload)
        for secret in (PHONE, EMAIL, OLD_ACCOUNT, HOLDER):
            self.assertNotIn(secret, blob)

    def test_it_writes_nothing(self):
        """Asserted rather than assumed: this is run against production."""
        self._archive(balance="2826.00", review=True)
        self._hold("10.00")
        before = (self.wallet.balance, self.wallet.account_number,
                  Transaction.objects.count(), UnsettledFunding.objects.count(),
                  User.objects.count())
        self._run()
        self.wallet.refresh_from_db()
        self.assertEqual(
            (self.wallet.balance, self.wallet.account_number,
             Transaction.objects.count(), UnsettledFunding.objects.count(),
             User.objects.count()), before)
        with connection.cursor() as cur:
            cur.execute("SELECT COUNT(*), SUM(balance) FROM banking_previousbankarchive")
            self.assertEqual(cur.fetchone(), (1, Decimal("2826.00")))
