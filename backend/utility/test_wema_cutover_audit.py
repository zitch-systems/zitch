from django.test import SimpleTestCase

from utility.management.commands.wema_cutover_audit import evaluate_cutover


class WemaCutoverAuditTests(SimpleTestCase):
    def test_no_known_state_has_no_database_blocker_but_is_not_approval(self):
        self.assertEqual(evaluate_cutover({}), [])

    def test_applied_migrations_and_new_bank_accounts_require_review(self):
        codes = evaluate_cutover({"banking_migrations": 9, "bank_accounts": 2})
        self.assertIn("9psb_era_migrations_need_forward_compatibility", codes)
        self.assertIn("9psb_accounts_require_bank_side_closeout_and_reconciliation", codes)

    def test_detached_old_bank_accounts_are_not_restored_automatically(self):
        codes = evaluate_cutover({
            "archived_bank_wallets": 3, "archived_bank_reviews": 2,
            "active_wema_wallets": 0, "pending_transfers": 1,
            "unsettled_funding": 1,
        })
        self.assertIn("wallet_account_numbers_were_detached_during_previous_cutover", codes)
        self.assertIn("previous_wema_accounts_require_bank_confirmed_ownership", codes)
        self.assertIn("historical_bank_balances_or_pending_items_require_review", codes)
        self.assertIn("pending_wallet_transfers_require_provider_outcome_review", codes)
        self.assertIn("unsettled_funding_requires_attributable_bank_evidence", codes)

    def test_archive_still_requires_bank_confirmation_even_if_wallet_number_present(self):
        codes = evaluate_cutover({"archived_bank_wallets": 1, "active_wema_wallets": 1})
        self.assertNotIn("wallet_account_numbers_were_detached_during_previous_cutover", codes)
        self.assertIn("previous_wema_accounts_require_bank_confirmed_ownership", codes)


    def test_forward_migration_marker_clears_schema_only_blocker_not_bank_liability(self):
        codes = evaluate_cutover({
            "banking_migrations": 9, "wema_forward_migration_applied": True,
            "bank_accounts": 1,
        })
        self.assertNotIn("9psb_era_migrations_need_forward_compatibility", codes)
        self.assertIn("9psb_accounts_require_bank_side_closeout_and_reconciliation", codes)

    def test_unresolved_old_bank_payments_and_notifications_block_promotion(self):
        codes = evaluate_cutover({
            "wema_forward_migration_applied": True,
            "bank_instructions_unresolved": 2,
            "bank_notifications_unresolved": 1,
        })
        self.assertIn("unresolved_9psb_instructions_require_bank_finality", codes)
        self.assertIn("unresolved_9psb_inflows_require_banked_ledger_evidence", codes)

    def test_closed_old_bank_instructions_do_not_by_themselves_certify_launch(self):
        self.assertEqual(evaluate_cutover({
            "wema_forward_migration_applied": True,
            "bank_instructions_unresolved": 0,
            "bank_notifications_unresolved": 0,
        }), [])
