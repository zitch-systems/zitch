"""BVN/NIN verify LIVE: lookup, then a code to the line the identity is
registered to, entered in the Flow. A name match proves someone knows a name;
a code delivered to the registered line proves they control it."""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import hash_identifier
from whatsapp.flows import (FLOW_ID_STATE, IDENTITY_CHAIN, IDENTITY_RETRY, IDENTITY_SCREEN,
                            SUCCESS_SCREEN, handle_flow_request, sign_identity_token)
from whatsapp.models import PendingAction, SystemSetting
from whatsapp.test_flows import MSISDN, _make_user

LOOKUP = "whatsapp.router.verify_bvn"


@override_settings(TESTING=False, DEBUG=False)
class IdentityOtpTests(TestCase):

    def setUp(self):
        self.user = _make_user()
        self.user.bvn_verified = False
        self.user.save(update_fields=["bvn_verified"])

    def _pa(self):
        return PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="kyc", state=FLOW_ID_STATE,
            payload={"id_kind": "bvn"}, expires_at=timezone.now() + timedelta(minutes=10))

    def _submit(self, pa, value):
        return handle_flow_request({"action": "data_exchange",
                                    "flow_token": sign_identity_token(pa),
                                    "data": {"number": value}})

    def _pass_lookup(self, phone="08031234567"):
        return patch(LOOKUP, return_value={"success": True, "first_name": "Ada",
                                           "last_name": "Eze", "phone": f"234{phone[1:]}"})

    def test_a_good_bvn_after_a_typo_still_reaches_the_code_screen(self):
        """The retry screen must route onward too, or a corrected number would
        dead-end on the attempt that finally succeeds."""
        pa = self._pa()
        with patch(LOOKUP, return_value={"success": False, "invalid": True, "message": "no"}):
            self.assertEqual(self._submit(pa, "11111111111")["screen"], IDENTITY_RETRY)
        with self._pass_lookup(), patch("whatsapp.router.flows_live", return_value=True), \
             patch("whatsapp.router.sms_live", return_value=True), \
             patch("whatsapp.router.send_sms", return_value={"success": True}):
            resp = self._submit(pa, "22222222222")
        self.assertEqual(resp["screen"], IDENTITY_CHAIN)

    def test_a_good_bvn_challenges_the_registered_line_instead_of_verifying(self):
        pa = self._pa()
        with self._pass_lookup(), patch("whatsapp.router.flows_live", return_value=True), \
             patch("whatsapp.router.sms_live", return_value=True), \
             patch("whatsapp.router.send_sms", return_value={"success": True}) as sms:
            resp = self._submit(pa, "22222222222")
        self.assertEqual(resp["screen"], IDENTITY_CHAIN)          # the code screen, chained
        self.user.refresh_from_db()
        self.assertFalse(self.user.bvn_verified)                  # NOT yet — the code decides
        self.assertEqual(sms.call_args[0][0], "2348031234567")    # the identity's line

    def test_the_code_verifies_and_the_number_never_reaches_the_chat(self):
        pa = self._pa()
        with self._pass_lookup(), patch("whatsapp.router.flows_live", return_value=True), \
             patch("whatsapp.router.sms_live", return_value=True), \
             patch("whatsapp.router.send_sms", return_value={"success": True}) as sms:
            self._submit(pa, "22222222222")
            code = sms.call_args[0][1].split("Zitch: ")[1][:6]
        pa.refresh_from_db()
        self.assertNotIn("2348031234567", pa.payload["id_otp_to"])   # masked
        with patch("whatsapp.router.reply"):
            done = self._submit(pa, code)
        self.assertEqual(done["screen"], SUCCESS_SCREEN)
        self.user.refresh_from_db()
        self.assertTrue(self.user.bvn_verified)

    def test_a_wrong_code_retries_then_queues_for_review(self):
        pa = self._pa()
        with self._pass_lookup(), patch("whatsapp.router.flows_live", return_value=True), \
             patch("whatsapp.router.sms_live", return_value=True), \
             patch("whatsapp.router.send_sms", return_value={"success": True}):
            self._submit(pa, "22222222222")
        for _ in range(2):
            self.assertEqual(self._submit(pa, "000000")["screen"], IDENTITY_CHAIN)
        third = self._submit(pa, "000000")
        self.assertEqual(third["screen"], SUCCESS_SCREEN)
        self.user.refresh_from_db()
        self.assertFalse(self.user.bvn_verified)
        self.assertIn("wrong verification codes",
                      SystemSetting.get("wa_last_identity_review", ""))

    def test_a_record_with_no_phone_is_reviewed_with_that_reason(self):
        """Never auto-verify because the challenge could not be run — the whole
        point is that the lookup alone is not proof of ownership."""
        pa = self._pa()
        with patch(LOOKUP, return_value={"success": True, "first_name": "Ada",
                                         "last_name": "Eze", "phone": ""}), \
             patch("whatsapp.router.flows_live", return_value=True), \
             patch("whatsapp.router.reply"):
            resp = self._submit(pa, "22222222222")
        self.assertEqual(resp["screen"], SUCCESS_SCREEN)
        self.user.refresh_from_db()
        self.assertFalse(self.user.bvn_verified)
        self.assertIn("no phone number", SystemSetting.get("wa_last_identity_review", ""))

    def test_a_failed_lookup_records_why_it_went_to_review(self):
        pa = self._pa()
        with patch(LOOKUP, return_value={"success": False,
                                         "message": "That BVN does not match the name on this account."}), \
             patch("whatsapp.router.reply"):
            self._submit(pa, "22222222222")
        self.assertIn("does not match", SystemSetting.get("wa_last_identity_review", ""))

    def test_reopening_the_flow_shows_the_code_screen_not_the_number(self):
        pa = self._pa()
        with self._pass_lookup(), patch("whatsapp.router.flows_live", return_value=True), \
             patch("whatsapp.router.sms_live", return_value=True), \
             patch("whatsapp.router.send_sms", return_value={"success": True}):
            self._submit(pa, "22222222222")
        resp = handle_flow_request({"action": "INIT", "flow_token": sign_identity_token(pa)})
        self.assertEqual(resp["screen"], IDENTITY_CHAIN)
        self.assertNotEqual(resp["screen"], IDENTITY_SCREEN)


@override_settings(
    TESTING=False,
    DEBUG=False,
    WEMA={"SIMULATION": True},
    ALLOW_PRODUCTION_SIMULATION=True,
    PREMBLY={"BASE_URL": "https://prembly.invalid", "API_KEY": "staged", "APP_ID": "staged"},
)
class SimulatedIdentityFlowTests(TestCase):
    """The private Flow still collects both IDs, but the values never leave Zitch
    or determine the identity markers stored for a simulation account."""

    def setUp(self):
        self.user = _make_user()
        self.user.phone_verified = True
        self.user.email_verified = True
        self.user.bvn_verified = False
        self.user.nin_verified = False
        self.user.save(update_fields=[
            "phone_verified", "email_verified", "bvn_verified", "nin_verified",
        ])
        self.pa = PendingAction.objects.create(
            user=self.user,
            msisdn=MSISDN,
            action_type="kyc",
            state=FLOW_ID_STATE,
            payload={"id_kind": "bvn"},
            expires_at=timezone.now() + timedelta(minutes=10),
        )

    def _submit(self, value):
        return handle_flow_request({
            "action": "data_exchange",
            "flow_token": sign_identity_token(self.pa),
            "data": {"number": value},
        })

    def test_bvn_and_nin_are_entered_then_verified_without_a_live_lookup(self):
        bvn, nin = "22222222222", "33333333333"
        with patch("utility.providers.requests.post") as provider_post, \
             patch("whatsapp.router.flows_live", return_value=True), \
             patch("whatsapp.router.send_flow", return_value={"success": True}), \
             patch("whatsapp.router.send_sms") as identity_sms, \
             patch("whatsapp.router.reply"):
            first = self._submit(bvn)
            self.pa.refresh_from_db()
            self.assertEqual(self.pa.payload["id_kind"], "nin")
            second = self._submit(nin)

        self.assertEqual(first["screen"], SUCCESS_SCREEN)
        self.assertEqual(second["screen"], SUCCESS_SCREEN)
        self.user.refresh_from_db()
        self.assertTrue(self.user.bvn_verified)
        self.assertTrue(self.user.nin_verified)
        self.assertEqual(self.user.tier, 1)
        self.assertEqual(
            self.user.bvn_hash,
            hash_identifier(f"simulation:bvn:{self.user.pk}"),
        )
        self.assertEqual(
            self.user.nin_hash,
            hash_identifier(f"simulation:nin:{self.user.pk}"),
        )
        self.assertNotEqual(self.user.bvn_hash, hash_identifier(bvn))
        self.assertNotEqual(self.user.nin_hash, hash_identifier(nin))
        provider_post.assert_not_called()
        identity_sms.assert_not_called()


@override_settings(TESTING=False, DEBUG=False)
class InvalidIdentityIsRejectedNotQueuedTests(TestCase):
    """A wrong BVN is the customer's to correct, not an operator's to approve.

    Queueing a name mismatch was the worse half: "this number belongs to someone
    else" is exactly the request a reviewer must never wave through.
    """

    def setUp(self):
        self.user = _make_user()
        self.user.bvn_verified = False
        self.user.save(update_fields=["bvn_verified"])

    def _pa(self):
        return PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="kyc", state=FLOW_ID_STATE,
            payload={"id_kind": "bvn"}, expires_at=timezone.now() + timedelta(minutes=10))

    def _submit(self, pa, value="22222222222"):
        return handle_flow_request({"action": "data_exchange",
                                    "flow_token": sign_identity_token(pa),
                                    "data": {"number": value}})

    def _reject(self, message="That BVN does not match the name on this account."):
        return patch(LOOKUP, return_value={"success": False, "invalid": True, "message": message})

    def test_a_wrong_bvn_asks_again_on_a_screen_that_starts_empty(self):
        """A DIFFERENT screen id, and that is the fix: WhatsApp keeps form state
        across a same-screen re-render, so re-rendering IDENTITY_SCREEN left the
        rejected digits in the box and one tap resubmitted them."""
        pa = self._pa()
        with self._reject():
            resp = self._submit(pa)
        self.assertEqual(resp["screen"], IDENTITY_RETRY)
        self.assertNotEqual(resp["screen"], IDENTITY_SCREEN)
        self.assertIn("isn't valid", resp["data"]["error"])
        self.assertIn("1 attempt(s) left", resp["data"]["error"])

    def test_a_wrong_bvn_is_never_queued_for_review(self):
        pa = self._pa()
        with self._reject():
            self._submit(pa)
        pa.refresh_from_db()
        self.assertNotIn("pending_review", pa.payload)
        self.assertEqual(SystemSetting.get("wa_last_identity_review", ""), "")

    def test_a_rejected_number_is_not_stored_on_the_account(self):
        """An identity that is not the customer's has no business being hashed
        onto their record — a reviewer might later approve what is already there."""
        pa = self._pa()
        with self._reject():
            self._submit(pa)
        self.user.refresh_from_db()
        self.assertFalse(self.user.bvn_hash)
        self.assertFalse(self.user.bvn_verified)

    def test_the_retry_screen_is_bounded_so_it_cannot_be_used_to_probe(self):
        pa = self._pa()
        with self._reject(), patch("whatsapp.router.reply"):
            self.assertEqual(self._submit(pa)["screen"], IDENTITY_RETRY)
            second = self._submit(pa)
        self.assertEqual(second["screen"], SUCCESS_SCREEN)     # terminal, not another guess
        self.assertIn("Too many", second["data"]["message"])

    def test_an_unreachable_provider_still_queues_because_that_one_is_ours(self):
        """The distinction: the provider SAYING no is definitive; not being able
        to ask is our problem, and accusing the customer would be wrong."""
        pa = self._pa()
        with patch(LOOKUP, return_value={"success": False,
                                         "message": "Identity provider unreachable: boom"}), \
             patch("whatsapp.router.reply"):
            resp = self._submit(pa)
        self.assertEqual(resp["screen"], SUCCESS_SCREEN)
        pa.refresh_from_db()
        self.assertEqual(pa.payload.get("pending_review"), "bvn")
        self.assertIn("unreachable", SystemSetting.get("wa_last_identity_review", ""))


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
        from whatsapp.flows import SUCCESS_SCREEN, sign_flow_token

        handle_flow_request({"action": "data_exchange",
                             "flow_token": sign_flow_token(self.pa),
                             "data": {"pin": "999999"}})
        done = handle_flow_request({"action": "data_exchange",
                                    "flow_token": sign_flow_token(self.pa),
                                    "data": {"pin": "1234"}})
        self.assertEqual(done["screen"], SUCCESS_SCREEN)
