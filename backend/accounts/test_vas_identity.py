"""VAS always selects real Prembly lookup plus registered-phone ownership."""
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings

from accounts.models import AccessToken, IdentityProof, User, hash_identifier
from wallet.models import Wallet


@override_settings(BANK_ACCOUNT_PROVIDER="wema_vas", WEMA_PARTNERSHIP_MODE="archived",
                   KYC_PROVIDER="wema", DEBUG=True, TESTING=True, RATELIMIT_ENABLE=False)
class VasIdentityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="vas-identity", phone="08010000001",
            first_name="Ada", last_name="Eze", email_verified=True, phone_verified=True)
        Wallet.objects.create(user=self.user)
        self.token = AccessToken.issue(self.user).key
        self.raw = "12345678901"
        self.record = {"success": True, "phone": "08077778888", "first_name": "Ada", "last_name": "Eze"}
        network = patch("requests.sessions.Session.request", side_effect=AssertionError("Unexpected network"))
        network.start()
        self.addCleanup(network.stop)

    def post(self, path, **data):
        return self.client.post(path, {"access_token": self.token, **data}, content_type="application/json")

    def start(self, kind, record=None, configured=True):
        endpoint = "/api/kyc/bvn/start/" if kind == "bvn" else "/api/kyc/nin/"
        with patch("utility.providers._prembly_live", return_value=configured), \
                patch("utility.providers.prembly_verify_" + kind, return_value=record or self.record) as lookup, \
                patch("accounts.views._otp_code", return_value="123456"), \
                patch("accounts.views.send_sms", return_value={"success": True}) as sms, \
                patch("accounts.views.verify_bvn") as legacy_bvn, \
                patch("accounts.views.verify_nin") as legacy_nin:
            response = self.post(endpoint, **{kind: self.raw})
        legacy_bvn.assert_not_called()
        legacy_nin.assert_not_called()
        return response, lookup, sms

    def test_both_identities_require_prembly_and_sms_even_in_debug(self):
        for kind in ("bvn", "nin"):
            with self.subTest(kind=kind):
                response, lookup, sms = self.start(kind)
                self.assertEqual(response.status_code, 200, response.content)
                data = response.json()
                self.assertTrue(data["otp_required"])
                self.assertEqual(data["identity_verification_provider"], "prembly")
                self.assertNotIn("tracking_id", data)
                lookup.assert_called_once_with(self.raw, name="Ada Eze")
                self.assertEqual(sms.call_args.args[0], self.record["phone"])
                self.user.refresh_from_db()
                self.assertFalse(getattr(self.user, kind + "_verified"))
                self.assertNotIn(self.raw, str(cache.get(f"kyc_identity:{kind}:{self.user.pk}")))

    def test_unconfigured_prembly_and_mock_results_cannot_fall_back(self):
        for kind in ("bvn", "nin"):
            with self.subTest(kind=kind):
                response, lookup, sms = self.start(kind, configured=False)
                self.assertEqual(response.status_code, 400)
                lookup.assert_not_called()
                sms.assert_not_called()
                response, _, sms = self.start(kind, record={**self.record, "mock": True})
                self.assertEqual(response.status_code, 400)
                sms.assert_not_called()

    def test_confirm_creates_trusted_name_proof_and_never_bank_account(self):
        for kind in ("bvn", "nin"):
            with self.subTest(kind=kind):
                self.start(kind)
                with patch("utility.wema.create_wallet_request") as bank:
                    response = self.post(f"/api/kyc/{kind}/confirm/", otp="123456")
                self.assertEqual(response.status_code, 200, response.content)
                self.assertTrue(response.json()[kind + "_verified"])
                proof = IdentityProof.objects.get(user=self.user, identity_type=kind)
                self.assertEqual(proof.identity_hash, hash_identifier(self.raw))
                self.assertEqual(proof.verified_name, "Ada Eze")
                self.assertEqual(proof.source, IdentityProof.IDENTITY_PROVIDER_OTP)
                bank.assert_not_called()
                self.assertIsNone(cache.get(f"kyc_identity:{kind}:{self.user.pk}"))
                self.assertEqual(self.post(f"/api/kyc/{kind}/confirm/", otp="123456").status_code, 400)

    def test_no_direct_bvn_shortcut_and_no_identifier_disclosure(self):
        with patch("utility.providers.prembly_verify_bvn") as lookup:
            response = self.post("/api/kyc/bvn/", bvn=self.raw)
        self.assertEqual(response.status_code, 409)
        lookup.assert_not_called()
        self.assertNotIn(self.raw, response.content.decode())

    def test_bad_and_expired_codes_do_not_create_proof(self):
        self.start("bvn")
        self.assertEqual(self.post("/api/kyc/bvn/confirm/", otp="000000").status_code, 400)
        self.assertFalse(IdentityProof.objects.filter(user=self.user).exists())
        cache.clear()
        self.assertEqual(self.post("/api/kyc/bvn/confirm/", otp="123456").status_code, 400)
        self.assertFalse(IdentityProof.objects.filter(user=self.user).exists())

    def test_existing_verified_identity_cannot_be_replaced(self):
        self.user.bvn_verified = True
        self.user.set_bvn("22222222222")
        self.user.save()
        self.start("bvn")
        response = self.post("/api/kyc/bvn/confirm/", otp="123456")
        self.assertEqual(response.status_code, 409)
        self.user.refresh_from_db()
        self.assertEqual(self.user.bvn_hash, hash_identifier("22222222222"))

    def test_status_selects_prembly_without_bank_face_or_upgrade(self):
        data = self.post("/api/kyc/status/").json()
        self.assertEqual(data["identity_verification_provider"], "prembly")
        self.assertFalse(data["identity_face_available"])
        self.assertFalse(data["bank_upgrade_required"])
