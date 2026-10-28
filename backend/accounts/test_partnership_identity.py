"""Tier 1 uses Partnership and historical funding holds cannot change its rail."""
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import AccessToken, IdentityProof, User
from wallet.funding_test_fixtures import funding_hold
from utility import providers
from wallet.models import Wallet


@override_settings(BANK_ACCOUNT_PROVIDER="partnership", WEMA_PARTNERSHIP_MODE="active",
                   KYC_PROVIDER="wema", DEBUG=True, TESTING=True, RATELIMIT_ENABLE=False)
class PartnershipIdentityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="partnership-identity", phone="08010000001",
            first_name="Ada", last_name="Eze", email_verified=True, phone_verified=True)
        Wallet.objects.create(user=self.user)
        self.token = AccessToken.issue(self.user).key
        network = patch("requests.sessions.Session.request", side_effect=AssertionError("Unexpected network"))
        network.start()
        self.addCleanup(network.stop)

    def post(self, path, **data):
        return self.client.post(path, {"access_token": self.token, **data}, content_type="application/json")

    def legacy_hold(self):
        return funding_hold(self.user, "100", "8110000001")

    def test_stale_provider_setting_and_prembly_credentials_cannot_redirect_tier_one(self):
        with override_settings(KYC_PROVIDER="prembly", BANK_ACCOUNT_PROVIDER="wema_vas",
                               PREMBLY={"API_KEY": "configured", "APP_ID": "configured"}), \
                patch("utility.providers.prembly_verify_bvn") as prembly_bvn, \
                patch("utility.providers.prembly_verify_nin") as prembly_nin, \
                patch("utility.wema.verify_bvn", return_value={"success": False}) as bvn, \
                patch("utility.wema.verify_nin", return_value={"success": False}) as nin:
            self.assertEqual(providers.kyc_provider(), "wema")
            providers.verify_bvn("12345678901")
            providers.verify_nin("12345678901")
        bvn.assert_called_once()
        nin.assert_called_once()
        prembly_bvn.assert_not_called()
        prembly_nin.assert_not_called()

    def test_held_account_cannot_start_or_confirm_identity_using_any_provider(self):
        self.legacy_hold()
        with patch("accounts.views._lookup_identity") as lookup, \
                patch("accounts.views._start_bank_identity") as bank, \
                patch("accounts.views._confirm_identity_ownership_challenge") as confirm:
            for path in ("bvn/start/", "bvn/", "nin/", "bvn/confirm/", "nin/confirm/", "face/start/"):
                with self.subTest(path=path):
                    response = self.post("/api/kyc/" + path,
                        bvn="12345678901", nin="12345678901", otp="123456")
                    self.assertEqual(response.status_code, 409, response.content)
                    self.assertEqual(response.json()["code"], "legacy_funding_review")
        lookup.assert_not_called()
        bank.assert_not_called()
        confirm.assert_not_called()
        self.assertFalse(IdentityProof.objects.filter(user=self.user).exists())

    def test_held_status_retains_partnership_ladder_without_exposing_retired_account(self):
        account = self.legacy_hold()
        data = self.post("/api/kyc/status/").json()
        self.assertEqual(data["identity_verification_provider"], "wema")
        self.assertEqual(data["account_provider"], "partnership")
        self.assertTrue(data["legacy_funding_review"])
        self.assertEqual(data["account_setup_state"], "legacy_funding_review")
        self.assertFalse(data["identity_face_available"])
        self.assertFalse(data["has_wema_account"])
        self.assertFalse(data["bank_upgrade_required"])
        self.assertTrue(data["address_verification_required"])
        self.assertEqual(data["address_rail"], "wema")
        self.assertNotIn("8110000001", str(data))

    def test_old_provider_setting_cannot_bypass_partnership_address_verification(self):
        self.user.bvn_verified = self.user.nin_verified = self.user.face_verified = True
        self.user.save()
        with override_settings(BANK_ACCOUNT_PROVIDER="wema_vas"), \
                patch("accounts.views.kyc_verify_address") as document, \
                patch("utility.wema.upgrade_tier3") as bank:
            response = self.post("/api/kyc/address/", address="Submitted address", document="ZmFrZQ==")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "address_verification_unavailable")
        document.assert_not_called()
        bank.assert_not_called()
        self.user.refresh_from_db()
        self.assertFalse(self.user.address_verified)
