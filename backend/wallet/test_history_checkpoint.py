"""Regression tests for durable partner-bank statement coverage."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings

from wallet.models import BankHistoryCheckpoint, Transaction, Wallet
from wallet.reconciliation import reconcile_account_history
from wallet.services import apply_wema_credit
from wallet.tests import make_user


def _credit(
    reference,
    amount="100.00",
    status="Successfull",
    transaction_date="2026-10-01T10:00:00Z",
):
    return {
        "referenceId": reference,
        "amount": amount,
        "creditType": "Credit",
        "status": status,
        "narration": "Bank transfer",
        "sender": "TEST BANK / SENDER",
        "date": transaction_date,
    }


@override_settings(
    PAYMENT_PROVIDER="wema",
    WEMA_HISTORY_WINDOW_DAYS=31,
    WEMA_HISTORY_MAX_WINDOWS=8,
)
class BankHistoryCheckpointTests(TestCase):
    def setUp(self):
        self.user, _ = make_user("08071110001", "history-checkpoint@zitch.test")
        self.wallet = Wallet.objects.get(user=self.user)
        self.wallet.account_number = "0123456701"
        self.wallet.save(update_fields=["account_number"])
        self.today = date(2026, 10, 1)
        self._set_wallet_created(self.wallet, self.today - timedelta(days=12))
        BankHistoryCheckpoint.objects.create(
            wallet=self.wallet,
            account_number=self.wallet.account_number,
            opening_review_required=False,
        )

    def _set_wallet_created(self, wallet, day):
        Wallet.objects.filter(pk=wallet.pk).update(
            created=datetime.combine(day, datetime.min.time(), tzinfo=UTC)
        )
        wallet.refresh_from_db()

    def _checkpoint(self, wallet=None, account_number=None):
        wallet = wallet or self.wallet
        return BankHistoryCheckpoint.objects.get(
            wallet=wallet,
            account_number=account_number or wallet.account_number,
        )

    def test_recovers_credit_after_outage_longer_than_two_day_lookback(self):
        old_coverage = self.today - timedelta(days=8)
        BankHistoryCheckpoint.objects.filter(
            wallet=self.wallet, account_number=self.wallet.account_number
        ).update(
            covered_through=old_coverage
        )
        missed_credit = _credit("AFTER-LONG-OUTAGE", "425.50")

        with patch(
            "utility.wema.get_transactions",
            return_value={"success": True, "transactions": [missed_credit]},
        ) as history:
            result = reconcile_account_history(self.wallet, today=self.today)

        self.assertEqual(
            history.call_args.args,
            (
                self.wallet.account_number,
                (old_coverage - timedelta(days=2)).isoformat(),
                self.today.isoformat(),
            ),
        )
        self.assertEqual(result["credited"], 1)
        self.assertEqual(self._checkpoint().covered_through, self.today)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("425.50"))

    def test_overlap_reobserves_a_credit_without_duplicating_it(self):
        BankHistoryCheckpoint.objects.filter(
            wallet=self.wallet, account_number=self.wallet.account_number
        ).update(
            covered_through=self.today - timedelta(days=1)
        )
        row = _credit("OVERLAP-ONCE", "75")
        response = {"success": True, "transactions": [row]}

        with patch("utility.wema.get_transactions", return_value=response) as history:
            first = reconcile_account_history(self.wallet, today=self.today)
            second = reconcile_account_history(self.wallet, today=self.today)

        self.assertEqual(first["credited"], 1)
        self.assertEqual(second["credited"], 0)
        self.assertEqual(history.call_count, 2)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("75.00"))
        self.assertEqual(
            Transaction.objects.filter(reference="WEMA-CR-OVERLAP-ONCE").count(),
            1,
        )

    def test_fetch_failure_does_not_advance_existing_coverage(self):
        old_coverage = self.today - timedelta(days=6)
        checkpoint = BankHistoryCheckpoint.objects.get(
            wallet=self.wallet, account_number=self.wallet.account_number
        )
        checkpoint.covered_through = old_coverage
        checkpoint.save(update_fields=["covered_through", "updated"])

        with patch(
            "utility.wema.get_transactions",
            return_value={
                "success": False,
                "transactions": [],
                "complete": False,
                "error_code": "provider_timeout",
            },
        ):
            result = reconcile_account_history(self.wallet, today=self.today)

        checkpoint.refresh_from_db()
        self.assertEqual(checkpoint.covered_through, old_coverage)
        self.assertEqual(checkpoint.last_error_code, "provider_timeout")
        self.assertEqual(result["error_code"], "provider_timeout")

    def test_partial_apply_failure_retries_without_double_credit(self):
        first_row = _credit("PARTIAL-FIRST", "125")
        second_row = _credit("PARTIAL-SECOND", "275")
        response = {"success": True, "transactions": [first_row, second_row]}

        def fail_second(wallet, row, self_refs=None):
            if row["referenceId"] == "PARTIAL-SECOND":
                raise RuntimeError("simulated crash while applying row")
            return apply_wema_credit(wallet, row, self_refs=self_refs)

        with patch("utility.wema.get_transactions", return_value=response), patch(
            "wallet.reconciliation.apply_wema_credit", side_effect=fail_second
        ):
            failed = reconcile_account_history(self.wallet, today=self.today)

        checkpoint = self._checkpoint()
        self.assertIsNone(checkpoint.covered_through)
        self.assertEqual(failed["error_code"], "history_apply_failed")
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("125.00"))

        with patch("utility.wema.get_transactions", return_value=response):
            retried = reconcile_account_history(self.wallet, today=self.today)

        self.assertEqual(retried["credited"], 1)
        self.assertEqual(self._checkpoint().covered_through, self.today)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("400.00"))
        self.assertEqual(
            Transaction.objects.filter(
                reference__in=["WEMA-CR-PARTIAL-FIRST", "WEMA-CR-PARTIAL-SECOND"]
            ).count(),
            2,
        )

    def test_untrusted_fetch_shapes_never_advance_coverage(self):
        responses = {
            "malformed": {
                "success": True,
                "transactions": {"referenceId": "NOT-A-LIST"},
                "error_code": "malformed_history",
            },
            "incomplete": {
                "success": True,
                "transactions": [],
                "complete": False,
                "error_code": "incomplete_history",
            },
            "mock": {
                "success": True,
                "mock": True,
                "transactions": [],
            },
        }

        for index, (label, response) in enumerate(responses.items(), start=2):
            with self.subTest(label=label):
                user, _ = make_user(
                    f"0807111000{index}", f"history-{label}@zitch.test"
                )
                wallet = Wallet.objects.get(user=user)
                wallet.account_number = f"012345670{index}"
                wallet.save(update_fields=["account_number"])
                self._set_wallet_created(wallet, self.today - timedelta(days=4))
                BankHistoryCheckpoint.objects.create(
                    wallet=wallet,
                    account_number=wallet.account_number,
                    opening_review_required=False,
                )

                with patch("utility.wema.get_transactions", return_value=response):
                    reconcile_account_history(wallet, today=self.today)

                checkpoint = self._checkpoint(wallet)
                self.assertIsNone(checkpoint.covered_through)
                self.assertTrue(checkpoint.last_error_code)

    def test_pending_and_unknown_credits_pin_window_but_confirmed_credit_applies(self):
        rows = [
            _credit("STILL-PENDING", "50", status="Pending"),
            _credit("CONFIRMED-AMONG-UNSETTLED", "300"),
            _credit("UNKNOWN-STATE", "70", status="Unexpected"),
        ]

        with patch(
            "utility.wema.get_transactions",
            return_value={"success": True, "transactions": rows},
        ):
            result = reconcile_account_history(self.wallet, today=self.today)

        checkpoint = self._checkpoint()
        self.assertIsNone(checkpoint.covered_through)
        self.assertEqual(checkpoint.last_error_code, "history_credit_unsettled")
        self.assertEqual(result["credited"], 1)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("300.00"))
        self.assertTrue(
            Transaction.objects.filter(
                reference="WEMA-CR-CONFIRMED-AMONG-UNSETTLED"
            ).exists()
        )

    def test_conflicting_duplicate_owner_or_amount_never_advances_coverage(self):
        other, _ = make_user("08071110009", "history-other@zitch.test")
        Transaction.objects.create(
            user=other,
            service="Conflicting credit",
            amount=Decimal("100.00"),
            direction=Transaction.IN,
            transaction_status=Transaction.SUCCESS,
            reference="WEMA-CR-WRONG-OWNER",
        )
        Transaction.objects.create(
            user=self.user,
            service="Conflicting credit",
            amount=Decimal("99.00"),
            direction=Transaction.IN,
            transaction_status=Transaction.SUCCESS,
            reference="WEMA-CR-WRONG-AMOUNT",
        )
        rows = [
            _credit("WRONG-OWNER", "100"),
            _credit("WRONG-AMOUNT", "100"),
        ]

        with patch(
            "utility.wema.get_transactions",
            return_value={"success": True, "transactions": rows},
        ):
            result = reconcile_account_history(self.wallet, today=self.today)

        checkpoint = self._checkpoint()
        self.assertIsNone(checkpoint.covered_through)
        self.assertEqual(checkpoint.last_error_code, "history_credit_not_accounted")
        self.assertEqual(result["credited"], 0)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("0.00"))

    def test_replacement_account_has_an_independent_checkpoint(self):
        first_account = self.wallet.account_number
        second_account = "0123456799"

        with patch(
            "utility.wema.get_transactions",
            return_value={"success": True, "transactions": []},
        ) as history:
            reconcile_account_history(self.wallet, today=self.today)
            self.wallet.account_number = second_account
            self.wallet.save(update_fields=["account_number"])
            BankHistoryCheckpoint.objects.create(
                wallet=self.wallet,
                account_number=second_account,
                opening_review_required=False,
            )
            reconcile_account_history(self.wallet, today=self.today)

        checkpoints = BankHistoryCheckpoint.objects.filter(wallet=self.wallet)
        self.assertEqual(checkpoints.count(), 2)
        self.assertEqual(
            set(checkpoints.values_list("account_number", flat=True)),
            {first_account, second_account},
        )
        self.assertEqual(
            set(checkpoints.values_list("covered_through", flat=True)),
            {self.today},
        )
        self.assertEqual(
            [call.args[0] for call in history.call_args_list],
            [first_account, second_account],
        )

    @override_settings(WEMA_HISTORY_WINDOW_DAYS=3, WEMA_HISTORY_MAX_WINDOWS=2)
    def test_window_budget_makes_incremental_durable_progress(self):
        start = self.today - timedelta(days=9)
        self._set_wallet_created(self.wallet, start)

        with patch(
            "utility.wema.get_transactions",
            return_value={"success": True, "transactions": []},
        ) as history:
            first = reconcile_account_history(self.wallet, today=self.today)

        checkpoint = self._checkpoint()
        self.assertEqual(checkpoint.covered_through, start + timedelta(days=5))
        self.assertTrue(first["backlog"])
        self.assertEqual(first["windows"], 2)
        self.assertEqual(checkpoint.last_error_code, "history_backlog")
        self.assertEqual(
            [call.args[1:] for call in history.call_args_list],
            [
                (start.isoformat(), (start + timedelta(days=2)).isoformat()),
                (
                    (start + timedelta(days=3)).isoformat(),
                    (start + timedelta(days=5)).isoformat(),
                ),
            ],
        )

        with patch(
            "utility.wema.get_transactions",
            return_value={"success": True, "transactions": []},
        ) as history:
            second = reconcile_account_history(self.wallet, today=self.today)

        checkpoint.refresh_from_db()
        self.assertEqual(checkpoint.covered_through, start + timedelta(days=8))
        self.assertTrue(second["backlog"])
        self.assertEqual(second["windows"], 2)
        self.assertEqual(
            [call.args[1:] for call in history.call_args_list],
            [
                (
                    (start + timedelta(days=3)).isoformat(),
                    (start + timedelta(days=5)).isoformat(),
                ),
                (
                    (start + timedelta(days=6)).isoformat(),
                    (start + timedelta(days=8)).isoformat(),
                ),
            ],
        )

    def test_legacy_attachment_keeps_opening_review_visible_after_empty_coverage(self):
        user, _ = make_user("08071110010", "history-legacy@zitch.test")
        wallet = Wallet.objects.get(user=user)
        wallet.account_number = "0123456710"
        wallet.save(update_fields=["account_number"])
        self._set_wallet_created(wallet, self.today - timedelta(days=30))

        with patch(
            "utility.wema.get_transactions",
            return_value={"success": True, "transactions": []},
        ):
            result = reconcile_account_history(wallet, today=self.today)

        checkpoint = self._checkpoint(wallet)
        self.assertTrue(checkpoint.opening_review_required)
        self.assertEqual(checkpoint.covered_through, self.today)
        self.assertEqual(checkpoint.last_error_code, "history_opening_review")
        self.assertEqual(result["error_code"], "history_opening_review")

    def test_pre_attachment_and_undated_credits_require_opening_review(self):
        cases = {
            "before-wallet": _credit(
                "BEFORE-WALLET",
                transaction_date="2026-09-18",
            ),
            "missing-date": _credit("MISSING-DATE"),
        }
        cases["missing-date"].pop("date")

        for index, (label, row) in enumerate(cases.items(), start=11):
            with self.subTest(label=label):
                user, _ = make_user(
                    f"080711100{index}", f"history-{label}@zitch.test"
                )
                wallet = Wallet.objects.get(user=user)
                wallet.account_number = f"01234567{index}"
                wallet.save(update_fields=["account_number"])
                self._set_wallet_created(wallet, date(2026, 9, 19))
                BankHistoryCheckpoint.objects.create(
                    wallet=wallet,
                    account_number=wallet.account_number,
                    opening_review_required=False,
                )

                with patch(
                    "utility.wema.get_transactions",
                    return_value={"success": True, "transactions": [row]},
                ):
                    result = reconcile_account_history(wallet, today=self.today)

                checkpoint = self._checkpoint(wallet)
                wallet.refresh_from_db()
                self.assertEqual(wallet.balance, Decimal("0.00"))
                self.assertFalse(
                    Transaction.objects.filter(
                        reference=f"WEMA-CR-{row['referenceId']}"
                    ).exists()
                )
                self.assertIsNone(checkpoint.covered_through)
                self.assertEqual(
                    checkpoint.last_error_code, "history_credit_opening_review"
                )
                self.assertEqual(
                    result["error_code"], "history_credit_opening_review"
                )
