import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from .flows import FLOW_PIN_STATE, handle_flow_request, resolve_flow_token, sign_flow_token
from .login_flow import _resolve, _token, start_login
from .models import PendingAction, WaOnboarding, WhatsAppLink


@override_settings(WHATSAPP_FLOW={"RESULT_SCREEN": True})
class WhatsAppLoginTests(TestCase):
    def setUp(self):
        cache.clear()
        self.enterContext(patch("requests.sessions.Session.request",
                                side_effect=AssertionError("Unexpected network call")))
        self.user = User.objects.create_user(
            username="08099990001", phone="08099990001", email="owner@example.com",
            phone_verified=True, email_verified=True,
            transaction_pin=make_password("638291"),
        )
        self.msisdn = "2348099990001"

    def start(self):
        with patch("whatsapp.providers.flows_live", return_value=True), \
             patch("whatsapp.providers.send_flow", return_value={"success": True}), \
             patch("whatsapp.login_flow.send_email", return_value={"success": True}), \
             patch("whatsapp.login_flow.secrets.randbelow", return_value=123456):
            start_login(self.msisdn)
        return _token(WaOnboarding.objects.get(msisdn=self.msisdn))

    def exchange(self, token, data, action="data_exchange"):
        return handle_flow_request({"action": action, "flow_token": token, "data": data})

    def test_email_code_and_pin_are_both_required_and_session_is_single_use(self):
        token = self.start()
        self.assertEqual(self.exchange(token, {}, "INIT")["screen"], "CODE_SCREEN")
        self.assertEqual(self.exchange(token, {"number": "123456"})["screen"], "PIN_CHAIN")
        self.assertFalse(WhatsAppLink.objects.exists())
        self.assertEqual(self.exchange(token, {"pin": "000000"})["screen"], "PIN_RETRY")
        self.assertFalse(WhatsAppLink.objects.exists())
        result = self.exchange(token, {"pin": "638291"})
        self.assertEqual(result["data"]["status"], "Done")
        link = WhatsAppLink.objects.get()
        self.assertEqual(link.user_id, self.user.pk)
        self.assertEqual(link.wa_msisdn, self.msisdn)
        self.assertFalse(WaOnboarding.objects.exists())
        self.assertIn("ended", self.exchange(token, {"pin": "638291"})["data"]["message"])

    def test_pin_cannot_skip_email_and_wrong_codes_end_session(self):
        token = self.start()
        self.assertEqual(self.exchange(token, {"pin": "638291"})["screen"], "CODE_RETRY")
        self.exchange(token, {"number": "111111"})
        self.assertFalse(WhatsAppLink.objects.exists())
        self.assertFalse(WaOnboarding.objects.exists())

    def test_changed_phone_revokes_login(self):
        token = self.start()
        self.user.phone = "08099990002"
        self.user.save(update_fields=["phone"])
        self.exchange(token, {"number": "123456"})
        self.assertFalse(WhatsAppLink.objects.exists())
        self.assertFalse(WaOnboarding.objects.exists())

    def test_restart_invalidates_previous_token_even_when_row_id_is_reused(self):
        old = self.start()
        cache.clear()
        new = self.start()
        self.assertNotEqual(old, new)
        self.assertIn("ended", self.exchange(old, {"number": "123456"})["data"]["message"])

    def test_wrong_pin_uses_shared_account_attempt_counter(self):
        token = self.start()
        self.exchange(token, {"number": "123456"})
        self.exchange(token, {"pin": "111111"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.pin_failed_attempts, 1)

    def test_does_not_link_unknown_or_inactive_number(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        with patch("whatsapp.providers.flows_live", return_value=True), patch("whatsapp.router.reply"):
            start_login(self.msisdn)
        self.assertFalse(WaOnboarding.objects.exists())

    def test_successful_login_retires_previous_active_link(self):
        old = WhatsAppLink.objects.create(user=self.user, wa_msisdn="2348088880001", status="active")
        token = self.start()
        self.exchange(token, {"number": "123456"})
        self.exchange(token, {"pin": "638291"})
        self.assertFalse(WhatsAppLink.objects.filter(pk=old.pk).exists())

    def test_forged_token_cannot_load_signin(self):
        token = self.start()
        token = token[:-1] + ("a" if token[-1] != "a" else "b")
        self.assertEqual(self.exchange(token, {}, "INIT")["data"]["status"], "❌ Not completed")

    def test_malformed_tokens_are_bounded_before_integer_conversion_or_database(self):
        invalid = [None, {}, "", "lg².sig", "lg" + "9" * 5000 + ".sig",
                   "lg9223372036854775808." + "a" * 22, "lg1." + "é" * 22,
                   "lg0." + "a" * 22, "lg-1." + "a" * 22, "lg1.a"]
        for token in invalid:
            with self.subTest(token_type=type(token).__name__), self.assertNumQueries(0):
                self.assertIsNone(_resolve(token))

    def test_pin_reset_requirement_prevents_email_send_and_challenge_creation(self):
        User.objects.filter(pk=self.user.pk).update(pin_reset_required=True)
        with patch("whatsapp.providers.flows_live", return_value=True), \
                patch("whatsapp.login_flow.send_email") as email, patch("whatsapp.router.reply"):
            start_login(self.msisdn)
        email.assert_not_called()
        self.assertFalse(WaOnboarding.objects.exists())

    def test_pin_reset_requirement_added_mid_login_blocks_even_correct_pin(self):
        token = self.start()
        self.exchange(token, {"number": "123456"})
        User.objects.filter(pk=self.user.pk).update(pin_reset_required=True)
        with patch("whatsapp.login_flow.evaluate_transaction_pin") as verify:
            response = self.exchange(token, {"pin": "638291"})
        verify.assert_not_called()
        self.assertIn("Reset", response["data"]["message"])
        self.assertFalse(WhatsAppLink.objects.exists())
        self.assertFalse(WaOnboarding.objects.exists())

    def test_password_or_pin_reset_revokes_challenge_before_and_after_email_proof(self):
        for field in ("password", "transaction_pin"):
            for email_proven in (False, True):
                with self.subTest(field=field, email_proven=email_proven):
                    cache.clear()
                    old_value = getattr(User.objects.get(pk=self.user.pk), field)
                    token = self.start()
                    if email_proven:
                        self.exchange(token, {"number": "123456"})
                    User.objects.filter(pk=self.user.pk).update(**{field: make_password("529846")})
                    # Even knowledge of the NEW PIN cannot reuse old email proof.
                    response = self.exchange(token, {"pin": "529846", "number": "123456"})
                    self.assertIn("Account details changed", response["data"]["message"])
                    self.assertFalse(WhatsAppLink.objects.exists())
                    self.assertFalse(WaOnboarding.objects.exists())
                    User.objects.filter(pk=self.user.pk).update(**{field: old_value})

    def test_different_country_same_phone_suffix_revokes_challenge(self):
        token = self.start()
        User.objects.filter(pk=self.user.pk).update(phone="+18099990001", phone_verified=True)
        response = self.exchange(token, {"number": "123456"})
        self.assertIn("Account details changed", response["data"]["message"])
        self.assertFalse(WaOnboarding.objects.exists())
        self.assertFalse(WhatsAppLink.objects.exists())

    def test_phone_or_email_change_after_code_proof_revokes_challenge(self):
        for field, new_value in (("phone", "08099990002"), ("email", "new@example.test")):
            with self.subTest(field=field):
                cache.clear()
                old_value = getattr(User.objects.get(pk=self.user.pk), field)
                token = self.start()
                self.exchange(token, {"number": "123456"})
                User.objects.filter(pk=self.user.pk).update(**{field: new_value})
                self.exchange(token, {"pin": "638291"})
                self.assertFalse(WhatsAppLink.objects.exists())
                self.assertFalse(WaOnboarding.objects.exists())
                User.objects.filter(pk=self.user.pk).update(**{field: old_value})

    def test_relink_preserves_privacy_and_marketing_preferences(self):
        for ai, marketing in ((False, False), (False, True), (True, False), (True, True)):
            with self.subTest(ai=ai, marketing=marketing):
                cache.clear()
                WhatsAppLink.objects.filter(user=self.user).delete()
                old = WhatsAppLink.objects.create(
                    user=self.user, wa_msisdn="2348088880001", status="active",
                    ai_enabled=ai, marketing_opt_in=marketing,
                )
                token = self.start()
                self.exchange(token, {"number": "123456"})
                self.exchange(token, {"pin": "638291"})
                fresh = WhatsAppLink.objects.get(user=self.user, status="active")
                self.assertNotEqual(fresh.pk, old.pk)
                self.assertEqual((fresh.ai_enabled, fresh.marketing_opt_in), (ai, marketing))

    def test_menu_delivery_exception_does_not_undo_or_misreport_completed_login(self):
        token = self.start()
        self.exchange(token, {"number": "123456"})
        with patch("whatsapp.router.send_menu", side_effect=RuntimeError(
                "Private provider error " + self.user.email)), \
                self.assertLogs("zitch.security", level="WARNING") as logs, \
                self.captureOnCommitCallbacks(execute=True):
            response = self.exchange(token, {"pin": "638291"})
        self.assertEqual(response["data"]["status"], "Done")
        self.assertTrue(WhatsAppLink.objects.filter(user=self.user, status="active").exists())
        self.assertFalse(WaOnboarding.objects.exists())
        self.assertIn("wa_login_menu_delivery_failed", logs.output[0])
        self.assertNotIn(self.user.email, " ".join(logs.output))
        self.assertIsNone(logs.records[0].exc_info)

    def test_relink_retires_old_flow_capability_but_keeps_authorized_payment(self):
        from .router import EXECUTING_STATE

        old = WhatsAppLink.objects.create(user=self.user, wa_msisdn="2348088880001", status="active")
        pending = PendingAction.objects.create(
            user=self.user, msisdn=old.wa_msisdn, action_type="transfer", state=FLOW_PIN_STATE,
            expires_at=timezone.now() + timedelta(minutes=2),
        )
        executing = PendingAction.objects.create(
            user=self.user, msisdn=old.wa_msisdn, action_type="transfer", state=EXECUTING_STATE,
            expires_at=timezone.now() + timedelta(minutes=15),
        )
        signed = sign_flow_token(pending)
        token = self.start()
        self.exchange(token, {"number": "123456"})
        self.exchange(token, {"pin": "638291"})
        self.assertIsNone(resolve_flow_token(signed))
        pending.refresh_from_db()
        executing.refresh_from_db()
        self.assertTrue(pending.expired)
        self.assertFalse(executing.expired)

    def test_challenge_contains_only_credential_digest_not_hashes_or_email(self):
        self.start()
        payload = WaOnboarding.objects.get(msisdn=self.msisdn).payload
        self.assertEqual(len(payload["credentials"]), 64)
        for private in (self.user.transaction_pin, self.user.email, "638291", "123456"):
            self.assertNotIn(private, json.dumps(payload))

    def test_shared_pin_lockout_still_blocks_correct_pin(self):
        token = self.start()
        self.exchange(token, {"number": "123456"})
        User.objects.filter(pk=self.user.pk).update(pin_locked_until=timezone.now() + timedelta(hours=1))
        response = self.exchange(token, {"pin": "638291"})
        self.assertIn("locked", response["data"]["message"])
        self.assertFalse(WhatsAppLink.objects.exists())

    def test_expired_login_cannot_advance(self):
        token = self.start()
        WaOnboarding.objects.filter(msisdn=self.msisdn).update(expires_at=timezone.now() - timedelta(seconds=1))
        response = self.exchange(token, {"number": "123456"})
        self.assertIn("ended", response["data"]["message"])
        self.assertFalse(WhatsAppLink.objects.exists())
