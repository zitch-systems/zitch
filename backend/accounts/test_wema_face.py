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

# No token segment: this URL is handed to the customer, so it carries the
# per-session state alone. See accounts.views._face_callback_url.
CB = "/webhooks/wema/face/{}"


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


class TheCallbackUrlCarriesNoSharedSecretTests(TestCase):
    """This URL is handed to the CUSTOMER.

    It rides in the bank page's `cb_uri`, so it is visible in a WebView address bar,
    in WhatsApp's in-app browser and its history, in Meta's CTA target, and in ALAT's
    own request logs. It once carried WEMA_CALLBACK_TOKEN — the single shared secret
    that also guards the payout AUTHORISATION callback — which published to every
    customer who verified their face the value that decides whether a transfer may
    proceed.
    """

    def test_the_shared_callback_token_never_appears_in_the_url(self):
        from accounts.views import _face_callback_url
        with override_settings(
                WEMA={"CALLBACK_TOKEN": "SUPERSECRETTOKENVALUE0123456789ab",
                      "FACE_CALLBACK_IPS": ["1.2.3.4"]},
                ZITCH_LINKS={"API_BASE": "https://api.zitch.ng"}):
            url = _face_callback_url("STATE123")
        self.assertNotIn("SUPERSECRETTOKENVALUE", url)
        self.assertTrue(url.endswith("/webhooks/wema/face/STATE123"))


@override_settings(
    WEMA={"CALLBACK_TOKEN": "tok", "CALLBACK_TOKEN_PREV": "",
          "CALLBACK_ENFORCE_IPS": False, "CALLBACK_IPS": [],
          "FACE_CALLBACK_IPS": ["9.9.9.9"],
          "KEYS": {"wallet": "k"}, "CHANNEL_ID": "c", "SIMULATION": False,
          "FACE_VERIFY_URL": "https://face.example/"})
class FaceSessionBindsToTheProvenIdentityTests(TestCase):
    """A face check against an identity the account never proved establishes nothing.

    The session used to bind to whatever eleven digits the caller sent, and the
    callback then compared the bank's answer to that same self-chosen value — a loop
    that agrees with itself. Someone who had taken over an account documented to
    another person could pass liveness honestly with their OWN BVN and lift the
    victim's tier, which is precisely the substitution this step exists to catch.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="b1", phone="08040000001",
                                             password="Str0ng!pass1", email="b@z.ng")
        self.user.email_verified = True
        self.user.bvn_verified = True
        self.user.bvn_hash = hash_identifier("22222222222")
        self.user.save()
        from accounts.models import AccessToken
        self.token = AccessToken.issue(self.user).key

    def _start(self, **body):
        return self.client.post("/api/kyc/face/start/",
                                {"access_token": self.token, **body},
                                content_type="application/json")

    def test_the_proven_identity_opens_a_session(self):
        res = self._start(bvn="22222222222")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(WemaFaceSession.objects.filter(user=self.user).count(), 1)

    def test_a_different_identity_is_refused(self):
        res = self._start(bvn="33333333333")
        self.assertEqual(res.status_code, 400)
        self.assertFalse(WemaFaceSession.objects.filter(user=self.user).exists())

    def test_an_unverified_identity_type_is_refused(self):
        # NIN is not verified on this account, so it cannot anchor a face check.
        res = self._start(nin="44444444444")
        self.assertEqual(res.status_code, 400)
        self.assertFalse(WemaFaceSession.objects.filter(user=self.user).exists())


class TheFaceRailRefusesWhatItCannotAuthenticateTests(TestCase):
    """With no source-IP allowlist the callback has no authentication at all.

    It carries no shared token by design, so the allowlist is the whole control. An
    unset list must mean "do not offer the rail", not "accept anything" — otherwise
    reading the URL out of your own browser is enough to grant yourself a tier.
    """

    def test_without_face_callback_ips_the_rail_is_unavailable(self):
        from utility import wema
        with override_settings(
                DEBUG=False, TESTING=False,
                WEMA={"CHANNEL_ID": "c", "SIMULATION": False,
                      "FACE_VERIFY_URL": "https://face.example/",
                      "FACE_CALLBACK_IPS": []}):
            self.assertFalse(wema.face_verify_live())

    def test_with_the_allowlist_configured_the_rail_is_available(self):
        from utility import wema
        with override_settings(
                DEBUG=False, TESTING=False,
                WEMA={"CHANNEL_ID": "c", "SIMULATION": False,
                      "FACE_VERIFY_URL": "https://face.example/",
                      "FACE_CALLBACK_IPS": ["9.9.9.9"]}):
            self.assertTrue(wema.face_verify_live())

    def test_an_unset_verifier_url_is_not_a_dev_default(self):
        # It used to default to ALAT's DEV verifier, which answers happily and
        # proves nothing — a fail-OPEN default guarded only by a preflight command.
        from django.conf import settings
        import importlib
        self.assertNotIn("face-verification-dev",
                         str(importlib.import_module("zitch_api.settings").WEMA.get(
                             "FACE_VERIFY_URL", "") or ""))


@override_settings(
    WEMA={"CALLBACK_TOKEN": "tok", "CALLBACK_TOKEN_PREV": "",
          "CALLBACK_ENFORCE_IPS": False, "CALLBACK_IPS": [],
          "FACE_CALLBACK_IPS": [], "KEYS": {"wallet": "k"}, "CHANNEL_ID": "c",
          "SIMULATION": False, "FACE_VERIFY_URL": "https://face.example/"})
class TheRawIdentityNeverReachesTheForensicTableTests(TestCase):
    """WebhookEvent is deliberately immutable, which makes it the worst place for a
    raw BVN to land. ALAT names the identity number "id" — a key generic enough to
    walk straight past a redaction list built from the field names we expected."""

    def test_the_recorded_payload_holds_a_fingerprint_not_the_number(self):
        from whatsapp.models import WebhookEvent

        session = WemaFaceSession.objects.create(
            user=User.objects.create_user(username="w1", phone="08050000001",
                                          password="Str0ng!pass1"),
            state="w" * 40, identity_type="bvn",
            identity_hash=hash_identifier("22222222222"),
            expires_at=timezone.now() + timedelta(minutes=20))
        self.client.post(f"/webhooks/wema/face/{session.state}",
                         {"success": True, "c_id": "C1", "id": "22222222222"},
                         content_type="application/json")
        row = WebhookEvent.objects.filter(source="wema.face").order_by("-id").first()
        self.assertIsNotNone(row)
        blob = str(row.payload)
        self.assertNotIn("22222222222", blob)
        self.assertIn("sha256:", blob)
