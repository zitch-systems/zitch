"""A cached table list must not outlive the schema it describes.

`previous_provider_cutover_blockers` caches the retired provider's table names
for five minutes, because introspection is not free and the schema almost
never moves. The retired-schema cleanup *does* drop those tables, so the cache
can name a table that is gone. Querying it then fails, and that failure used to
be reported as "previous provider state unavailable" -- which blocks funding
for every customer for the rest of the TTL, over tables that are simply no
longer there, with nothing to make it recover.

What is pinned here: a dropped table reads as "no evidence", evidence that
genuinely cannot be read still blocks, and a held archive row still blocks.
The last two matter most -- the point of the guard is to fail closed, and
making it recover from a stale list must not cost that.
"""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.utils import timezone

from wallet.partnership import previous_provider_cutover_blockers
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

#: Present, but not readable the way the guard needs to read it. Standing in
#: for a schema the query cannot answer against -- a renamed or dropped
#: column, a revoked grant -- which is the case that must still fail closed.
ARCHIVE_DDL_UNREADABLE = """
CREATE TABLE banking_previousbankarchive (
    id integer PRIMARY KEY,
    wallet_id integer NOT NULL
)
"""


class PreviousProviderSourceTableCacheTests(TestCase):

    def setUp(self):
        self.user = User.objects.create(
            username="ada", phone="08022220001", email="ada@zitch.test",
            first_name="Ada", last_name="Eze")
        self.wallet = get_or_create_wallet(self.user)
        cache.clear()
        self.addCleanup(cache.clear)
        self.addCleanup(self._drop)

    def _drop(self):
        with connection.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS banking_previousbankarchive")

    def _create(self, ddl=ARCHIVE_DDL):
        with connection.cursor() as cur:
            cur.execute(ddl)

    def _row(self, *, balance="0.00", pending=0, review=False):
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO banking_previousbankarchive (id, wallet_id, "
                "account_number, account_name, bank_name, account_reference, "
                "balance, account_metadata, pending_transactions, "
                "review_required, created) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [1, self.wallet.pk, "0123456789", "ADA EZE", "Wema Bank", "REF",
                 balance, "{}", pending, review, timezone.now()])

    def test_a_dropped_table_is_not_reported_as_unreadable_evidence(self):
        """The cache outliving the retired-schema cleanup must not block anyone.

        Without recovery this returns previous_provider_state_unavailable for
        up to five minutes after the table goes away, which blocks funding for
        every customer over a table that no longer exists.
        """
        self._create()
        self.assertEqual(previous_provider_cutover_blockers(self.user), [])
        self._drop()  # what the retired-schema cleanup does
        self.assertEqual(previous_provider_cutover_blockers(self.user), [])

    def test_a_held_row_still_blocks_through_a_warm_cache(self):
        """Recovering from a stale list must not cost the guard its job."""
        self._create()
        self.assertEqual(previous_provider_cutover_blockers(self.user), [])
        self._row(balance="2826.00", review=True)
        self.assertEqual(previous_provider_cutover_blockers(self.user),
                         ["archived_wema_balance_review"])

    def test_evidence_that_genuinely_cannot_be_read_still_blocks(self):
        """A table that is there but unanswerable is unavailable, so fail closed."""
        self._create(ARCHIVE_DDL_UNREADABLE)
        self.assertEqual(previous_provider_cutover_blockers(self.user),
                         ["previous_provider_state_unavailable"])
        # ...and still blocks on the next call, when the list is warm.
        self.assertEqual(previous_provider_cutover_blockers(self.user),
                         ["previous_provider_state_unavailable"])

    def test_recovering_leaves_the_connection_usable(self):
        """The retry runs in a savepoint, so a caller's transaction survives.

        Callers are inside a transaction (an ATOMIC_REQUESTS view, a wallet
        debit). A failed statement leaves the whole transaction unusable, so
        without the savepoint the recovery could not query at all -- and the
        caller's own later writes would fail too.
        """
        self._create()
        previous_provider_cutover_blockers(self.user)
        self._drop()
        self.assertEqual(previous_provider_cutover_blockers(self.user), [])
        # The caller can still use the connection it lent us.
        self.assertEqual(User.objects.filter(pk=self.user.pk).count(), 1)
        self.wallet.account_name = "ADA EZE"
        self.wallet.save(update_fields=["account_name"])

    def test_a_database_that_never_carried_the_archive_is_not_blocked(self):
        self.assertEqual(previous_provider_cutover_blockers(self.user), [])

    def test_a_user_with_no_wallet_is_not_blocked(self):
        other = User.objects.create(
            username="noa", phone="08022220002", email="noa@zitch.test")
        self.assertEqual(previous_provider_cutover_blockers(other), [])
