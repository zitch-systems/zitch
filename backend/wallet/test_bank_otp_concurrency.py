"""PostgreSQL proof that concurrent confirmations submit one bank OTP."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from unittest.mock import patch

from django.db import close_old_connections, connections
from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone

from accounts.models import hash_identifier
from wallet.models import WemaProvisioningAttempt
from wallet.tests import make_user
from wallet.views import complete_wema_provisioning


@skipUnlessDBFeature("has_select_for_update")
@override_settings(WEMA={"SIMULATION": False, "CHANNEL_ID": "otp-race", "KEYS": {"wallet": "test"}})
class BankOtpConcurrencyTests(TransactionTestCase):
    def test_two_confirmations_consume_one_provider_code(self):
        user, _ = make_user("08010000673", "bankrace@zitch.test", identity_verified=False, tier=0)
        attempt = WemaProvisioningAttempt.objects.create(user=user, tracking_id="BANK-RACE",
            identity_type="bvn", identity_hash=hash_identifier("22222222222"), identity_last4="2222",
            expires_at=timezone.now() + timedelta(minutes=5))
        bank_entered, release_bank, second_lock = Event(), Event(), Event()
        lock_query = WemaProvisioningAttempt.objects.select_for_update

        def accepted(*args, **kwargs):
            bank_entered.set()
            if not release_bank.wait(5):
                raise AssertionError("Bank response was not released")
            return {"success": True}

        def locking(*args, **kwargs):
            if bank_entered.is_set():
                second_lock.set()
            return lock_query(*args, **kwargs)

        def confirm():
            close_old_connections()
            try:
                return complete_wema_provisioning(user, "123456", attempt.tracking_id)
            finally:
                connections.close_all()

        with patch.object(WemaProvisioningAttempt.objects, "select_for_update", side_effect=locking), \
             patch("utility.wema.validate_wallet_otp", side_effect=accepted) as bank, \
             patch("utility.wema.get_account_details", return_value={"success": False}), \
             ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(confirm)
            self.assertTrue(bank_entered.wait(5))
            second = pool.submit(confirm)
            try:
                self.assertTrue(second_lock.wait(5))
            finally:
                release_bank.set()
            outcomes = [first.result(timeout=10), second.result(timeout=10)]
        self.assertEqual(bank.call_count, 1)
        self.assertEqual([status for _, status in outcomes], [202, 202])
        attempt.refresh_from_db()
        self.assertIsNotNone(attempt.otp_verified_at)
