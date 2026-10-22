from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import AccessToken, User, hash_identifier
from utility import wema
from wallet.account_recovery import active_account_recovery, prepare_account_recovery
from wallet.models import WemaFaceSession, WemaProvisioningAttempt
from wallet.services import get_or_create_wallet
from wallet.views import _account_setup_state
from whatsapp.models import AuditLog, PendingAction


FACE = {"KEYS": {"wallet": "k", "face_account": "f"}, "CHANNEL_ID": "c",
        "BASE_URL": "https://bank.example", "SIMULATION": False,
        "FACE_VERIFY_URL": "https://face.example/", "FACE_CB_MODE": "registered",
        "FACE_CALLBACK_IPS": ["9.9.9.9"], "FACE_CALLBACK_ORIGINS": ["https://face.example"],
        "CALLBACK_TOKEN": "test-token"}
ABSENT = {"success": False, "account_number": "", "account_lookup_state": "not_found", "http_status": 200}
BVN = "22222222222"


@override_settings(WEMA=FACE, API_BASE_URL="https://api.example")
class AccountRecoveryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="recovery", phone="08012340001",
            email="recovery@example.test", email_verified=True, phone_verified=True,
            bvn_verified=True, bvn_hash=hash_identifier(BVN), bvn_last4="2222", tier=1)
        self.wallet = get_or_create_wallet(self.user)
        self.old = WemaFaceSession.objects.create(user=self.user, state="old-face",
            identity_type="bvn", identity_hash=self.user.bvn_hash,
            correlation_id="old-correlation", status="verified", expires_at=timezone.now()-timedelta(days=30))
        WemaFaceSession.objects.filter(pk=self.old.pk).update(updated=timezone.now()-timedelta(days=30))
        self.token = AccessToken.issue(self.user).key
        patch = mock.patch("utility.wema.get_account_details", return_value=ABSENT)
        self.lookup = patch.start()
        self.addCleanup(patch.stop)

    def prepare(self, **extra):
        return prepare_account_recovery(self.user.pk, kind="bvn", operator_reference="incident-test", **extra)

    def start(self, bvn=BVN):
        return self.client.post("/api/kyc/face/start/",
            {"access_token": self.token, "bvn": bvn, "prefer_face": True}, content_type="application/json")

    def test_dry_run_does_not_change_proof_or_authorize_recovery(self):
        with mock.patch("utility.wema.create_wallet_with_face") as create:
            _, changed = self.prepare()
        self.assertFalse(changed)
        self.assertEqual(WemaFaceSession.objects.count(), 1)
        self.assertFalse(AuditLog.objects.filter(action="account_recovery_authorized").exists())
        self.assertEqual(self.start().status_code, 409)
        create.assert_not_called()

    def test_approval_is_audited_idempotent_and_preserves_proof(self):
        session, created = self.prepare(approve=True)
        again, repeated = self.prepare(approve=True)
        self.assertTrue(created)
        self.assertFalse(repeated)
        self.assertEqual(again.pk, session.pk)
        self.old.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(self.old.status, "verified")
        self.assertEqual(self.old.correlation_id, "old-correlation")
        self.assertTrue(self.user.bvn_verified)
        self.assertEqual(self.user.bvn_hash, hash_identifier(BVN))
        audit = AuditLog.objects.get(action="account_recovery_authorized")
        self.assertNotIn(BVN, str(audit.after))
        self.assertNotIn(session.state, str(audit.after))
        self.assertEqual(_account_setup_state(self.user, self.wallet)["account_setup_state"], "recovery_ready")

    def test_account_lookup_must_explicitly_confirm_absence(self):
        for result in ({}, {"success": False}, {**ABSENT, "mock": True},
                       {**ABSENT, "account_number": "0123456789"},
                       {"success": True, "account_lookup_state": "unknown"}):
            with self.subTest(result=result):
                self.lookup.return_value = result
                with self.assertRaises(ValueError):
                    self.prepare(approve=True)
        self.assertEqual(WemaFaceSession.objects.count(), 1)

    def test_known_bank_acceptance_or_duplicate_cannot_be_reset(self):
        for fields in ({"account_state": "awaiting_callback"},
                       {"account_state": "unknown", "account_failure_category": "existing_customer"}):
            WemaFaceSession.objects.filter(pk=self.old.pk).update(**fields)
            with self.assertRaises(ValueError):
                self.prepare(approve=True)
        self.lookup.assert_not_called()

    def test_live_or_accepted_otp_blocks_recovery(self):
        attempt = WemaProvisioningAttempt.objects.create(user=self.user, tracking_id="pending",
            identity_type="bvn", identity_hash=self.user.bvn_hash, identity_last4="2222",
            expires_at=timezone.now()+timedelta(minutes=10))
        with self.assertRaises(ValueError):
            self.prepare(approve=True)
        attempt.expires_at = timezone.now()-timedelta(days=2)
        attempt.otp_verified_at = timezone.now()-timedelta(days=3)
        attempt.save()
        with self.assertRaises(ValueError):
            self.prepare(approve=True)

    def test_face_start_reuses_exact_authorized_session_and_shortens_expiry(self):
        session, _ = self.prepare(approve=True)
        first = self.start()
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(first.json()["session"], session.state)
        session.refresh_from_db()
        deadline = session.expires_at
        self.assertLessEqual(deadline, timezone.now()+timedelta(minutes=20))
        second = self.start()
        self.assertEqual(second.json()["session"], session.state)
        session.refresh_from_db()
        self.assertEqual(session.expires_at, deadline)
        self.assertEqual(WemaFaceSession.objects.count(), 2)

    def test_wrong_identity_expiry_or_consumption_cannot_open_another_session(self):
        session, _ = self.prepare(approve=True)
        self.assertEqual(self.start("33333333333").status_code, 409)
        WemaFaceSession.objects.filter(pk=session.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.start().status_code, 409)
        with self.assertRaises(ValueError):
            self.prepare(approve=True)
        WemaFaceSession.objects.filter(pk=session.pk).update(status="verified", expires_at=timezone.now()+timedelta(hours=1))
        self.assertIsNone(active_account_recovery(self.user))
        self.assertEqual(self.start().status_code, 409)

    def test_account_arriving_during_lookup_aborts_authorization(self):
        def arriving(*args, **kwargs):
            self.wallet.account_number = "0123456789"
            self.wallet.save(update_fields=["account_number"])
            return ABSENT
        self.lookup.side_effect = arriving
        with self.assertRaises(ValueError):
            self.prepare(approve=True)
        self.assertEqual(WemaFaceSession.objects.count(), 1)

    def test_identity_changed_during_lookup_aborts_authorization(self):
        def changed(*args, **kwargs):
            User.objects.filter(pk=self.user.pk).update(bvn_hash=hash_identifier("33333333333"))
            return ABSENT
        self.lookup.side_effect = changed
        with self.assertRaises(ValueError):
            self.prepare(approve=True)
        self.assertEqual(WemaFaceSession.objects.count(), 1)

    def test_management_command_is_dry_by_default_and_redacted(self):
        output = StringIO()
        call_command("prepare_account_recovery", user_id=self.user.pk, identity_type="bvn",
                     operator_reference="incident-test", stdout=output)
        self.assertIn("recovery_authorized=False", output.getvalue())
        self.assertNotIn(BVN, output.getvalue())
        self.lookup.return_value = {}
        with self.assertRaises(CommandError):
            call_command("prepare_account_recovery", user_id=self.user.pk, identity_type="bvn",
                         operator_reference="incident-test", approve=True, stdout=output)

    def test_whatsapp_uses_same_session_and_never_sends_identity_as_text(self):
        from whatsapp import router
        session, _ = self.prepare(approve=True)
        action = PendingAction.objects.create(user=self.user, msisdn="2348012340001",
            action_type="add_account", state=router.FACE_ID_STATE, payload={"id_purpose": "account_face"},
            expires_at=timezone.now()+timedelta(minutes=20))
        with mock.patch.object(router, "send_cta_url") as send, mock.patch.object(router, "_kyc_next"):
            router._kyc_send_face_link(action, self.user, action.msisdn, "bvn", BVN)
        self.assertEqual(WemaFaceSession.objects.count(), 2)
        self.assertNotIn(BVN, send.call_args.args[1])
        self.assertFalse(send.call_args.kwargs["allow_text_fallback"])
        self.assertEqual(active_account_recovery(self.user).pk, session.pk)

    def callback(self, correlation="fresh-correlation"):
        return self.client.post("/webhooks/wema/face", {
            "success": True, "c_id": correlation, "id": BVN, "id_type": "bvn"},
            content_type="application/json", REMOTE_ADDR="8.8.8.8", HTTP_ORIGIN="https://face.example")

    @override_settings(TESTING=False, DEBUG=False, SECURE_SSL_REDIRECT=False, ALLOWED_HOSTS=["testserver"])
    def test_fresh_callback_submits_once_but_old_correlation_never_replays(self):
        session, _ = self.prepare(approve=True)
        with mock.patch("utility.wema.create_wallet_with_face", return_value={"success": True, "http_status": 200}) as create, \
                mock.patch("wallet.wema_callbacks._tell_whatsapp_face_passed"):
            self.assertEqual(self.callback("old-correlation").status_code, 200)
            create.assert_not_called()
            session.refresh_from_db()
            self.assertEqual(session.status, "pending")
            self.assertEqual(self.callback().status_code, 200)
            self.assertEqual(self.callback().status_code, 200)
            create.assert_called_once()
        session.refresh_from_db()
        self.assertEqual(session.status, "verified")
        self.assertEqual(session.account_state, "awaiting_callback")
        self.assertIsNone(active_account_recovery(self.user))

    @override_settings(TESTING=False, DEBUG=False, SECURE_SSL_REDIRECT=False, ALLOWED_HOSTS=["testserver"])
    def test_callback_rechecks_bank_and_does_not_create_when_lookup_changes(self):
        session, _ = self.prepare(approve=True)
        self.lookup.return_value = {"success": True, "account_number": "0123456789", "account_lookup_state": "found"}
        with mock.patch("utility.wema.create_wallet_with_face") as create:
            self.assertEqual(self.callback().status_code, 200)
        create.assert_not_called()
        session.refresh_from_db()
        self.assertEqual(session.account_failure_category, "recovery_lookup_unresolved")


@override_settings(WEMA=FACE)
class AccountLookupEvidenceTests(TestCase):
    def test_only_explicit_successful_no_account_response_is_absence(self):
        cases = [
            (200, {"status": True, "message": "Account Details not found.", "data": {}}, "not_found"),
            (200, {"status": True, "message": "Account Details not found.", "data": {"accountNumber": "0123456789"}}, "found"),
            (500, {"status": True, "message": "Account Details not found."}, "unknown"),
            (200, {"status": False, "message": "Account Details not found."}, "unknown"),
            (200, {"status": True, "message": "Account Details not found.", "errors": ["outage"]}, "unknown"),
            (200, {"status": True, "data": {}}, "unknown"),
            (200, {"status": True, "message": "Account Details not found.", "data": "unavailable"}, "unknown"),
            (200, {"status": True, "message": "Account Details not found.", "data": {"phoneNumber": "08012340001"}}, "not_found"),
            (200, {"status": "true", "message": "Account Details not found."}, "unknown"),
        ]
        for http_status, body, expected in cases:
            with self.subTest(body=body, http_status=http_status):
                response = mock.Mock(status_code=http_status)
                response.json.return_value = body
                with mock.patch("utility.wema._get", return_value=response):
                    self.assertEqual(wema.get_account_details("08012340001", bvn=True)["account_lookup_state"], expected)
