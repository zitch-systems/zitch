"""BVN/NIN verify LIVE: lookup, then a code to the line the identity is
registered to, entered in the Flow. A name match proves someone knows a name;
a code delivered to the registered line proves they control it."""
from banking.test_fixtures import bank_channel_case
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import IdentityProof, hash_identifier
from whatsapp.flows import (FLOW_ID_STATE, IDENTITY_CHAIN, IDENTITY_RETRY, IDENTITY_SCREEN,
                            RESULT_SCREEN, SUCCESS_SCREEN, handle_flow_request, sign_identity_token)
from whatsapp.models import PendingAction, SystemSetting
from whatsapp.test_flows import MSISDN, _make_user

LOOKUP = "whatsapp.router.verify_bvn"








@bank_channel_case
class WrongPinRetriesOnAnEmptyScreenTests(TestCase):
    """A wrong PIN must not come back with the wrong PIN still in the box.

    WhatsApp keeps form state across a same-screen re-render (the reason
    PIN_CONFIRM exists), so the retry arrived pre-filled with digits already
    known to be wrong — and one tap spent another of the five attempts on them.
    """

    def setUp(self):
        from whatsapp.test_flows import _transfer_action

        self.user = _make_user()
        self.pa = _transfer_action(self.user)

    def test_a_wrong_pin_returns_a_screen_that_starts_empty(self):
        from whatsapp.flows import PIN_RETRY, PIN_SCREEN, sign_flow_token

        resp = handle_flow_request({"action": "data_exchange",
                                    "flow_token": sign_flow_token(self.pa),
                                    "data": {"pin": "999999"}})
        self.assertEqual(resp["screen"], PIN_RETRY)
        self.assertNotEqual(resp["screen"], PIN_SCREEN)
        self.assertTrue(resp["data"]["error"])
        self.assertIn("5,000", resp["data"]["amount"])       # still says what is being paid

    def test_the_correct_pin_still_executes_from_the_retry_screen(self):
        from whatsapp.flows import RESULT_SCREEN, sign_flow_token

        handle_flow_request({"action": "data_exchange",
                             "flow_token": sign_flow_token(self.pa),
                             "data": {"pin": "999999"}})
        done = handle_flow_request({"action": "data_exchange",
                                    "flow_token": sign_flow_token(self.pa),
                                    "data": {"pin": "1234"}})
        # The point of this test is that the retry screen still EXECUTES; the
        # ending is whatever the executor reported, which here is not a settled
        # success, so it lands on the outcome panel rather than closing.
        self.assertEqual(done["screen"], RESULT_SCREEN)


@bank_channel_case
@override_settings(TESTING=False, DEBUG=False)
class PinResetOtpTests(TestCase):
    """A PIN reset must prove CURRENT possession of the phone, not historical
    flags: the reset opens on a live SMS code, and only the code advances — on
    the same session's next page — to the create/confirm pair."""

    def setUp(self):
        self.user = _make_user()

    def _reset(self):
        from whatsapp.router import _start_pin_reset

        sent = {}

        def capture(msisdn, token, **kw):
            sent.update(kw, token=token)
            return {"success": True}

        with patch("whatsapp.router.flows_live", return_value=True), \
             patch("whatsapp.router.send_flow", side_effect=capture), \
             patch("whatsapp.router.reply"), \
             patch("whatsapp.router.sms_live", return_value=True), \
             patch("whatsapp.router.send_sms", return_value={"success": True}) as sms:
            _start_pin_reset(self.user, MSISDN)
        code = sms.call_args[0][1].split("Zitch: ")[1][:6]
        pa = PendingAction.objects.get(msisdn=MSISDN, action_type="setpin")
        return pa, code, sent

    def _submit(self, pa, **data):
        from whatsapp.flows import handle_flow_request, sign_flow_token

        with patch("whatsapp.router.reply"):
            return handle_flow_request({"action": "data_exchange",
                                        "flow_token": sign_flow_token(pa), "data": data})

    def test_the_reset_opens_on_the_code_page_not_the_pin_pad(self):
        from whatsapp.flows import CODE_SCREEN

        pa, code, sent = self._reset()
        self.assertEqual(sent["screen"], CODE_SCREEN)
        self.assertIn("PIN reset code", sent["screen_data"]["label"])
        self.assertNotIn(code, str(sent))                    # code never in the flow send

    def test_only_the_code_reaches_the_pin_pair_and_the_reset_completes(self):
        from whatsapp.flows import CODE_RETRY, PIN_CHAIN, PIN_CONFIRM, SUCCESS_SCREEN

        pa, code, _ = self._reset()
        wrong = self._submit(pa, number="000000")
        self.assertEqual(wrong["screen"], CODE_RETRY)         # empty box, reason stated
        pa.refresh_from_db()
        ok = self._submit(pa, number=code)
        self.assertEqual(ok["screen"], PIN_CHAIN)             # same session, next page
        pa.refresh_from_db()
        self.assertNotIn("pin_reset_otp_hash", pa.payload)    # single-use
        first = self._submit(pa, pin="246810")
        self.assertEqual(first["screen"], PIN_CONFIRM)
        done = self._submit(pa, pin="246810")
        self.assertEqual(done["screen"], RESULT_SCREEN)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_transaction_pin("246810"))

    def test_three_wrong_codes_cancel_the_reset(self):
        from whatsapp.flows import CODE_RETRY, SUCCESS_SCREEN

        pa, code, _ = self._reset()
        for _ in range(2):
            self.assertEqual(self._submit(pa, number="000000")["screen"], CODE_RETRY)
            pa.refresh_from_db()
        third = self._submit(pa, number="000000")
        self.assertEqual(third["screen"], RESULT_SCREEN)
        self.assertIn("cancelled", third["data"]["message"])
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_transaction_pin("1234"))   # unchanged

    def test_no_sms_channel_refuses_the_chat_reset_outright(self):
        from whatsapp.router import _start_pin_reset

        replies = []
        with patch("whatsapp.router.flows_live", return_value=True), \
             patch("whatsapp.router.send_flow") as flow, \
             patch("whatsapp.router.reply", side_effect=lambda m, t, **k: replies.append(t)), \
             patch("whatsapp.router.sms_live", return_value=False):
            _start_pin_reset(self.user, MSISDN)
        flow.assert_not_called()                              # no pad without the code
        self.assertIn("can't be reset yet", "\n".join(replies))
        self.assertIn("Reply *reset pin*", "\n".join(replies))
        self.assertNotIn("Zitch app", "\n".join(replies))
        self.assertFalse(PendingAction.objects.filter(msisdn=MSISDN,
                                                      action_type="setpin").exists())


@bank_channel_case
class CodeChallengeDeadlineTests(TestCase):
    """A flow waiting on a 10-minute SMS code must not expire in two minutes.

    The PIN reset inherited PIN_TTL from the armed-payment path, so the SMS said
    "expires in 10 minutes" while the action it unlocked died after 2 — making the
    only chat route out of a 24h PIN lockout unusable on a slow network, and then
    reporting it as an expired *payment*.
    """

    def setUp(self):
        pass

    def test_a_pin_reset_outlives_its_own_sms_code(self):
        from django.utils import timezone

        from whatsapp.router import PIN_TTL, _flow_deadline

        code_exp = timezone.now() + timedelta(minutes=10)
        deadline = _flow_deadline("flow_pin", {"pin_reset_otp_exp": code_exp.isoformat()})
        self.assertGreater(deadline, code_exp,
                           "the action must still exist when the code is entered")
        self.assertGreater(deadline, timezone.now() + PIN_TTL)

    def test_once_the_code_is_consumed_the_short_clock_resumes(self):
        from django.utils import timezone

        from whatsapp.router import PIN_TTL, _flow_deadline

        # No code in the payload -> the ordinary armed-PIN window applies again.
        deadline = _flow_deadline("flow_pin", {})
        self.assertLessEqual(deadline, timezone.now() + PIN_TTL + timedelta(seconds=5))


@bank_channel_case
@override_settings(TESTING=False, DEBUG=False)
class AccountOtpScreenNamesTheIdentityRecordTests(TestCase):
    """The bank's code goes to the phone on the BVN/NIN record, not the Zitch line.

    This screen used to mask and display the customer's OWN number, on the belief
    that ALAT texts the phoneNumber supplied at creation. It does not — it validates
    the identity against its register and sends the consent code to the line held
    there. Showing the Zitch number sent people to a silent handset and made the NIN
    step read as though it wanted a BVN code, which is exactly how it was reported.
    """

    def setUp(self):
        self.user = _make_user()

    def _pa(self, payload):
        from whatsapp.router import _flow_deadline
        return PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="add_account",
            state=FLOW_ID_STATE, payload=payload,
            expires_at=_flow_deadline("otp"))

    def _screen(self, payload):
        from whatsapp.flows import _account_otp_screen
        return _account_otp_screen(self._pa(payload))["data"]

    def test_it_names_the_record_and_never_the_customers_own_number(self):
        digits = "".join(ch for ch in str(self.user.phone or "") if ch.isdigit())
        for payload, kind in (({"using_bvn": False}, "NIN"), ({"using_bvn": True}, "BVN")):
            with self.subTest(kind=kind):
                data = self._screen(payload)
                self.assertIn(f"registered on your {kind}", data["summary"])
                # Not even the masked head/tail the old screen showed: any slice of
                # the account's own number is a pointer to the wrong handset.
                self.assertNotIn(digits[-4:], data["summary"])
                self.assertNotIn(digits[:4], data["summary"])

    def test_tracking_record_overrides_a_stale_bvn_label_for_nin(self):
        from wallet.models import WemaProvisioningAttempt
        from whatsapp.flows import _account_otp_screen

        attempt = WemaProvisioningAttempt.objects.create(
            user=self.user,
            tracking_id="nin-track-1",
            identity_type=WemaProvisioningAttempt.NIN,
            identity_hash=hash_identifier("33333333333"),
            identity_last4="3333",
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        pa = self._pa({
            "tracking_id": attempt.tracking_id,
            "using_bvn": True,
            "id_type": "bvn",
        })
        data = _account_otp_screen(pa)["data"]
        self.assertIn("registered on your NIN", data["summary"])
        self.assertNotIn("registered on your BVN", data["summary"])

    def test_a_blank_tracking_id_does_not_borrow_another_attempts_identity(self):
        """Wema does not always return a tracking id, and `filter(tracking_id="")`
        then matches whatever other row for this user carries an empty one. For a
        customer with a verified BVN that is very likely the BVN row - which is
        how a NIN challenge came to be labelled BVN on a live account."""
        from wallet.models import WemaProvisioningAttempt
        from whatsapp.flows import _account_otp_screen

        WemaProvisioningAttempt.objects.create(
            user=self.user,
            tracking_id="",                       # the row a blank key would match
            identity_type=WemaProvisioningAttempt.BVN,
            identity_hash=hash_identifier("22222222222"),
            identity_last4="2222",
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        data = _account_otp_screen(self._pa({"tracking_id": "", "using_bvn": False}))["data"]
        self.assertIn("registered on your NIN", data["summary"])
        self.assertNotIn("registered on your BVN", data["summary"])

    def test_the_hint_sends_an_unreachable_customer_to_face_not_resend(self):
        """A resend goes back to the same registered line, so offering it as the
        remedy is a loop. The face route is the one that can actually finish."""
        data = self._screen({"using_bvn": False})
        self.assertIn("face", data["error"].lower())
        self.assertNotIn("RESEND", data["error"])

    def test_it_falls_back_to_the_menu_choice_when_the_rail_is_not_recorded_yet(self):
        self.assertIn("BVN", self._screen({"id_type": "bvn"})["summary"])
        self.assertIn("NIN", self._screen({"id_type": "nin"})["summary"])


@bank_channel_case
@override_settings(TESTING=False, DEBUG=False)
class IdentityOtpScreenNeverGuessesBvnTests(TestCase):
    """The KYC identity challenge must not label itself BVN by default.

    `_identity_otp_screen` derived its label from
    `(payload.get("id_otp_kind") or "bvn").upper()`, so any session reaching this
    screen without that key rendered "BVN code" and told the customer a BVN code
    had been sent. On a NIN verification that names the wrong document and points
    at the wrong phone. It is the same defect that was fixed on the account-
    creation screen, still live on this second code path.
    """

    def setUp(self):
        self.user = _make_user()

    def _screen(self, payload, error=""):
        from whatsapp.flows import _identity_otp_screen
        from whatsapp.router import _flow_deadline
        pa = PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="kyc",
            state=FLOW_ID_STATE, payload=payload,
            expires_at=_flow_deadline("otp"))
        return _identity_otp_screen(pa, error=error)["data"]

    def test_a_nin_challenge_is_labelled_nin(self):
        data = self._screen({"id_otp_kind": "nin", "id_otp_to": "•••••6789"})
        self.assertEqual(data["label"], "NIN code")
        self.assertNotIn("BVN", data["summary"])

    def test_a_bvn_challenge_is_still_labelled_bvn(self):
        data = self._screen({"id_otp_kind": "bvn", "id_otp_to": "•••••6789"})
        self.assertEqual(data["label"], "BVN code")

    def test_an_unknown_identity_is_never_called_bvn(self):
        """The whole bug in one assertion: with nothing in the payload saying
        which document this is, the old code asserted BVN."""
        data = self._screen({})
        self.assertNotIn("BVN", data["label"])
        self.assertNotIn("BVN", data["summary"])
        self.assertEqual(data["label"], "Verification code")

    def test_it_falls_back_to_the_menu_choice_before_giving_up(self):
        """id_type is written when the customer picks the rail, so it is a
        truthful answer when the challenge key is missing."""
        data = self._screen({"id_type": "nin"})
        self.assertEqual(data["label"], "NIN code")
        self.assertIn("NIN", data["summary"])

    def test_a_junk_identity_value_is_not_echoed_into_the_screen(self):
        data = self._screen({"id_otp_kind": "passport"})
        self.assertEqual(data["label"], "Verification code")
        self.assertNotIn("PASSPORT", data["summary"])
