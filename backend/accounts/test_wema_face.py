"""The bank-run face-biometric rail.

ALAT does liveness in a hosted WEB app and reports the result to a server callback,
so the security question is not "did the check pass" — the bank answers that — but
"whose check was it". The payload names an identity number, and an identity number
is public enough to appear on forms and in breach dumps. Everything here protects
one property: a face check can only ever verify the user whose session it was
opened for.
"""
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User, hash_identifier
from wallet.models import WemaFaceSession

CB = "/webhooks/wema/face/tok/{}"


@override_settings(
    WEMA={"CALLBACK_TOKEN": "tok", "CALLBACK_TOKEN_PREV": "",
          "CALLBACK_ENFORCE_IPS": False, "CALLBACK_IPS": [],
          "KEYS": {"wallet": "k"}, "CHANNEL_ID": "c", "SIMULATION": False,
          "FACE_VERIFY_URL": "https://face.example/"})
class FaceCallbackTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u1", phone="08010000001",
                                             password="Str0ng!pass1")
        self.other = User.objects.create_user(username="u2", phone="08010000002",
                                              password="Str0ng!pass1")
        self.session = WemaFaceSession.objects.create(
            user=self.user, state="s" * 40, identity_type="bvn",
            identity_hash=hash_identifier("22222222222"),
            expires_at=timezone.now() + timedelta(minutes=20))

    def _post(self, state, body):
        return self.client.post(CB.format(state), body,
                                content_type="application/json")

    def test_a_matching_result_verifies_the_session_owner(self):
        res = self._post(self.session.state,
                         {"success": True, "c_id": "COR1", "id": "22222222222", "id_type": "bvn"})
        self.assertEqual(res.status_code, 200)
        self.user.refresh_from_db()
        self.session.refresh_from_db()
        self.assertTrue(self.user.face_verified)
        self.assertEqual(self.session.status, WemaFaceSession.VERIFIED)
        self.assertEqual(self.session.correlation_id, "COR1")

    def test_a_different_identity_never_verifies_the_session_owner(self):
        # The bank checked SOMEBODY's face — just not the person this session is for.
        res = self._post(self.session.state,
                         {"success": True, "c_id": "COR1", "id": "33333333333", "id_type": "bvn"})
        self.assertEqual(res.status_code, 200)
        self.user.refresh_from_db()
        self.session.refresh_from_db()
        self.assertFalse(self.user.face_verified)
        self.assertEqual(self.session.status, WemaFaceSession.FAILED)

    def test_an_unknown_state_verifies_nobody(self):
        self._post("z" * 40, {"success": True, "c_id": "C", "id": "22222222222"})
        self.user.refresh_from_db()
        self.other.refresh_from_db()
        self.assertFalse(self.user.face_verified)
        self.assertFalse(self.other.face_verified)

    def test_success_false_is_not_a_pass(self):
        self._post(self.session.state, {"success": False, "id": "22222222222"})
        self.user.refresh_from_db()
        self.assertFalse(self.user.face_verified)

    def test_a_missing_correlation_id_is_not_a_pass(self):
        # success=true with no proof is not proof.
        self._post(self.session.state, {"success": True, "c_id": "", "id": "22222222222"})
        self.user.refresh_from_db()
        self.assertFalse(self.user.face_verified)

    def test_an_expired_session_cannot_be_completed(self):
        self.session.expires_at = timezone.now() - timedelta(seconds=1)
        self.session.save(update_fields=["expires_at"])
        self._post(self.session.state,
                   {"success": True, "c_id": "C", "id": "22222222222"})
        self.user.refresh_from_db()
        self.assertFalse(self.user.face_verified)

    def test_a_completed_session_cannot_be_replayed(self):
        self._post(self.session.state,
                   {"success": True, "c_id": "C1", "id": "22222222222"})
        self.user.face_verified = False
        self.user.save(update_fields=["face_verified"])
        # Same state, second delivery: the session is no longer PENDING.
        self._post(self.session.state,
                   {"success": True, "c_id": "C2", "id": "22222222222"})
        self.user.refresh_from_db()
        self.session.refresh_from_db()
        self.assertFalse(self.user.face_verified)
        self.assertEqual(self.session.correlation_id, "C1")

    def test_get_is_refused(self):
        self.assertEqual(self.client.get(CB.format(self.session.state)).status_code, 405)


class FaceUrlTests(TestCase):
    def test_the_url_carries_the_identity_key_and_a_server_callback(self):
        from utility import wema
        with override_settings(WEMA={"FACE_VERIFY_URL": "https://face.example/",
                                     # x_tk is the channel id — see
                                     # TheFaceUrlCarriesTheChannelIdTests.
                                     "KEYS": {"wallet": "SUBKEY"}, "CHANNEL_ID": "CHAN",
                                     "SIMULATION": False}):
            url = wema.face_verification_url("nin", "12345678901", "https://api.z/cb")
        self.assertIn("nin=12345678901", url)
        self.assertIn("x_tk=CHAN", url)
        # cb_uri, never rd_uri: the result must reach our server, not the browser.
        self.assertIn("cb_uri=", url)
        self.assertNotIn("rd_uri", url)


@override_settings(KYC_PROVIDER="wema",
                   WEMA={"KEYS": {"wallet": "k", "upgrade": "k"}, "CHANNEL_ID": "c",
                         "SIMULATION": False, "FACE_VERIFY_URL": "https://face.example/"})
class AddressRailTests(TestCase):
    """With the bank rail live, the BANK decides — and its refusal is final.

    The tempting shape here is "ask the bank, and if it says no, accept a utility
    bill instead". That is strictly worse than having no bank check: every customer
    the bank rejects simply takes the other door.
    """

    def setUp(self):
        from wallet.services import get_or_create_wallet
        self.user = User.objects.create_user(username="a1", phone="08030000001",
                                             password="Str0ng!pass1", email="a@z.ng")
        self.user.email_verified = True
        self.user.save(update_fields=["email_verified"])
        wallet = get_or_create_wallet(self.user)
        wallet.account_number = "0123456789"
        wallet.save(update_fields=["account_number"])
        from accounts.models import AccessToken
        self.token = AccessToken.issue(self.user).key

    def _post(self, **extra):
        return self.client.post("/api/kyc/address/",
                                {"access_token": self.token, "address": "12 Allen Avenue",
                                 "city": "Ikeja", "state": "Lagos", **extra},
                                content_type="application/json")

    def test_the_bank_verifies_and_no_document_is_required(self):
        with mock.patch("utility.wema.upgrade_tier3",
                        return_value={"success": True}) as up:
            res = self._post()
        up.assert_called_once()
        self.user.refresh_from_db()
        self.assertTrue(self.user.address_verified)
        self.assertEqual(res.status_code, 200)

    def test_a_bank_refusal_is_final_and_a_document_cannot_route_around_it(self):
        with mock.patch("utility.wema.upgrade_tier3",
                        return_value={"success": False, "message": "Address not found"}), \
             mock.patch("utility.providers.kyc_verify_address",
                        return_value={"success": True}) as prembly:
            res = self._post(document="ZmFrZQ==")
        self.assertEqual(res.status_code, 400)
        prembly.assert_not_called()
        self.user.refresh_from_db()
        self.assertFalse(self.user.address_verified)

    def test_without_a_nuban_the_customer_is_told_what_to_do_first(self):
        self.user.wallet.account_number = ""
        self.user.wallet.save(update_fields=["account_number"])
        res = self._post()
        self.assertEqual(res.status_code, 409)


class TheFaceUrlCarriesTheChannelIdTests(TestCase):
    """`x_tk` is the CHANNEL ID, not a subscription key.

    Wema confirmed it directly, with a sample whose x_tk is the channel id's GUID
    shape; subscription keys are 32 hex characters with no dashes. Sending the wrong
    one fails the check outright — and since this value rides in a URL the customer's
    browser loads, sending a subscription key would also publish a credential that
    should never leave our server.
    """

    LIVE = {"FACE_VERIFY_URL": "https://face.example/",
            "CHANNEL_ID": "2d70a5af-2266-4a6b-86a1-ffb20279400e",
            "SIMULATION": False,
            "KEYS": {"wallet": "MONEY_KEY", "wallet_bvn": "CREATION_KEY"}}

    def test_the_channel_id_is_what_travels(self):
        from utility import wema
        with override_settings(WEMA=self.LIVE):
            self.assertTrue(wema.face_verify_live())
            url = wema.face_verification_url("bvn", "22222222222", "https://api.z/cb")
        self.assertIn("x_tk=2d70a5af-2266-4a6b-86a1-ffb20279400e", url)

    def test_no_subscription_key_ever_reaches_the_browser(self):
        from utility import wema
        with override_settings(WEMA=self.LIVE):
            url = wema.face_verification_url("bvn", "22222222222", "https://api.z/cb")
        self.assertNotIn("MONEY_KEY", url)
        self.assertNotIn("CREATION_KEY", url)

    def test_without_a_channel_id_the_rail_reports_itself_unavailable(self):
        # Rather than sending a blank x_tk and letting the customer meet the bank's
        # own error page.
        from utility import wema
        with override_settings(WEMA={**self.LIVE, "CHANNEL_ID": ""}):
            self.assertFalse(wema.face_verify_live())
