"""Bank KYC contracts and ownership boundaries; all bank traffic is mocked."""
import json
from datetime import timedelta
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from accounts.models import (AccessToken, IdentityProof, User, hash_identifier,
                             record_identity_proof, rehydrate_verified_identity_flags)
from accounts.views import verify_kyc_address
from utility import wema
from utility.providers import kyc_verify_address, kyc_verify_face, kyc_verify_id_document
from wallet.models import WemaFaceSession, WemaProvisioningAttempt
from wallet.services import get_or_create_wallet
from wallet.views import upgrade_wema_identity


PILOT = {
    "BASE_URL": "https://lagos-alat-blueapi.azure-api.net",
    "CHANNEL_ID": "test-channel", "KEYS": {"wallet": "test-wallet", "upgrade": "test-upgrade"},
    "SIMULATION": False, "FACE_CB_MODE": "registered",
    "FACE_VERIFY_URL": "https://face-verification-pilot.azurewebsites.net",
}
BVN, NIN = "22222222222", "33333333333"


@override_settings(WEMA=PILOT, KYC_PROVIDER="wema", PAYMENT_PROVIDER="wema")
class BankIdentityRoutingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="bank-kyc", phone="08010000201", email="kyc@example.test",
            first_name="Ada", last_name="Eze", email_verified=True, phone_verified=True)
        self.token = AccessToken.issue(self.user).key
        self.wallet = get_or_create_wallet(self.user)
        blocker = patch("requests.sessions.Session.request", side_effect=AssertionError("Unexpected network"))
        blocker.start()
        self.addCleanup(blocker.stop)

    def post(self, path, **data):
        return self.client.post(path, {"access_token": self.token, **data},
                                content_type="application/json")

    def attempt(self, kind="nin", tracking="TRACK-1"):
        return WemaProvisioningAttempt.objects.create(
            user=self.user, identity_type=kind, identity_hash=hash_identifier(NIN if kind == "nin" else BVN),
            identity_last4=(NIN if kind == "nin" else BVN)[-4:], tracking_id=tracking,
            expires_at=timezone.now() + timedelta(minutes=10))

    def test_both_identity_routes_start_bank_otp_without_prembly_or_local_sms(self):
        for path, kind, raw in (("/api/kyc/bvn/start/", "bvn", BVN),
                                ("/api/kyc/bvn/", "bvn", BVN),
                                ("/api/kyc/nin/", "nin", NIN)):
            with self.subTest(path=path), patch("utility.wema.create_wallet_request", return_value={
                    "success": True, "tracking_id": "TRACK-" + kind}) as bank, \
                    patch("accounts.views.verify_bvn") as bvn_lookup, \
                    patch("accounts.views.verify_nin") as nin_lookup, \
                    patch("accounts.views.send_sms") as sms:
                WemaProvisioningAttempt.objects.all().delete()
                response = self.post(path, **{kind: raw})
                self.assertEqual(response.status_code, 200, response.content)
                data = response.json()
                self.assertTrue(data["otp_required"])
                self.assertEqual(data["otp_destination_kind"], kind)
                self.assertEqual(data["otp_destination"], "")
                bank.assert_called_once_with(self.user.phone, self.user.email,
                    bvn=raw if kind == "bvn" else "", nin=raw if kind == "nin" else "")
                bvn_lookup.assert_not_called()
                nin_lookup.assert_not_called()
                sms.assert_not_called()
                self.user.refresh_from_db()
                self.assertFalse(self.user.bvn_verified or self.user.nin_verified)

    def test_account_create_accepts_nin_and_its_read_state_names_the_identity(self):
        with patch("utility.wema.create_wallet_request", return_value={"success": True, "tracking_id": "NIN-1"}):
            result = self.post("/api/wallet/account/create/", nin=NIN)
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.json()["using_bvn"])
        state = self.post("/api/wallet/account/").json()
        self.assertEqual(state["otp_destination_kind"], "nin")
        self.assertEqual(state["otp_destination"], "")

    def test_existing_account_returns_upgrade_required_without_issuing_otp(self):
        self.wallet.account_number = "0123456789"
        self.wallet.save(update_fields=["account_number"])
        for path in ("/api/kyc/nin/", "/api/wallet/account/create/"):
            with self.subTest(path=path), patch("utility.wema.create_wallet_request") as bank:
                response = self.post(path, nin=NIN)
                self.assertEqual(response.status_code, 409)
                self.assertTrue(response.json()["upgrade_required"])
                bank.assert_not_called()

    def test_other_users_identity_is_rejected_before_initiation(self):
        other = User.objects.create_user(username="owner", phone="08010000202")
        other.set_nin(NIN)
        other.save(update_fields=["nin_hash", "nin_last4"])
        with patch("utility.wema.create_wallet_request") as bank:
            self.assertEqual(self.post("/api/kyc/nin/", nin=NIN).status_code, 409)
            bank.assert_not_called()

    def test_confirm_rejects_another_users_tracking_and_wrong_identity_route(self):
        attempt = self.attempt()
        other = User.objects.create_user(username="other", phone="08010000203")
        with patch("utility.wema.validate_wallet_otp") as bank:
            wrong_kind = self.post("/api/kyc/bvn/confirm/", tracking_id=attempt.tracking_id, otp="123456")
            self.assertEqual(wrong_kind.status_code, 400)
            self.token = AccessToken.issue(other).key
            wrong_owner = self.post("/api/kyc/nin/confirm/", tracking_id=attempt.tracking_id, otp="123456")
            self.assertEqual(wrong_owner.status_code, 400)
            bank.assert_not_called()

    def test_confirm_requires_tracking_and_rejects_expired_attempt(self):
        attempt = self.attempt()
        attempt.expires_at = timezone.now() - timedelta(seconds=1)
        attempt.save(update_fields=["expires_at"])
        with patch("utility.wema.validate_wallet_otp") as bank:
            self.assertEqual(self.post("/api/kyc/nin/confirm/", otp="123456").status_code, 400)
            self.assertEqual(self.post("/api/kyc/nin/confirm/", otp="123456", tracking_id=attempt.tracking_id).status_code, 400)
            bank.assert_not_called()

    def test_pending_completion_survives_both_http_routes_without_granting_identity(self):
        attempt = self.attempt()
        for path in ("/api/kyc/nin/confirm/", "/api/wallet/wema/verify-otp/"):
            with self.subTest(path=path), \
                    patch("utility.wema.validate_wallet_otp", return_value={"success": True}) as bank, \
                    patch("utility.wema.get_account_details", return_value={"success": False}):
                response = self.post(path, otp="123456", tracking_id=attempt.tracking_id, using_bvn=True)
                self.assertEqual(response.status_code, 202)
                self.assertFalse(response.json()["success"])
                self.assertTrue(response.json()["pending"])
                self.assertFalse(response.json()["otp_required"])
                bank.assert_called_once_with(self.user.phone, "123456", attempt.tracking_id, bvn=False)
                self.user.refresh_from_db()
                self.assertFalse(self.user.nin_verified)
                self.assertFalse(IdentityProof.objects.filter(user=self.user).exists())

    def test_confirm_records_only_the_server_bound_identity(self):
        attempt = self.attempt()
        with patch("utility.wema.validate_wallet_otp", return_value={"success": True}), \
                patch("utility.wema.get_account_details", return_value={
                    "success": True, "account_number": "0123456789", "account_name": "ADA EZE"}), \
                patch("utility.wema.lift_debit_restriction", return_value={"success": True}), \
                patch("wallet.views.sync_bank_tier"):
            response = self.post("/api/kyc/nin/confirm/", otp="123456", tracking_id=attempt.tracking_id)
        self.assertEqual(response.status_code, 200, response.content)
        self.user.refresh_from_db()
        self.assertTrue(self.user.nin_verified)
        self.assertFalse(self.user.bvn_verified)
        self.assertEqual(self.user.tier, 1)
        self.assertEqual(response.json()["tier"], 1)
        self.assertEqual(self.user.nin_hash, hash_identifier(NIN))
        proof = IdentityProof.objects.get(user=self.user)
        self.assertEqual(proof.source, IdentityProof.WEMA_WALLET_OTP)

    def test_name_mismatch_never_leaves_an_attempt_that_can_rehydrate_identity(self):
        attempt = self.attempt()
        with patch("utility.wema.validate_wallet_otp", return_value={"success": True}), \
                patch("utility.wema.get_account_details", return_value={
                    "success": True, "account_number": "0123456789", "account_name": "OTHER PERSON"}), \
                patch("utility.wema.lift_debit_restriction", return_value={"success": True}), \
                patch("wallet.views.sync_bank_tier"):
            response = self.post("/api/kyc/nin/confirm/", otp="123456", tracking_id=attempt.tracking_id)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["identity_review_required"])
        self.assertFalse(response.json()["nin_verified"])
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, WemaProvisioningAttempt.FAILED)
        self.assertFalse(self.post("/api/kyc/status/").json()["nin_verified"])

    def test_face_link_preserves_configured_exact_callback_and_does_not_verify_user(self):
        with override_settings(ZITCH_LINKS={"API_BASE": "https://frankfurt.example.test"}):
            response = self.post("/api/kyc/face/start/", nin=NIN, prefer_face=True)
        self.assertEqual(response.status_code, 200, response.content)
        query = parse_qs(urlsplit(response.json()["url"]).query)
        self.assertEqual(query, {"nin": [NIN], "x_tk": ["test-channel"],
            "cb_uri": ["https://frankfurt.example.test/webhooks/wema/face"]})
        session = WemaFaceSession.objects.get(user=self.user)
        self.assertEqual(session.identity_hash, hash_identifier(NIN))
        self.user.refresh_from_db()
        self.assertFalse(self.user.nin_verified)


@override_settings(WEMA=PILOT, KYC_PROVIDER="wema", PAYMENT_PROVIDER="wema")
class BankUpgradeContractsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="upgrade", phone="08010000301",
            email_verified=True, phone_verified=True, bvn_verified=True, nin_verified=True,
            face_verified=True, tier=2)
        self.user.set_bvn(BVN)
        self.user.set_nin(NIN)
        self.user.save()
        self.wallet = get_or_create_wallet(self.user)
        self.wallet.account_number, self.wallet.bank_tier = "0123456789", 2
        self.wallet.save(update_fields=["account_number", "bank_tier"])
        self.token = AccessToken.issue(self.user).key
        blocker = patch("requests.sessions.Session.request", side_effect=AssertionError("Unexpected network"))
        blocker.start()
        self.addCleanup(blocker.stop)

    def post_address(self, **data):
        return self.client.post("/api/kyc/address/", {"access_token": self.token,
            "address": "12 Allen Avenue", "city": "Ikeja", "state": "Lagos", **data},
            content_type="application/json")

    def test_address_prerequisites_apply_to_bank_and_document_rails_and_shared_service(self):
        for field in ("bvn_verified", "nin_verified", "face_verified", "email_verified", "phone_verified"):
            for live in (True, False):
                with self.subTest(field=field, bank=live), \
                        patch("utility.wema.address_verify_live", return_value=live), \
                        patch("utility.wema.upgrade_tier3") as bank, \
                        patch("accounts.views.kyc_verify_address") as document:
                    setattr(self.user, field, False)
                    result = verify_kyc_address(self.user, {"address": "12 Allen Avenue", "document": "ZmFrZQ=="})
                    self.assertEqual(result.status_code, 409)
                    bank.assert_not_called()
                    document.assert_not_called()
                    setattr(self.user, field, True)

    def test_bank_tier_one_requires_combined_upgrade_before_address_submission(self):
        self.wallet.bank_tier = 1
        self.wallet.save(update_fields=["bank_tier"])
        with patch("wallet.services.sync_bank_tier"), patch("utility.wema.upgrade_tier3") as bank:
            response = self.post_address()
        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.json()["upgrade_required"])
        bank.assert_not_called()

    def test_structured_address_reaches_bank_and_both_tiers_update_on_completion(self):
        address = {"buildingNumber": "12", "apartment": "2B", "street": "Allen Avenue",
            "city": "Ikeja", "town": "Ikeja", "state": "Lagos", "lga": "Ikeja",
            "lcda": "Ikeja", "landmark": "Library", "additionalInformation": "Blue gate",
            "country": "Nigeria", "fullAddress": "12 Allen Avenue, Ikeja, Lagos", "postalCode": "100001"}
        with patch("utility.wema.upgrade_tier3", return_value={"success": True}) as bank:
            response = self.post_address(residentialAddress=address)
        self.assertEqual(response.status_code, 200, response.content)
        bank.assert_called_once_with(self.wallet.account_number, address)
        self.user.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertTrue(self.user.address_verified)
        self.assertEqual(self.user.tier, 3)
        self.assertEqual(self.wallet.bank_tier, 3)
        self.assertEqual(response.json()["bank_tier"], 3)

    def test_pending_or_failed_bank_address_does_not_grant_tier_or_fall_back(self):
        for result, code in (({"success": True, "pending": True}, 202), ({"success": False}, 400)):
            with self.subTest(result=result), patch("utility.wema.upgrade_tier3", return_value=result), \
                    patch("accounts.views.kyc_verify_address") as document:
                response = self.post_address(document="ZmFrZQ==")
                self.assertEqual(response.status_code, code)
                if code == 202:
                    self.assertFalse(response.json()["success"])
                    self.assertTrue(response.json()["pending"])
                document.assert_not_called()
                self.user.refresh_from_db()
                self.wallet.refresh_from_db()
                self.assertFalse(self.user.address_verified)
                self.assertEqual(self.wallet.bank_tier, 2)
                self.assertEqual(self.user.tier, 2)

    def test_tier2_cannot_replace_an_already_verified_nin(self):
        with patch("wallet.views.kyc_verify_face") as face, patch("utility.wema.upgrade_tier2") as bank:
            response = upgrade_wema_identity(self.user, {
                "bvn": BVN, "nin": "44444444444", "live_image": "ZmFrZQ=="})
        self.assertEqual(response.status_code, 409)
        face.assert_not_called()
        bank.assert_not_called()

    def test_tier2_passes_plain_base64_from_data_url_and_records_confirmed_bank_tier(self):
        self.wallet.bank_tier = 1
        self.wallet.save(update_fields=["bank_tier"])
        with patch("wallet.views.kyc_verify_face", return_value={"success": True}) as face, \
                patch("utility.wema.upgrade_tier2", return_value={"success": True}) as bank:
            result = upgrade_wema_identity(self.user, {
                "bvn": BVN, "nin": NIN, "liveImageOfFace": "data:image/jpeg;base64,ZmFrZQ==",
                "accountNumber": "9999999999"})
        self.assertEqual(result.status_code, 200)
        face.assert_called_once_with("ZmFrZQ==")
        bank.assert_called_once_with(self.wallet.account_number, bvn=BVN, nin=NIN, live_image="ZmFrZQ==")
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.bank_tier, 2)

    def test_tier2_pending_cannot_grant_identity_or_liveness(self):
        self.user.nin_verified = self.user.face_verified = False
        self.user.save(update_fields=["nin_verified", "face_verified"])
        with patch("wallet.views.kyc_verify_face", return_value={"success": True}), \
                patch("utility.wema.upgrade_tier2", return_value={"success": True, "pending": True}):
            response = upgrade_wema_identity(self.user, {"bvn": BVN, "nin": NIN, "live_image": "ZmFrZQ=="})
        self.assertEqual(response.status_code, 202)
        self.user.refresh_from_db()
        self.assertFalse(self.user.nin_verified or self.user.face_verified)
        self.assertFalse(IdentityProof.objects.filter(user=self.user).exists())

    def test_tier2_rejects_invalid_base64_and_preserves_liveness_gate(self):
        with patch("wallet.views.kyc_verify_face", return_value={"success": False}) as face, \
                patch("utility.wema.upgrade_tier2") as bank:
            response = upgrade_wema_identity(self.user, {"bvn": BVN, "nin": NIN, "live_image": "not-base64"})
            self.assertEqual(response.status_code, 400)
            face.assert_not_called()
            response = upgrade_wema_identity(self.user, {"bvn": BVN, "nin": NIN, "live_image": "ZmFrZQ=="})
            self.assertEqual(response.status_code, 400)
            face.assert_called_once()
            bank.assert_not_called()

    def test_tier2_does_not_replace_identity_verified_during_provider_call(self):
        self.user.nin_verified = False
        self.user.save(update_fields=["nin_verified"])
        replacement = "44444444444"

        def bank_result(*args, **kwargs):
            User.objects.filter(pk=self.user.pk).update(
                nin_verified=True, nin_hash=hash_identifier(replacement), nin_last4=replacement[-4:])
            return {"success": True}

        with patch("wallet.views.kyc_verify_face", return_value={"success": True}), \
                patch("utility.wema.upgrade_tier2", side_effect=bank_result):
            result = upgrade_wema_identity(self.user, {"bvn": BVN, "nin": NIN, "live_image": "ZmFrZQ=="})
        self.assertEqual(result.status_code, 409)
        self.user.refresh_from_db()
        self.assertEqual(self.user.nin_hash, hash_identifier(replacement))

    @override_settings(PREMBLY={"BASE_URL": "https://kyc.example.test", "API_KEY": "test", "APP_ID": "test"})
    def test_face_similarity_response_cannot_reach_bank_upgrade(self):
        self.user.nin_verified = self.user.face_verified = False
        self.user.save(update_fields=["nin_verified", "face_verified"])
        response = Mock(status_code=200, json=Mock(return_value={
            "status": True, "data": {"face_match": True}}))
        with patch("utility.providers.requests.post", return_value=response), \
                patch("utility.wema.upgrade_tier2") as bank:
            result = upgrade_wema_identity(self.user, {"bvn": BVN, "nin": NIN, "live_image": "ZmFrZQ=="})
        self.assertEqual(result.status_code, 400)
        bank.assert_not_called()
        self.user.refresh_from_db()
        self.assertFalse(self.user.nin_verified or self.user.face_verified)

    def test_anonymous_api_calls_cannot_reach_shared_services(self):
        for path in ("/api/kyc/address/", "/api/wallet/wema/upgrade-tier2/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path, {}, content_type="application/json").status_code, 401)


@override_settings(WEMA=PILOT)
class BankKycEnvelopeTests(SimpleTestCase):
    def response(self, data, status=200, content=b"json"):
        return Mock(status_code=status, content=content, json=Mock(return_value=data))

    def test_explicit_negative_or_pending_status_overrides_other_success_signals(self):
        for value in ("failed", "Rejected", " pending ", "processing", "in_progress", "false"):
            for envelope in ({"status": value, "hasError": False},
                             {"Status": value, "Success": True},
                             {"success": True, "data": {"status": value}}):
                with self.subTest(envelope=envelope), patch("utility.wema._post", return_value=self.response(envelope)):
                    self.assertFalse(wema._kyc_ok(envelope))
                    self.assertFalse(wema.validate_wallet_otp("08010000111", "123456", "TRACK")["success"])

    def test_nonempty_error_fields_override_success_even_in_nested_envelopes(self):
        for field, value in (("errors", ["rejected"]), ("ErrorMessages", ["rejected"]),
                             ("errorMessage", "Rejected"), ("error", {"reason": "rejected"}),
                             ("hasError", "true")):
            for envelope in ({"success": True, field: value},
                             {"status": True, "data": {field: value}}):
                with self.subTest(envelope=envelope), patch("utility.wema._post", return_value=self.response(envelope)):
                    self.assertFalse(wema._kyc_ok(envelope))
                    self.assertFalse(wema.validate_wallet_otp("08010000111", "123456", "TRACK")["success"])
        self.assertTrue(wema._kyc_ok({"status": True, "errors": [], "errorMessage": "", "hasError": False}))

    def test_otp_validation_requires_completion_not_202_or_processing(self):
        for envelope, status in (({"status": True}, 202),
                                 ({"status": True, "message": "Processing"}, 200),
                                 ({"status": True, "pending": True}, 200),
                                 ({"status": True, "data": {"otpStatus": "Pending"}}, 200)):
            for bvn in (True, False):
                with self.subTest(envelope=envelope, status=status, bvn=bvn), \
                        patch("utility.wema._post", return_value=self.response(envelope, status)):
                    self.assertFalse(wema.validate_wallet_otp("08010000111", "123456", "TRACK", bvn=bvn)["success"])
        with patch("utility.wema._post", return_value=self.response({"Status": True, "message": "OTP validated"})):
            self.assertTrue(wema.validate_wallet_otp("08010000111", "123456", "TRACK")["success"])

    def test_otp_initiation_can_accept_202_with_real_tracking(self):
        with patch("utility.wema._post", return_value=self.response({
                "status": True, "trackingId": "TRACK", "message": "Processing"}, 202)):
            result = wema.create_wallet_request("08010000111", "test@example.test", nin=NIN)
        self.assertTrue(result["success"])
        self.assertEqual(result["tracking_id"], "TRACK")

    def test_otp_tracking_envelope_variants_and_product_paths(self):
        envelopes = [
            {"status": True, "trackingId": "TRACK"},
            {"status": True, "data": {"otpTrackingID": "TRACK"}},
            {"Status": True, "Data": {"OtpTrackingId": "TRACK"}},
            {"successful": True, "result": {"trackingID": "TRACK"}},
            {"success": True, "data": {"result": {"trackingId": "TRACK"}}},
        ]
        for envelope in envelopes:
            for kind, path in (("bvn", "/account-creation/api/CustomerAccount/PostPartnershipAccountCreationWithBvn"),
                               ("nin", "/wallet-creation/api/CustomerAccount/GenerateWalletAccountForPartnerships/Request")):
                with self.subTest(envelope=envelope, kind=kind), \
                        patch("utility.wema.requests.post", return_value=self.response(envelope)) as post:
                    result = wema.create_wallet_request("08010000111", "test@example.test", **{kind: BVN})
                    self.assertTrue(result["success"])
                    self.assertEqual(result["tracking_id"], "TRACK")
                    self.assertEqual(result["otp_destination"], "")
                    self.assertEqual(post.call_args.args[0], PILOT["BASE_URL"] + path)

    def test_otp_never_accepts_missing_tracking_or_false_success_or_http_error(self):
        cases = [({"status": True}, 200), ({"status": "false", "trackingId": "TRACK"}, 200),
                 ({"status": False, "success": True, "trackingId": "TRACK"}, 200),
                 ({"status": True, "trackingId": "TRACK"}, 400),
                 ({"status": True, "data": []}, 200), (["TRACK"], 200)]
        for data, status in cases:
            with self.subTest(data=data, status=status), patch("utility.wema._post", return_value=self.response(data, status)):
                self.assertFalse(wema.create_wallet_request("08010000111", "test@example.test", nin=NIN)["success"])

    def test_malformed_json_is_not_a_successful_otp_request_or_resend(self):
        response = self.response(None)
        response.json.side_effect = ValueError("bad JSON")
        with patch("utility.wema._post", return_value=response):
            self.assertFalse(wema.create_wallet_request("08010000111", "test@example.test", nin=NIN)["success"])
            self.assertFalse(wema.validate_wallet_otp("08010000111", "123456", "TRACK")["success"])
            self.assertFalse(wema.resend_wallet_otp("08010000111", "TRACK")["success"])

    def test_empty_documented_resend_is_accepted(self):
        with patch("utility.wema._post", return_value=self.response(None, 204, b"")):
            self.assertTrue(wema.resend_wallet_otp("08010000111", "TRACK")["success"])

    def test_upgrades_distinguish_accepted_from_completed(self):
        for data, status, pending in (({"status": True, "message": "Success"}, 200, False),
                ({"status": True}, 202, True),
                ({"status": True, "data": {"addressVerificationStatus": "Pending"}}, 200, True),
                ({"status": True, "message": "Request submitted for processing"}, 200, True)):
            for tier in (2, 3):
                with self.subTest(tier=tier, data=data), patch("utility.wema._post", return_value=self.response(data, status)):
                    result = (wema.upgrade_tier2("0123456789", bvn=BVN, nin=NIN, live_image="ZmFrZQ==")
                              if tier == 2 else wema.upgrade_tier3("0123456789", "12 Allen Avenue"))
                    self.assertEqual(result["success"], not pending)
                    self.assertEqual(result["pending"], pending)

    def test_face_without_callback_cannot_be_advertised_as_available(self):
        with override_settings(WEMA={**PILOT, "FACE_CB_MODE": "none"}):
            self.assertFalse(wema.face_verify_live())

    def test_both_upgrades_preserve_acknowledged_nested_pending(self):
        for data in ({"status": True, "data": {"pending": True}},
                     {"status": True, "data": {"status": "Pending"}},
                     {"status": True, "data": {"status": "in_progress"}},
                     {"status": True, "data": {"result": {"pending": True}}}):
            for tier in (2, 3):
                with self.subTest(tier=tier, data=data), \
                        patch("utility.wema._post", return_value=self.response(data)):
                    result = (wema.upgrade_tier2("0123456789", bvn=BVN, nin=NIN, live_image="ZmFrZQ==")
                              if tier == 2 else wema.upgrade_tier3("0123456789", "12 Allen Avenue"))
                    self.assertFalse(result["success"])
                    self.assertTrue(result["pending"])
                    # The same envelope must never prove completed ownership.
                    self.assertFalse(wema._kyc_ok(data))

    def test_pending_upgrades_cannot_override_rejection_errors_or_http_failure(self):
        for data in ({"status": False, "data": {"pending": True}},
                     {"status": True, "errors": ["rejected"], "data": {"pending": True}},
                     {"status": True, "data": {"pending": True, "status": "failed"}},
                     {"status": True, "data": {"pending": True, "errors": ["rejected"]}}):
            for status in (200, 202, 500):
                for tier in (2, 3):
                    with self.subTest(tier=tier, data=data, status=status), \
                            patch("utility.wema._post", return_value=self.response(data, status)):
                        result = (wema.upgrade_tier2("0123456789", bvn=BVN, nin=NIN, live_image="ZmFrZQ==")
                                  if tier == 2 else wema.upgrade_tier3("0123456789", "12 Allen Avenue"))
                        self.assertFalse(result["success"])
                        self.assertFalse(result["pending"])
        for tier in (2, 3):
            with self.subTest(tier=tier), patch("utility.wema._post", return_value=self.response(
                    {"status": True, "data": {"pending": True}}, 500)):
                result = (wema.upgrade_tier2("0123456789", bvn=BVN, nin=NIN, live_image="ZmFrZQ==")
                          if tier == 2 else wema.upgrade_tier3("0123456789", "12 Allen Avenue"))
                self.assertFalse(result["success"])
                self.assertFalse(result["pending"])

    def test_nested_upgrade_rejection_is_never_treated_as_completed(self):
        with patch("utility.wema._post", return_value=self.response({
                "status": True, "data": {"addressVerificationStatus": "Failed"}})):
            result = wema.upgrade_tier3("0123456789", "12 Allen Avenue")
        self.assertFalse(result["success"])
        self.assertFalse(result["pending"])

    def test_account_readback_does_not_truthify_string_false_or_ignore_http_failure(self):
        for success, status in (("false", 200), (True, 400)):
            with self.subTest(success=success, status=status), patch("utility.wema._get", return_value=self.response({
                    "successful": success, "data": {"accountNumber": "0123456789", "accountName": "ADA EZE"}}, status)):
                self.assertFalse(wema.get_account_details("08010000111")["success"])


@override_settings(PREMBLY={"BASE_URL": "https://kyc.example.test", "API_KEY": "test-key", "APP_ID": "test-app"},
                   WEMA={"SIMULATION": False})
class ProviderLivenessContractTests(SimpleTestCase):
    def check(self, payload, status=200):
        with patch("utility.providers.requests.post", return_value=Mock(
                status_code=status, json=Mock(return_value=payload))):
            return kyc_verify_face("ZmFrZQ==")

    def test_explicit_boolean_liveness_pass_is_required(self):
        self.assertTrue(self.check({"status": True, "data": {"liveness": True}})["success"])
        for value in (False, "false", "true", 0, 1, None, [], {"status": True}):
            with self.subTest(value=value):
                self.assertFalse(self.check({"status": True, "data": {
                    "liveness": value, "face_match": True}})["success"])

    def test_face_match_alone_cannot_prove_liveness(self):
        self.assertFalse(self.check({"status": True, "data": {"face_match": True}})["success"])

    def test_truthy_top_level_status_cannot_pass(self):
        for value in (False, "false", "true", 0, 1, None, {"status": True}):
            with self.subTest(value=value):
                self.assertFalse(self.check({"status": value, "data": {"liveness": True}})["success"])

    def test_malformed_or_incomplete_envelopes_fail_closed(self):
        for value in (None, [], "success", {}, {"status": True},
                      {"status": True, "data": []}, {"status": True, "data": "live"}):
            with self.subTest(value=value):
                self.assertFalse(self.check(value)["success"])

    def test_http_failure_and_pending_cannot_pass_even_with_liveness_true(self):
        for code in (202, 302, 400, 500):
            with self.subTest(code=code):
                self.assertFalse(self.check({"status": True, "data": {"liveness": True}}, code)["success"])
        self.assertFalse(self.check({"status": True, "pending": True, "data": {"liveness": True}})["success"])

    def test_invalid_json_and_timeout_fail_without_leaking_request_details(self):
        import requests
        response = Mock(status_code=200, json=Mock(side_effect=ValueError("bad JSON")))
        with patch("utility.providers.requests.post", return_value=response):
            self.assertFalse(kyc_verify_face("ZmFrZQ==")["success"])
        with patch("utility.providers.requests.post", side_effect=requests.Timeout("sensitive detail")):
            result = kyc_verify_face("ZmFrZQ==")
        self.assertFalse(result["success"])
        self.assertNotIn("sensitive", result["message"])

    def test_missing_or_oversized_capture_is_rejected_before_provider(self):
        with patch("utility.providers.requests.post") as provider:
            for image in (None, {}, "", " ", "x" * 2_800_001):
                with self.subTest(type=type(image).__name__):
                    self.assertFalse(kyc_verify_face(image)["success"])
            provider.assert_not_called()


@override_settings(PREMBLY={"BASE_URL": "https://kyc.example.test", "API_KEY": "test-key", "APP_ID": "test-app"},
                   WEMA={"SIMULATION": False})
class ProviderDocumentEnvelopeTests(SimpleTestCase):
    """Generic safety boundaries, not certification of unconfirmed live contracts."""

    def checks(self):
        return ((kyc_verify_address, ("12 Allen Avenue", "ZmFrZQ==")),
                (kyc_verify_id_document, ("ZmFrZQ==", "passport")))

    def assert_envelope(self, envelope, status=200, *, success=False):
        for verify, args in self.checks():
            with self.subTest(adapter=verify.__name__, envelope=envelope, status=status), \
                    patch("utility.providers.requests.post", return_value=Mock(
                        status_code=status, json=Mock(return_value=envelope))):
                self.assertIs(verify(*args)["success"], success)

    def test_literal_boolean_success_is_required(self):
        self.assert_envelope({"status": True, "data": {}}, success=True)
        for value in (False, "false", "true", 0, 1, None, [], {"status": True}):
            self.assert_envelope({"status": value})

    def test_http_failure_or_accepted_pending_never_grants_verification(self):
        for status in (202, 302, 400, 500):
            self.assert_envelope({"status": True}, status)

    def test_nested_pending_and_rejection_override_success_envelope(self):
        for detail in ({"pending": True}, {"pending": "true"}, {"failed": True},
                       {"status": False}, {"status": "false"}, {"status": "pending"},
                       {"verification_status": "in_progress"}, {"addressVerificationStatus": "Failed"},
                       {"documentVerificationStatus": "Rejected"}, {"verified": False},
                       {"hasError": True}, {"errors": ["Could not verify"]},
                       {"message": "Verification pending"}, {"detail": "Document not verified"}):
            self.assert_envelope({"status": True, "data": {"verification": detail}})
        self.assert_envelope({"status": True, "pending": True})
        self.assert_envelope({"status": True, "data": {"checks": [{"status": "Failed"}]}})

    def test_malformed_json_types_and_wrappers_fail_closed(self):
        for envelope in (None, [], True, 1, "success", {}, {"status": True, "data": "verified"},
                         {"status": True, "data": []}, {"status": True, "data": False},
                         {"status": True, "data": {"verification": "passed"}}):
            self.assert_envelope(envelope)

    def test_invalid_json_and_transport_errors_do_not_leak_exception_details(self):
        import requests
        for verify, args in self.checks():
            for error in (ValueError("private response detail"), requests.Timeout("private request detail")):
                with self.subTest(adapter=verify.__name__, error=type(error).__name__), \
                        patch("utility.providers.requests.post", return_value=Mock(
                            status_code=200, json=Mock(side_effect=error))):
                    result = verify(*args)
                    self.assertFalse(result["success"])
                    self.assertNotIn("private", result["message"])
            with patch("utility.providers.requests.post", side_effect=requests.ConnectionError("private URL")):
                result = verify(*args)
                self.assertFalse(result["success"])
                self.assertNotIn("private", result["message"])

    def test_invalid_input_types_are_rejected_before_provider_call(self):
        with patch("utility.providers.requests.post") as post:
            for value in (None, {}, [], 1, "", " "):
                self.assertFalse(kyc_verify_address(value)["success"])
                self.assertFalse(kyc_verify_id_document(value)["success"])
            self.assertFalse(kyc_verify_address("12 Allen Avenue", document={})["success"])
            self.assertFalse(kyc_verify_id_document("ZmFrZQ==", doc_type={})["success"])
            post.assert_not_called()


class IdentityTierModelTests(SimpleTestCase):
    def test_either_identity_earns_tier_one_but_both_are_needed_for_tier_two(self):
        for bvn, nin, face, address, expected in (
                (False, False, False, False, 0),
                (True, False, False, False, 1),
                (False, True, False, False, 1),
                (True, False, True, True, 1),
                (False, True, True, True, 1),
                (True, True, False, True, 1),
                (True, True, True, False, 2),
                (True, True, True, True, 3)):
            with self.subTest(bvn=bvn, nin=nin, face=face, address=address):
                user = User(email_verified=True, phone_verified=True, bvn_verified=bvn,
                            nin_verified=nin, face_verified=face, address_verified=address)
                user.recompute_tier()
                self.assertEqual(user.tier, expected)
                for contact in ("email_verified", "phone_verified"):
                    setattr(user, contact, False)
                    user.recompute_tier()
                    self.assertEqual(user.tier, 0)
                    setattr(user, contact, True)


class TrustedIdentityRehydrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="rehydrate", phone="08010000401",
            email_verified=True, phone_verified=True)
        self.wallet = get_or_create_wallet(self.user)
        self.wallet.account_number = "0123456789"
        self.wallet.save(update_fields=["account_number"])

    def test_pending_or_failed_attempt_with_existing_nuban_cannot_verify_either_identity(self):
        from accounts.views import _kyc_state

        for kind, raw in (("bvn", BVN), ("nin", NIN)):
            for status in (WemaProvisioningAttempt.PENDING, WemaProvisioningAttempt.FAILED):
                WemaProvisioningAttempt.objects.create(user=self.user, identity_type=kind,
                    identity_hash=hash_identifier(raw), identity_last4=raw[-4:],
                    tracking_id=f"{kind}-{status}", status=status,
                    expires_at=timezone.now() + timedelta(minutes=10))
        state = _kyc_state(self.user)
        self.user.refresh_from_db()
        self.assertFalse(state["bvn_verified"] or state["nin_verified"])
        self.assertFalse(self.user.bvn_verified or self.user.nin_verified)
        self.assertEqual(self.user.tier, 0)
        self.assertEqual(self.user.bvn_hash, "")
        self.assertEqual(self.user.nin_hash, "")

    def test_completed_proofs_rehydrate_without_an_account_number(self):
        self.wallet.account_number = ""
        self.wallet.save(update_fields=["account_number"])
        record_identity_proof(self.user, IdentityProof.NIN, NIN, source=IdentityProof.WEMA_WALLET_OTP)
        rehydrate_verified_identity_flags(self.user)
        self.assertTrue(self.user.nin_verified)
        self.assertFalse(self.user.bvn_verified)
        self.assertEqual(self.user.nin_hash, hash_identifier(NIN))
        self.assertEqual(self.user.tier, 1)

    def test_verified_face_session_remains_valid_identity_evidence(self):
        WemaFaceSession.objects.create(user=self.user, state="verified-face", identity_type="nin",
            identity_hash=hash_identifier(NIN), status=WemaFaceSession.VERIFIED,
            correlation_id="verified-correlation", expires_at=timezone.now() - timedelta(minutes=1))
        rehydrate_verified_identity_flags(self.user)
        self.assertTrue(self.user.nin_verified)
        self.assertFalse(self.user.face_verified)
        self.assertEqual(self.user.tier, 1)

    def test_unfinished_face_session_is_not_identity_evidence(self):
        WemaFaceSession.objects.create(user=self.user, state="pending-face", identity_type="nin",
            identity_hash=hash_identifier(NIN), expires_at=timezone.now() + timedelta(minutes=10))
        self.assertEqual(rehydrate_verified_identity_flags(self.user), [])
        self.assertFalse(self.user.nin_verified)

    def test_proof_for_another_value_cannot_verify_a_stored_identity(self):
        self.user.set_nin(NIN)
        self.user.save(update_fields=["nin_hash", "nin_last4"])
        record_identity_proof(self.user, IdentityProof.NIN, "44444444444", source=IdentityProof.WEMA_WALLET_OTP)
        self.assertEqual(rehydrate_verified_identity_flags(self.user), [])
        self.assertFalse(self.user.nin_verified)
        self.assertEqual(self.user.nin_hash, hash_identifier(NIN))

    def test_conflicting_ownership_does_not_leak_verified_flags_into_response(self):
        other = User.objects.create_user(username="rehydrate-owner", phone="08010000402")
        other.set_nin(NIN)
        other.save(update_fields=["nin_hash", "nin_last4"])
        record_identity_proof(self.user, IdentityProof.NIN, NIN, source=IdentityProof.WEMA_WALLET_OTP)
        self.assertEqual(rehydrate_verified_identity_flags(self.user), [])
        self.assertFalse(self.user.nin_verified)
        self.assertEqual(self.user.nin_hash, "")
        self.assertEqual(self.user.tier, 0)
        self.user.refresh_from_db()
        self.assertFalse(self.user.nin_verified)
