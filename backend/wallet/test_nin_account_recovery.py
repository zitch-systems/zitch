"""NIN ownership stays usable while bank account issuance is being recovered."""
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase

from accounts.models import User
from wallet.services import repair_missing_funding_accounts
from wallet.tests import make_user


class NinAccountRecoveryTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user, self.token = make_user("08010009201", "nin-recovery@example.test")
        User.objects.filter(pk=self.user.pk).update(bvn_verified=False, nin_verified=True)
        self.user.refresh_from_db()

    def test_recovery_sweep_includes_nin_only_customer_and_keeps_correct_rail(self):
        with patch("wallet.services.attach_existing_bank_account", return_value=(None, "pending")) as recover:
            result = repair_missing_funding_accounts()
        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["repaired"], 0)
        recover.assert_called_once_with(self.user, using_bvn=False)

    def test_no_input_account_create_keeps_verified_nin_and_does_not_restart_identity(self):
        with patch("wallet.views.attach_existing_bank_account", return_value=(None, "pending")) as recover, \
                patch("utility.wema.create_wallet_request") as create:
            response = self.client.post("/api/wallet/account/create/", {
                "access_token": self.token,
            }, content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["success"])
        self.assertTrue(response.json()["nin_verified"])
        self.assertFalse(response.json()["bvn_verified"])
        self.assertIn("NIN is already verified", response.json()["message"])
        create.assert_not_called()
        recover.assert_called_once_with(self.user, using_bvn=False)

    def test_verified_nin_is_not_submitted_to_account_creation_again(self):
        for path in ("/api/wallet/account/create/", "/api/wallet/wema/create/"):
            with self.subTest(path=path), patch("utility.wema.create_wallet_request") as create:
                response = self.client.post(path, {
                    "access_token": self.token, "nin": "33333333333",
                }, content_type="application/json")
            self.assertEqual(response.status_code, 409, response.content)
            self.assertIn("NIN is already verified", response.json()["message"])
            create.assert_not_called()
