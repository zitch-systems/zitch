"""The bank face check, in chat.

The chat can only ever hand over a link. The outcome arrives on our own callback,
so nothing in this module may mark anybody verified — a customer who opens the
bank's page and closes it is exactly as unverified as one who never tapped.

The other property under test is that the identity number never lands in the
thread: it is collected in the encrypted Flow, and where there is no Flow the step
is skipped rather than asked for in clear.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from wallet.models import WemaFaceSession
from wallet.services import get_or_create_wallet

from . import router
from .models import PendingAction, WhatsAppLink

User = get_user_model()
MSISDN = "2348011113333"

FACE_ON = {"KEYS": {"wallet": "k"}, "CHANNEL_ID": "c", "SIMULATION": False,
           "FACE_VERIFY_URL": "https://face.example/", "CALLBACK_TOKEN": "tok",
           "CALLBACK_TOKEN_PREV": "", "CALLBACK_ENFORCE_IPS": False, "CALLBACK_IPS": []}
# The face rail needs the channel id (x_tk) and a base URL — see utility.wema._face_key.
FACE_OFF = {**FACE_ON, "CHANNEL_ID": "", "FACE_VERIFY_URL": ""}


#: The identity this test account has actually PROVEN. The face session must bind
#: to it — see accounts.views.face_identity_error.
VERIFIED_BVN = "22222222222"


def _user(**flags):
    from accounts.models import hash_identifier
    u = User.objects.create(username="f1", phone="08010000009", email="f@z.ng",
                            first_name="Ada", last_name="Eze", tier=1,
                            email_verified=True, phone_verified=True,
                            bvn_verified=True, nin_verified=True,
                            bvn_hash=hash_identifier(VERIFIED_BVN), **flags)
    u.save()
    get_or_create_wallet(u)
    WhatsAppLink.objects.create(user=u, wa_msisdn=MSISDN, status=WhatsAppLink.ACTIVE)
    return u


@override_settings(WEMA=FACE_ON)
class FaceStepLadderTests(TestCase):
    def test_the_ladder_lists_the_face_check_when_the_rail_is_live(self):
        u = _user()
        self.assertIn("face", router._kyc_outstanding(u))
        self.assertIn("Face check", router._kyc_status_lines(u))

    def test_a_verified_face_is_no_longer_outstanding(self):
        u = _user(face_verified=True)
        self.assertNotIn("face", router._kyc_outstanding(u))

    @override_settings(WEMA=FACE_OFF)
    def test_the_step_is_hidden_when_the_rail_is_not_configured(self):
        # A rung nobody can ever climb reads as a broken account.
        u = _user()
        self.assertNotIn("face", router._kyc_outstanding(u))
        self.assertNotIn("Face check", router._kyc_status_lines(u))

    def test_the_face_step_is_not_offered_before_an_identity_is_verified(self):
        # It runs against a BVN/NIN the customer has proven. Offering it first
        # would send them to the bank with a number we have no reason to trust.
        u = _user()
        u.bvn_verified = u.nin_verified = False
        u.save(update_fields=["bvn_verified", "nin_verified"])
        self.assertNotIn("face", router._kyc_outstanding(u))


@override_settings(WEMA=FACE_ON)
class FaceLinkTests(TestCase):
    def setUp(self):
        self.user = _user()
        self.pa = PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="kyc", state="idle",
            payload={}, expires_at=router._flow_deadline("idle"))

    def test_the_link_is_sent_as_a_button_and_nothing_is_marked_verified(self):
        with patch.object(router, "send_cta_url") as cta, patch.object(router, "reply"):
            router._kyc_send_face_link(self.pa, self.user, MSISDN, "bvn", "22222222222")
        self.user.refresh_from_db()
        self.assertFalse(self.user.face_verified)
        cta.assert_called_once()
        self.assertIn("face.example", cta.call_args.args[2])

    def test_the_session_binds_the_identity_that_was_entered(self):
        from accounts.models import hash_identifier
        with patch.object(router, "send_cta_url"), patch.object(router, "reply"):
            router._kyc_send_face_link(self.pa, self.user, MSISDN, "bvn", VERIFIED_BVN)
        s = WemaFaceSession.objects.get(user=self.user)
        self.assertEqual(s.identity_type, "bvn")
        self.assertEqual(s.identity_hash, hash_identifier(VERIFIED_BVN))
        self.assertEqual(s.status, WemaFaceSession.PENDING)

    def test_the_number_never_appears_in_the_chat_body(self):
        # The URL carries the BVN to the bank, but it rides inside a CTA button where
        # only the label shows. The message BODY must not repeat it — pasted into the
        # text it would sit in the customer's history in clear, forever.
        with patch.object(router, "send_cta_url") as cta, patch.object(router, "reply"):
            router._kyc_send_face_link(self.pa, self.user, MSISDN, "bvn", "22222222222")
        body = str(cta.call_args.args[1])
        self.assertNotIn("22222222222", body)
        self.assertNotIn("http", body)

    def test_the_session_state_is_never_shown_to_the_customer(self):
        # It is the capability that completes the verification; it belongs in the
        # callback URL and nowhere a screenshot could carry it.
        with patch.object(router, "send_cta_url") as cta, patch.object(router, "reply"):
            router._kyc_send_face_link(self.pa, self.user, MSISDN, "bvn", "22222222222")
        state = WemaFaceSession.objects.get(user=self.user).state
        self.assertNotIn(state, str(cta.call_args.args[1]))

    def test_without_a_flow_the_step_is_skipped_not_asked_in_chat(self):
        # The number is only being forwarded, so a clear-text BVN in the thread
        # would buy nothing at all.
        with patch.object(router, "_send_identity_flow", return_value=False), \
             patch.object(router, "reply") as rep, \
             patch.object(router, "_kyc_next"):
            router._kyc_start_face_step(self.pa, self.user, MSISDN)
        sent = " ".join(str(c.args[1]) for c in rep.call_args_list)
        self.assertNotIn("11-digit", sent)
        self.assertIn("Zitch app", sent)


@override_settings(WEMA=FACE_ON)
class FaceCallbackNotifiesChatTests(TestCase):
    def test_the_customer_is_told_when_the_bank_confirms(self):
        from datetime import timedelta

        from django.utils import timezone

        from accounts.models import hash_identifier
        user = _user()
        session = WemaFaceSession.objects.create(
            user=user, state="q" * 40, identity_type="bvn",
            identity_hash=hash_identifier("22222222222"),
            expires_at=timezone.now() + timedelta(minutes=20))
        with patch("whatsapp.router.reply") as rep:
            res = self.client.post(f"/webhooks/wema/face/{session.state}",
                                   {"success": True, "c_id": "C1", "id": "22222222222"},
                                   content_type="application/json")
        self.assertEqual(res.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.face_verified)
        rep.assert_called_once()
        self.assertIn("Face check confirmed", str(rep.call_args.args[1]))

    def test_a_messaging_failure_never_fails_the_callback(self):
        # The tier is already lifted; a 500 here would have the bank retry a
        # verification that already succeeded.
        from datetime import timedelta

        from django.utils import timezone

        from accounts.models import hash_identifier
        user = _user()
        session = WemaFaceSession.objects.create(
            user=user, state="r" * 40, identity_type="bvn",
            identity_hash=hash_identifier("22222222222"),
            expires_at=timezone.now() + timedelta(minutes=20))
        with patch("whatsapp.router.reply", side_effect=RuntimeError("wa down")):
            res = self.client.post(f"/webhooks/wema/face/{session.state}",
                                   {"success": True, "c_id": "C1", "id": "22222222222"},
                                   content_type="application/json")
        self.assertEqual(res.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.face_verified)


@override_settings(WEMA=FACE_ON)
class TypedInChatTests(TestCase):
    """The face step asks for the identity in the Flow — but a customer can always
    type it into the thread instead, and that path had no branch at all.

    They were told "Got it", dropped at the main menu, and no face check was ever
    started — with their BVN left sitting in the chat. Refusing the number at that
    point would cost them the step and save nothing, since it is already in their
    history, so it is accepted and the deletion tip is named.
    """

    def setUp(self):
        self.user = _user()
        self.pa = PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="kyc",
            state=router.FACE_ID_STATE, payload={"id_kind": "bvn", "id_purpose": "face"},
            expires_at=router._flow_deadline("idle"))

    def test_a_typed_number_still_starts_the_face_check(self):
        with patch.object(router, "send_cta_url") as cta, \
             patch.object(router, "reply"), patch.object(router, "_kyc_next"):
            router._advance_kyc(self.pa, self.user, MSISDN, "22222222222")
        cta.assert_called_once()
        self.assertIn("face.example", cta.call_args.args[2])
        self.assertEqual(WemaFaceSession.objects.filter(user=self.user).count(), 1)

    def test_the_customer_is_told_how_to_remove_it_from_the_thread(self):
        with patch.object(router, "send_cta_url"), \
             patch.object(router, "reply") as rep, patch.object(router, "_kyc_next"):
            router._advance_kyc(self.pa, self.user, MSISDN, "22222222222")
        sent = " ".join(str(c.args[1]) for c in rep.call_args_list).lower()
        self.assertIn("delete", sent)

    def test_a_short_number_is_re_asked_not_dumped_to_the_menu(self):
        with patch.object(router, "send_cta_url") as cta, \
             patch.object(router, "reply") as rep, patch.object(router, "send_menu") as menu:
            router._advance_kyc(self.pa, self.user, MSISDN, "12345")
        cta.assert_not_called()
        menu.assert_not_called()
        self.assertIn("11 digits", str(rep.call_args.args[1]))

    def test_the_identity_is_not_re_verified(self):
        # It is forwarded to the bank, not checked here. Routing it through the
        # ordinary bvn/nin branch would re-run verification on an identity the
        # customer has already proven — and never send the link.
        with patch.object(router, "send_cta_url"), patch.object(router, "reply"), \
             patch.object(router, "_kyc_next"), \
             patch.object(router, "_kyc_submit_identity") as submit:
            router._advance_kyc(self.pa, self.user, MSISDN, "22222222222")
        submit.assert_not_called()
