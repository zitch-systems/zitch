"""Prembly ownership is available before any VAS allocation invitation."""
import json
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import IdentityProof, User, hash_identifier
from wema_vas.models import VirtualAccount
from whatsapp import flows, router, vas_identity
from whatsapp.models import PendingAction, WhatsAppLink


@override_settings(BANK_ACCOUNT_PROVIDER="wema_vas", WEMA_PARTNERSHIP_MODE="archive",
                   KYC_PROVIDER="prembly", WEMA_VAS={"ENABLED": False,
                   "RELEASE_PHASE": "validation", "VALIDATION_USER_IDS": []},
                   DEBUG=True, TESTING=True,
                   PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class StandaloneVasIdentityTests(TestCase):
    def setUp(self):
        self.msisdn, self.raw = "2348011112222", "12345678901"
        self.user = User.objects.create(username="identity-only", phone="+" + self.msisdn,
            email="signup@example.test", email_verified=True, phone_verified=True,
            first_name="Profile", last_name="Name")
        self.link = WhatsAppLink.objects.create(user=self.user, wa_msisdn=self.msisdn,
                                               status=WhatsAppLink.ACTIVE)
        self.pa = PendingAction.objects.create(user=self.user, msisdn=self.msisdn,
            action_type="kyc", state="idle", payload={"id_kind": "bvn"},
            expires_at=timezone.now() + timedelta(minutes=10))
        self.assertTrue(vas_identity.arm(self.pa, self.user, self.msisdn))
        self.pa.state = flows.FLOW_ID_STATE
        self.pa.save(update_fields=["state"])
        self.record = {"success": True, "first_name": "Ada", "middle_name": "",
                       "last_name": "Eze", "phone": "2348077778888",
                       "email": "record@example.test"}
        patches = {
            "live": patch("utility.providers._prembly_identity_live", return_value=True),
            "lookup": patch("utility.providers.prembly_verify_bvn", return_value=self.record),
            "nin": patch("utility.providers.prembly_verify_nin", return_value=self.record),
            "flows": patch.object(router, "flows_live", return_value=True),
            "sms_live": patch.object(router, "sms_live", return_value=True),
            "email_live": patch.object(router, "email_live", return_value=True),
            "sms": patch.object(router, "send_sms", return_value={"success": True}),
            "email": patch.object(router, "send_email", return_value={"success": True}),
            "code": patch.object(router.secrets, "randbelow", return_value=123456),
            "enroll": patch("wema_vas.enrollment.enroll_verified", side_effect=AssertionError("no allocation")),
            "network": patch("requests.sessions.Session.request", side_effect=AssertionError("no live calls")),
        }
        for name, mocked in patches.items():
            setattr(self, name, mocked.start())
            self.addCleanup(mocked.stop)

    def submit(self, digits=None, kind="bvn"):
        return vas_identity.submit(self.pa, self.user, self.msisdn, kind, digits or self.raw)

    def test_no_invite_required_and_identity_claimed_only_after_ownership(self):
        self.assertEqual(self.submit(), "otp")
        self.user.refresh_from_db()
        self.assertFalse(self.user.bvn_verified)
        self.assertEqual(self.user.bvn_hash, "")
        self.assertFalse(IdentityProof.objects.exists())
        self.assertEqual(router.kyc_flow_identity_otp(self.pa, "123456")[0], "ok")
        self.user.refresh_from_db()
        self.assertTrue(self.user.bvn_verified)
        self.assertEqual(self.user.bvn_hash, hash_identifier(self.raw))
        self.assertEqual(IdentityProof.objects.get().verified_name, "Ada Eze")
        self.assertFalse(VirtualAccount.objects.exists())
        self.enroll.assert_not_called()

    def test_same_code_uses_only_record_contacts_and_bounded_timeouts(self):
        self.assertEqual(self.submit(), "otp")
        self.assertEqual(self.sms.call_args.args[0], self.record["phone"])
        self.assertEqual(self.email.call_args.args[0], self.record["email"])
        self.assertEqual(self.sms.call_args.args[1], self.email.call_args.args[2])
        self.assertEqual(self.lookup.call_args.kwargs["timeout"].total, 6)
        self.assertEqual(self.lookup.call_args.kwargs["timeout"].read_timeout, 5.5)
        self.assertEqual(self.sms.call_args.kwargs["timeout"].total, 3)
        self.assertEqual(self.email.call_args.kwargs["timeout"].total, 2)
        self.pa.refresh_from_db()
        payload = json.dumps(self.pa.payload)
        for secret in (self.raw, self.record["phone"], self.record["email"], "123456"):
            self.assertNotIn(secret, payload)

    def test_slow_private_lookup_shares_the_remaining_delivery_budget(self):
        clock = [100.0]

        def lookup(*_args, **kwargs):
            self.assertGreater(kwargs["timeout"].read_timeout, 5)
            clock[0] += 5.5
            return self.record

        def sms(*_args, **kwargs):
            self.assertEqual(kwargs["timeout"].total, 2)
            clock[0] += 1.9
            return {"success": True}

        self.lookup.side_effect, self.sms.side_effect = lookup, sms
        with patch("whatsapp.vas_identity.monotonic", side_effect=lambda: clock[0]):
            self.assertEqual(self.submit(), "otp")
            self.assertEqual(self.submit(), "otp")
        self.lookup.assert_called_once()
        self.sms.assert_called_once()
        self.email.assert_not_called()
        self.assertEqual(router.kyc_flow_identity_otp(self.pa, "123456")[0], "ok")
        self.assertTrue(IdentityProof.objects.exists())
        self.assertFalse(VirtualAccount.objects.exists())

    def test_expired_budget_prevents_lookup_and_does_not_claim_identity(self):
        with patch("whatsapp.vas_identity.monotonic", side_effect=[100.0, 107.4]):
            self.assertEqual(self.submit(), "fail")
        self.assertEqual(self.submit(), "fail")
        self.lookup.assert_not_called()
        self.sms.assert_not_called()
        self.email.assert_not_called()
        self.assertFalse(IdentityProof.objects.exists())
        self.assertFalse(VirtualAccount.objects.exists())

    def test_missing_record_email_never_falls_back_to_signup_contact(self):
        self.lookup.return_value = {**self.record, "email": ""}
        self.assertEqual(self.submit(), "otp")
        self.email.assert_not_called()
        self.sms.assert_called_once()

    def test_duplicate_and_changed_candidates_cannot_repeat_paid_lookup_or_delivery(self):
        self.assertEqual(self.submit(), "otp")
        self.assertEqual(self.submit(), "otp")
        self.assertEqual(self.submit("10987654321"), "stop")
        self.lookup.assert_called_once()
        self.sms.assert_called_once()

    def test_duplicate_during_lookup_returns_processing(self):
        def lookup(*args, **kwargs):
            self.assertEqual(self.submit(), "processing")
            self.assertEqual(self.submit("10987654321"), "stop")
            return self.record
        self.lookup.side_effect = lookup
        self.assertEqual(self.submit(), "otp")
        self.lookup.assert_called_once()
        self.sms.assert_called_once()

    def test_not_found_replay_does_not_repeat_lookup_or_spend_another_attempt(self):
        self.lookup.return_value = {"success": False, "invalid": True}
        self.assertEqual(self.submit(), "invalid")
        self.assertEqual(self.submit(), "invalid")
        self.pa.refresh_from_db()
        self.assertEqual(self.pa.payload["id_bad_attempts"], 1)
        self.lookup.assert_called_once()
        self.sms.assert_not_called()
        self.user.refresh_from_db()
        self.assertEqual(self.user.bvn_hash, "")

    def test_unavailable_lookup_is_not_an_identity_claim_or_an_invalid_attempt(self):
        self.live.return_value = False
        self.assertEqual(self.submit(), "fail")
        self.assertEqual(self.submit(), "fail")
        self.lookup.assert_not_called()
        self.sms.assert_not_called()
        self.user.refresh_from_db()
        self.assertEqual(self.user.bvn_hash, "")
        self.assertNotIn("id_bad_attempts", self.pa.payload)

    def test_provider_exception_does_not_log_sensitive_error_text(self):
        self.lookup.side_effect = RuntimeError(self.raw + " " + self.record["email"])
        with self.assertLogs("zitch.security", level="WARNING") as logs:
            self.assertEqual(self.submit(), "fail")
        self.assertNotIn(self.raw, " ".join(logs.output))
        self.assertNotIn(self.record["email"], " ".join(logs.output))
        self.sms.assert_not_called()

    def test_mock_or_missing_provider_name_cannot_arm_ownership(self):
        self.lookup.return_value = {**self.record, "mock": True}
        self.assertEqual(self.submit(), "fail")
        self.lookup.return_value = {**self.record, "first_name": "", "last_name": ""}
        self.assertEqual(self.submit("10987654321"), "fail")
        self.sms.assert_not_called()

    def test_verified_identity_and_other_owners_are_never_replaced(self):
        User.objects.filter(pk=self.user.pk).update(bvn_verified=True, bvn_hash=hash_identifier("10987654321"))
        self.assertEqual(self.submit(), "stop")
        self.lookup.assert_not_called()
        User.objects.filter(pk=self.user.pk).update(bvn_verified=False, bvn_hash="")
        User.objects.create(username="existing-owner", bvn_hash=hash_identifier(self.raw))
        self.assertEqual(self.submit(), "stop")
        self.lookup.assert_not_called()

    def test_contact_change_and_link_revocation_revoke_open_form(self):
        User.objects.filter(pk=self.user.pk).update(email="changed@example.test")
        self.assertEqual(self.submit(), "stop")
        User.objects.filter(pk=self.user.pk).update(email="signup@example.test")
        self.link.delete()
        self.assertEqual(self.submit(), "stop")
        self.lookup.assert_not_called()
        self.sms.assert_not_called()

    def test_contact_revocation_during_lookup_prevents_delivery(self):
        def lookup(*args, **kwargs):
            User.objects.filter(pk=self.user.pk).update(email_verified=False)
            return self.record
        self.lookup.side_effect = lookup
        self.assertEqual(self.submit(), "stop")
        self.sms.assert_not_called()

    def test_candidate_change_during_lookup_prevents_delivery(self):
        def lookup(*args, **kwargs):
            User.objects.filter(pk=self.user.pk).update(bvn_hash=hash_identifier("10987654321"))
            return self.record
        self.lookup.side_effect = lookup
        self.assertEqual(self.submit(), "stop")
        self.sms.assert_not_called()

    def test_rearming_does_not_adopt_changed_credentials(self):
        User.objects.filter(pk=self.user.pk).update(email_verified=False)
        self.assertFalse(vas_identity.arm(self.pa, self.user, self.msisdn))
        self.assertEqual(self.submit(), "stop")
        self.lookup.assert_not_called()

    def test_wrong_private_code_cannot_claim_candidate(self):
        self.assertEqual(self.submit(), "otp")
        self.assertEqual(router.kyc_flow_identity_otp(self.pa, "654321")[0], "retry")
        self.user.refresh_from_db()
        self.assertEqual(self.user.bvn_hash, "")
        self.assertFalse(IdentityProof.objects.exists())

    def contact_action(self):
        self.user.email_verified = False
        self.user.phone_verified = False
        self.user.save(update_fields=["email_verified", "phone_verified"])
        self.pa.payload = {"id_kind": "bvn", "vas_contacts": True}
        self.pa.state = "idle"
        self.pa.save(update_fields=["payload", "state"])

    def test_contact_binding_does_not_require_verified_contacts(self):
        self.contact_action()
        self.assertTrue(vas_identity.arm_contacts(self.pa, self.user, self.msisdn))
        self.assertTrue(vas_identity.contact_bound(self.pa, self.user))
        self.assertFalse(vas_identity.bound(self.pa, self.user))
        self.assertFalse(vas_identity.arm(self.pa, self.user, self.msisdn))

    def test_contact_binding_cannot_rearm_after_credential_change(self):
        self.contact_action()
        self.assertTrue(vas_identity.arm_contacts(self.pa, self.user, self.msisdn))
        self.user.password = "changed-password-hash"
        self.user.save(update_fields=["password"])
        self.assertFalse(vas_identity.contact_bound(self.pa, self.user))
        self.assertFalse(vas_identity.arm_contacts(self.pa, self.user, self.msisdn))

    def test_contact_binding_requires_the_original_active_link(self):
        self.contact_action()
        self.assertTrue(vas_identity.arm_contacts(self.pa, self.user, self.msisdn))
        self.link.delete()
        WhatsAppLink.objects.create(user=self.user, wa_msisdn=self.msisdn, status=WhatsAppLink.ACTIVE)
        self.assertFalse(vas_identity.contact_bound(self.pa, self.user))
        self.assertFalse(vas_identity.arm_contacts(self.pa, self.user, self.msisdn))

    def test_contact_to_identity_requires_explicit_stamp_refresh_after_proof(self):
        self.contact_action()
        self.assertTrue(vas_identity.arm_contacts(self.pa, self.user, self.msisdn))
        self.user.phone_verified = self.user.email_verified = True
        self.user.save(update_fields=["phone_verified", "email_verified"])
        self.pa.state = "email_otp"
        self.pa.save(update_fields=["state"])
        self.assertFalse(vas_identity.arm(self.pa, self.user, self.msisdn))
        # The contact proof consumer refreshes the stamp within its proof lock.
        self.pa.payload["vas_identity_credentials"] = vas_identity.credentials(self.user)
        self.pa.save(update_fields=["payload"])
        self.assertTrue(vas_identity.arm(self.pa, self.user, self.msisdn))
        self.pa.state = flows.FLOW_ID_STATE
        self.pa.save(update_fields=["state"])
        self.assertTrue(vas_identity.bound(self.pa, self.user))

    def test_expired_contact_action_cannot_issue_or_resolve_a_form(self):
        self.contact_action()
        self.assertTrue(vas_identity.arm_contacts(self.pa, self.user, self.msisdn))
        self.pa.expires_at = timezone.now() - timedelta(seconds=1)
        self.pa.save(update_fields=["expires_at"])
        self.assertFalse(vas_identity.contact_bound(self.pa, self.user))
        self.assertFalse(vas_identity.arm_contacts(self.pa, self.user, self.msisdn))
