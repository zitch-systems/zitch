"""Wema's "existing customer" answer attaches the account Wema already holds.

Wema refuses to open a second account for a customer who already has one under
Zitch's channel. The wallet used to wait forever for a callback that never comes;
it now reads the existing account back and attaches it when it is safely theirs.
"""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from wallet.models import BankHistoryCheckpoint, Wallet, WemaFaceSession
from wallet.services import attach_existing_bank_account
from wallet.tests import make_user
from whatsapp.models import AuditLog


def _found(name="EZE ADA CHIOMA", number="0123456789"):
    return {"success": True, "account_number": number, "account_name": name,
            "bank_name": "Wema Bank", "account_lookup_state": "found", "http_status": 200}


class ExistingCustomerAttachTests(TestCase):
    def setUp(self):
        self.user, _ = make_user("08011117001", "existing@example.test")
        WemaFaceSession.objects.create(
            user=self.user, state="s-existing", identity_type="bvn", identity_hash="h" * 64,
            status=WemaFaceSession.VERIFIED, account_state="rejected",
            account_failure_category="existing_customer",
            expires_at=timezone.now() - timedelta(hours=1))

    def _attach(self, lookup):
        with patch("utility.wema.get_account_details", return_value=lookup) as read:
            result = attach_existing_bank_account(self.user, using_bvn=True)
        return result, read

    def test_existing_account_is_attached_with_an_opening_review(self):
        (wallet, _detail), read = self._attach(_found())
        read.assert_called_once_with(self.user.phone, bvn=True)
        self.assertEqual(wallet.account_number, "0123456789")
        self.assertTrue(BankHistoryCheckpoint.objects.get(wallet=wallet).opening_review_required)
        self.assertTrue(AuditLog.objects.filter(action="wema_existing_account_attached").exists())

    def test_holder_name_must_match_first_and_last_name(self):
        (wallet, _), _ = self._attach(_found(name="OKAFOR BELLO"))
        self.assertIsNone(wallet)
        self.assertEqual(Wallet.objects.get(user=self.user).account_number, "")

    def test_unverified_identity_never_triggers_the_lookup(self):
        type(self.user).objects.filter(pk=self.user.pk).update(bvn_verified=False)
        self.user.refresh_from_db()
        (wallet, _), read = self._attach(_found())
        self.assertIsNone(wallet)
        read.assert_not_called()

    def test_an_account_on_another_wallet_is_not_taken(self):
        other, _ = make_user("08011117002", "other@example.test")
        Wallet.objects.filter(user=other).update(account_number="0123456789")
        (wallet, _), _ = self._attach(_found())
        self.assertIsNone(wallet)

    def test_no_account_from_the_bank_attaches_nothing(self):
        (wallet, _), _ = self._attach({"success": False, "account_number": "",
                                       "account_lookup_state": "not_found"})
        self.assertIsNone(wallet)
        self.assertEqual(Wallet.objects.get(user=self.user).account_number, "")


class Tier2NeedsAnAccountTests(TestCase):
    def test_tier2_link_is_refused_without_an_account_number(self):
        from whatsapp.verification_web import _verification_allowed
        user, _ = make_user("08011117003", "tier2@example.test")
        self.assertFalse(_verification_allowed(user, 2))
        Wallet.objects.filter(user=user).update(account_number="0123456780")
        self.assertTrue(_verification_allowed(user, 2))


class PreWemaVerifiedCustomerTests(TestCase):
    """Customer 34: BVN verified by 9PSB's OTP, never a Wema account."""

    def test_9psb_verified_bvn_takes_the_standard_wema_opening(self):
        from accounts.models import IdentityProof
        from wallet.partnership import first_partnership_setup
        user, _ = make_user("08011117004", "pre-wema@example.test")
        type(user).objects.filter(pk=user.pk).update(bvn_hash="b" * 64, nin_verified=False)
        user.refresh_from_db()
        self.assertFalse(first_partnership_setup(user))
        IdentityProof.objects.create(user=user, identity_type="bvn", identity_hash="b" * 64,
                                     source="9psb_otp")
        self.assertTrue(first_partnership_setup(user))
