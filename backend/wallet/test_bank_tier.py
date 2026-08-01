"""The partner bank's own tier caps.

ALAT runs its own tier ladder on the NUBAN (Tier 1 = BVN or NIN, Tier 2 = BVN and
NIN, Tier 3 = both plus address verification) with its own inflow / daily-spend /
balance limits, and enforces them regardless of our KYC tier. Where ours is looser,
the gateway refuses the transfer — so we check the bank's ceiling too and turn that
into a clear message instead of a failed payout.
"""
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from common.http import send_limit_error
from utility import wema
from wallet.models import Transaction, Wallet
from wallet.services import bank_spend_error, sync_bank_tier
from wallet.tests import make_user


class BankTierLimitTests(TestCase):
    def test_published_limits_match_the_bank_guide(self):
        # Values are quoted from ALAT's Getting Started guide (2026-07-27); if the bank
        # revises them this test is the tripwire.
        self.assertEqual(wema.bank_tier_limit(1, "daily_spend"), Decimal("30000"))
        self.assertEqual(wema.bank_tier_limit(1, "single_inflow"), Decimal("50000"))
        self.assertEqual(wema.bank_tier_limit(1, "max_balance"), Decimal("300000"))
        self.assertEqual(wema.bank_tier_limit(2, "daily_spend"), Decimal("100000"))
        self.assertEqual(wema.bank_tier_limit(2, "max_balance"), Decimal("500000"))
        self.assertIsNone(wema.bank_tier_limit(3, "daily_spend"))     # tier 3 uncapped

    def test_unknown_tier_asserts_no_cap(self):
        # Tier 0 means "never read back" — it must not invent a restriction.
        self.assertIsNone(wema.bank_tier_limit(0, "daily_spend"))
        self.assertIsNone(wema.bank_tier_limit(None, "daily_spend"))


class BankSpendErrorTests(TestCase):
    def setUp(self):
        self.user, _ = make_user("08031110001", "bt@zitch.app", tier=2)
        Wallet.objects.filter(user=self.user).update(account_number="0155500099")

    def _set_bank_tier(self, tier):
        Wallet.objects.filter(user=self.user).update(bank_tier=tier)

    def test_unknown_tier_on_a_provisioned_account_assumes_tier1(self):
        Wallet.objects.filter(user=self.user).update(bank_tier=0, account_number="0155500099")
        self.assertIsNotNone(bank_spend_error(self.user, Decimal("30001")))

    def test_unprovisioned_account_has_no_bank_tier_cap(self):
        Wallet.objects.filter(user=self.user).update(bank_tier=0, account_number="")
        self.assertIsNone(bank_spend_error(self.user, Decimal("999999")))

    def test_blocks_above_the_bank_tier1_daily_cap(self):
        self._set_bank_tier(1)
        self.assertIsNone(bank_spend_error(self.user, Decimal("30000")))       # at cap
        msg = bank_spend_error(self.user, Decimal("30001"))
        self.assertIsNotNone(msg)
        self.assertIn("30,000", msg)

    def _payout(self, amount, status=Transaction.SUCCESS, bank=True):
        return Transaction.objects.create(
            user=self.user, service="transfer", amount=Decimal(amount),
            direction=Transaction.OUT, transaction_status=status,
            reference=f"REF-{Transaction.objects.count()}-{amount}",
            meta={"bank": "035"} if bank else {"vtu": True})

    def test_the_daily_cap_is_cumulative_not_per_transfer(self):
        # ALAT publishes this as "Daily Max Spend". Ten transfers each under the cap
        # must not be able to sum past it — the gateway would refuse whichever one
        # crossed, which is the debit-then-reverse this check exists to prevent.
        self._set_bank_tier(1)                       # 30,000/day
        for _ in range(9):
            self._payout("3000")                     # 27,000 spent
        self.assertIsNone(bank_spend_error(self.user, Decimal("3000")))    # lands exactly on cap
        msg = bank_spend_error(self.user, Decimal("3001"))
        self.assertIsNotNone(msg)
        self.assertIn("3,000", msg)                  # tells them what is actually left

    def test_pending_payouts_count_against_the_day(self):
        # A payout in flight can still settle; ignoring it would let a burst of
        # concurrent transfers each see an empty day.
        self._set_bank_tier(1)
        self._payout("29000", status=Transaction.PENDING)
        self.assertIsNotNone(bank_spend_error(self.user, Decimal("2000")))

    def test_failed_payouts_do_not_count(self):
        self._set_bank_tier(1)
        self._payout("29000", status=Transaction.FAILED)   # already refunded
        self.assertIsNone(bank_spend_error(self.user, Decimal("1000")))

    def test_non_bank_spend_does_not_count(self):
        # A VTU purchase settles with the VAS provider and never debits the NUBAN.
        self._set_bank_tier(1)
        self._payout("29000", bank=False)
        self.assertIsNone(bank_spend_error(self.user, Decimal("1000")))

    def test_exhausted_day_says_so_plainly(self):
        self._set_bank_tier(1)
        self._payout("30000")
        msg = bank_spend_error(self.user, Decimal("100"))
        self.assertIn("reached", msg)

    def test_tier3_is_uncapped(self):
        self._set_bank_tier(3)
        self.assertIsNone(bank_spend_error(self.user, Decimal("10000000")))

    def test_send_limit_error_applies_the_bank_ceiling(self):
        # Our own tier-2 per-transaction limit is 200,000 — well above the bank's
        # 100,000 daily cap at ITS tier 2. The tighter of the two must win, otherwise
        # the payout is accepted here and refused at the gateway.
        self._set_bank_tier(2)
        self.assertIsNone(send_limit_error(self.user, Decimal("90000")))
        self.assertIsNotNone(send_limit_error(self.user, Decimal("150000")))

    def test_our_own_tier_limit_still_applies_first(self):
        # The bank ceiling is additional, never a replacement.
        user, _ = make_user("08031110002", "bt2@zitch.app", tier=0)
        Wallet.objects.filter(user=user).update(bank_tier=3)   # bank says unlimited
        msg = send_limit_error(user, Decimal("50000"))         # our tier 0 cap is 20,000
        self.assertIsNotNone(msg)
        self.assertIn("Tier 0", msg)


class SyncBankTierTests(TestCase):
    def setUp(self):
        self.user, _ = make_user("08031110003", "bt3@zitch.app")
        Wallet.objects.filter(user=self.user).update(account_number="0155500022")
        self.wallet = Wallet.objects.get(user=self.user)

    @patch("utility.wema.get_kyc_status",
           return_value={"success": True, "tier": "Tier 2"})
    def test_parses_the_banks_tier_string(self, _s):
        self.assertEqual(sync_bank_tier(self.wallet), 2)
        self.assertEqual(Wallet.objects.get(user=self.user).bank_tier, 2)

    @patch("utility.wema.get_kyc_status", return_value={"success": False})
    def test_failed_read_leaves_the_stored_tier_alone(self, _s):
        Wallet.objects.filter(user=self.user).update(bank_tier=2)
        self.assertEqual(sync_bank_tier(Wallet.objects.get(user=self.user)), 2)

    @patch("utility.wema.get_kyc_status",
           return_value={"success": True, "tier": "unclassified"})
    def test_unparseable_tier_is_not_invented(self, _s):
        self.assertEqual(sync_bank_tier(self.wallet), 0)
        self.assertEqual(Wallet.objects.get(user=self.user).bank_tier, 0)

    @patch("utility.wema.get_kyc_status")
    def test_unprovisioned_wallet_is_not_queried(self, mock_status):
        Wallet.objects.filter(user=self.user).update(account_number="")
        self.assertEqual(sync_bank_tier(Wallet.objects.get(user=self.user)), 0)
        mock_status.assert_not_called()
