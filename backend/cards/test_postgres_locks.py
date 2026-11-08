"""Compile the actual card service queries with PostgreSQL, even in SQLite CI."""
from contextlib import contextmanager
from decimal import Decimal
from unittest.mock import patch

from django.db.backends.postgresql.base import DatabaseWrapper
from django.db.models.query import QuerySet
from django.test import TestCase

from wallet.models import Transaction
from wallet.tests import make_user

from cards.issuance import claim_card_issuance, finalize_card_issuance
from cards.models import CardIssuance, VirtualCard
from cards.services import claim_card_funding, finalize_card_funding


@contextmanager
def postgres_lock_statements(model):
    # SQL compilation needs backend features and quoting, but never a socket.
    pg = DatabaseWrapper({
        "ENGINE": "django.db.backends.postgresql", "NAME": "unused",
        "USER": "", "PASSWORD": "", "HOST": "", "PORT": "", "OPTIONS": {},
        "TIME_ZONE": None, "AUTOCOMMIT": False,
    }, alias="card_lock_compiler")
    statements = []
    original_fetch = QuerySet._fetch_all

    def capture_fetch(queryset):
        if (queryset.model is model and queryset.query.select_for_update
                and queryset.query.select_related):
            sql, _ = queryset.query.get_compiler(connection=pg).as_sql()
            statements.append(sql)
        return original_fetch(queryset)

    with patch.object(pg, "get_autocommit", return_value=False), \
            patch.object(QuerySet, "_fetch_all", new=capture_fetch):
        yield statements
    if pg.connection is not None:
        raise AssertionError("PostgreSQL compilation opened an unexpected connection")


class PostgreSQLCardLockTests(TestCase):
    def setUp(self):
        self.user, _ = make_user(
            "08020009991", "card-postgres-lock@zitch.test", balance="5000")

    def test_nullable_card_join_locks_only_issuance_and_preserves_pending_intent(self):
        claim = claim_card_issuance(self.user, "postgres-null-card", "issuer")
        self.assertIsNone(claim.intent.card_id)
        with postgres_lock_statements(CardIssuance) as statements:
            result = finalize_card_issuance(
                claim.intent, {"pending": True, "status": "PROCESSING"}, "ADA TEST")
        self.assertTrue(statements)
        self.assertIn('LEFT OUTER JOIN "cards_virtualcard"', statements[0])
        self.assertTrue(statements[0].endswith('FOR UPDATE OF "cards_cardissuance"'))
        self.assertEqual(result.state, CardIssuance.PENDING)
        self.assertIsNone(result.card_id)
        self.assertFalse(VirtualCard.objects.filter(user=self.user).exists())

    def test_funding_user_join_locks_only_ledger_and_projects_success_once(self):
        card = VirtualCard.objects.create(
            user=self.user, card_token="issuer-lock-test", last4="1234", expiry="12/29")
        txn = claim_card_funding(self.user, card, Decimal("100"), "postgres-funding")
        with postgres_lock_statements(Transaction) as statements:
            first = finalize_card_funding(txn, {"success": True})
            replay = finalize_card_funding(txn, {"success": True})
        self.assertTrue(statements)
        for sql in statements:
            self.assertIn('INNER JOIN "accounts_user"', sql)
            self.assertTrue(sql.endswith('FOR UPDATE OF "wallet_transaction"'))
        self.assertEqual((first, replay), ("success", "success"))
        card.refresh_from_db()
        txn.refresh_from_db()
        self.assertEqual(card.balance, Decimal("100"))
        self.assertEqual(txn.transaction_status, Transaction.SUCCESS)
