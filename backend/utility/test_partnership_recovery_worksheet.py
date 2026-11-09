"""The worksheet that tells an operator who is stuck and what to do about it.

What is pinned here is the part that makes it usable in a ticket or a deploy
log: it reports bank-side state and nothing identifying, it separates "blocked
on the bank" from "still in flight", and it changes nothing. The second property
matters most — this runs against production while customers are waiting.
"""
from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.utils import timezone

from wallet.models import WemaFaceSession
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

PHONE = "08099990001"
EMAIL = "stuck@zitch.test"
OLD_ACCOUNT = "0987654321"
HOLDER = "ADA EZE"


class PartnershipRecoveryWorksheetTests(TestCase):

    def setUp(self):
        self.user = User.objects.create(
            username="stuck", phone=PHONE, email=EMAIL,
            first_name="Ada", last_name="Eze", bvn_verified=True,
            bvn_hash="a" * 64)
        self.wallet = get_or_create_wallet(self.user)
        self.wallet.account_number = ""
        self.wallet.save(update_fields=["account_number"])

    def _session(self, **kwargs):
        defaults = {
            "user": self.user, "state": "s" * 40, "identity_type": "bvn",
            "identity_hash": self.user.bvn_hash,
            "status": WemaFaceSession.VERIFIED,
            "expires_at": timezone.now() + timedelta(hours=1),
        }
        return WemaFaceSession.objects.create(**{**defaults, **kwargs})

    def _run(self, **opts):
        buf = StringIO()
        call_command("partnership_recovery_worksheet", stdout=buf, **opts)
        return buf.getvalue()

    def _create_archive(self, wallet_id, *, balance="500.00", review=False, pending=0):
        with connection.cursor() as cur:
            cur.execute(ARCHIVE_DDL)
            cur.execute(
                "INSERT INTO banking_previousbankarchive (id, wallet_id, "
                "account_number, account_name, bank_name, account_reference, "
                "balance, account_metadata, pending_transactions, "
                "review_required, created) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [1, wallet_id, OLD_ACCOUNT, HOLDER, "Wema Bank", "REF", balance,
                 "{}", pending, review, timezone.now()])
        self.addCleanup(self._drop_archive)

    def _drop_archive(self):
        with connection.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS banking_previousbankarchive")

    def test_a_bank_rejected_customer_is_named_as_blocked_not_in_progress(self):
        """existing_customer never resolves on its own, so it must not read as waiting."""
        self._session(account_state="review_required",
                      account_failure_category="existing_customer")
        out = self._run()
        self.assertIn(f"user {self.user.pk}", out)
        self.assertIn("existing_customer", out)
        self.assertIn("Reconcile with the bank", out)
        self.assertIn("blocked on bank reconciliation", out)
        self.assertIn("no archived account", out)

    def test_an_archived_account_outranks_the_session_diagnosis(self):
        """A customer with an archived account is not a failed setup.

        They had an account and the migration archived it, so issuance never
        needed to run. Reporting "confirm the customer reached account setup",
        or sending an operator to the bank, points at the wrong thing entirely.
        """
        self._create_archive(self.wallet.pk, review=False, balance="0.00")
        self._session(account_state="review_required",
                      account_failure_category="existing_customer")
        out = self._run()
        self.assertIn("former_provider_evidence=yes", out)
        self.assertIn("restore_archived_bank_accounts --apply", out)
        self.assertIn("can have their archived account restored", out)
        # The bank is not the answer for this customer.
        self.assertNotIn("blocked on bank reconciliation", out)
        self.assertNotIn("Confirm the customer actually reached account setup", out)

    def test_a_held_archive_row_says_restoring_would_not_surface_it(self):
        """The same row blocks the funding account, so the number alone is not enough."""
        self._create_archive(self.wallet.pk, review=True, balance="250.00")
        out = self._run()
        self.assertIn("former_provider_evidence=yes_held", out)
        self.assertIn("HELD", out)
        self.assertIn("previous_provider_cutover_blockers", out)
        self.assertIn("Reconcile", out)
        self.assertNotIn("can have their archived account restored", out)

    def test_it_reports_no_identifying_detail(self):
        """This output belongs in tickets and deploy logs, so it carries ids only."""
        self._create_archive(self.wallet.pk)
        self._session(account_state="review_required",
                      account_failure_category="existing_customer")
        out = self._run()
        for secret in (PHONE, EMAIL, OLD_ACCOUNT, HOLDER, "Ada", "Eze"):
            self.assertNotIn(secret, out)
        # It still says the evidence exists, which is what decides the next step.
        self.assertIn("former_provider_evidence=yes", out)

    def test_an_in_flight_callback_is_not_reported_as_needing_a_human(self):
        self._session(status=WemaFaceSession.PENDING, account_state="awaiting_callback")
        out = self._run()
        self.assertIn("the bank's account callback", out)
        self.assertIn("No action yet", out)
        self.assertNotIn("blocked on bank reconciliation", out)

    def test_a_stale_callback_stops_being_called_in_flight(self):
        session = self._session(status=WemaFaceSession.PENDING,
                                account_state="awaiting_callback")
        WemaFaceSession.objects.filter(pk=session.pk).update(
            updated=timezone.now() - timedelta(hours=4))
        out = self._run()
        self.assertIn("treat the callback as lost", out)

    def test_a_customer_with_no_attempt_is_distinguished_from_a_failed_one(self):
        out = self._run()
        self.assertIn("issuance was never started", out)
        self.assertNotIn("existing_customer", out)

    def test_a_database_without_the_archive_is_not_reported_as_no_evidence(self):
        """"No archive table" and "no archived row" lead to different actions."""
        self._session(account_state="review_required",
                      account_failure_category="existing_customer")
        out = self._run()
        self.assertIn("former_provider_evidence=no", out)

    def test_customers_with_an_account_are_not_listed(self):
        self.wallet.account_number = "0123456789"
        self.wallet.save(update_fields=["account_number"])
        out = self._run()
        self.assertIn("No verified customer is missing a funding account", out)

    def test_json_output_is_ids_and_states_only(self):
        import json

        self._session(account_state="review_required",
                      account_failure_category="existing_customer")
        payload = json.loads(self._run(as_json=True))
        self.assertTrue(payload["read_only"])
        self.assertEqual(payload["count"], 1)
        row = payload["customers"][0]
        self.assertEqual(row["user_id"], self.user.pk)
        self.assertEqual(row["failure_category"], "existing_customer")
        blob = json.dumps(payload)
        for secret in (PHONE, EMAIL, OLD_ACCOUNT, HOLDER):
            self.assertNotIn(secret, blob)

    def test_it_writes_nothing(self):
        """Asserted rather than assumed: this is run against production."""
        session = self._session(account_state="review_required",
                                account_failure_category="existing_customer")
        before = (self.wallet.account_number, session.status, session.account_state,
                  WemaFaceSession.objects.count(), User.objects.count())
        self._run()
        self.wallet.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual(
            (self.wallet.account_number, session.status, session.account_state,
             WemaFaceSession.objects.count(), User.objects.count()), before)
