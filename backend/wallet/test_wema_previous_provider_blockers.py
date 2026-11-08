"""9PSB-era account evidence must never be silently adopted by Wema."""
from unittest.mock import MagicMock, patch

from django.db import DatabaseError
from django.test import SimpleTestCase

from wallet.partnership import previous_provider_cutover_blockers


class PreviousProviderCutoverBlockerTests(SimpleTestCase):
    def _fake_wallet_and_db(self, table_names, fetch_results):
        wallet = patch("wallet.partnership.Wallet")
        conn = patch("wallet.partnership.connection")
        cache = patch("wallet.partnership.cache")
        wallet_mock = wallet.start()
        conn_mock = conn.start()
        cache_mock = cache.start()
        self.addCleanup(wallet.stop)
        self.addCleanup(conn.stop)
        self.addCleanup(cache.stop)
        wallet_mock.objects.filter.return_value.values_list.return_value.first.return_value = 17
        conn_mock.alias = "default"
        conn_mock.introspection.table_names.return_value = table_names
        cache_mock.get.return_value = None
        cursor = conn_mock.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = [(x,) for x in fetch_results]
        return cursor

    def test_previous_bank_account_blocks_new_rail(self):
        c = self._fake_wallet_and_db(["banking_bankaccount"], [True])
        self.assertEqual(previous_provider_cutover_blockers(MagicMock(pk=3)),
                         ["previous_provider_account_review"])
        self.assertIn("wallet_id = %s", c.execute.call_args.args[0])

    def test_previous_bank_archive_with_review_blocks(self):
        self._fake_wallet_and_db(["banking_previousbankarchive"], [True])
        self.assertEqual(previous_provider_cutover_blockers(MagicMock(pk=3)),
                         ["archived_wema_balance_review"])

    def test_without_old_bank_schema_has_no_legacy_account_blocker(self):
        self._fake_wallet_and_db([], [])
        self.assertEqual(previous_provider_cutover_blockers(MagicMock(pk=3)), [])

    def test_provider_state_read_error_fails_closed(self):
        c = self._fake_wallet_and_db(["banking_bankaccount"], [])
        c.execute.side_effect = DatabaseError("unavailable")
        self.assertEqual(previous_provider_cutover_blockers(MagicMock(pk=3)),
                         ["previous_provider_state_unavailable"])
