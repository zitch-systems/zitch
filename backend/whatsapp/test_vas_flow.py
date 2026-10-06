"""Private consent, trusted identity proof, rollout and encrypted transport."""
import base64
import hashlib
import hmac
import json
import os
from datetime import timedelta
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from accounts.models import IdentityProof, User, hash_identifier, record_identity_proof
from wallet.models import Wallet
from wema_vas.models import VirtualAccount
from wema_vas.test_enrollment import SETTINGS
from whatsapp import flows, router, vas_flow
from whatsapp.models import PendingAction, WhatsAppLink
from whatsapp.providers import _published_flow_report
from whatsapp.test_flow_publish_probe import _Resp

FLOW = {"FLOW_ID": "approved-flow", "PRIVATE_KEY": "key", "VAS_ENROLLMENT_ENABLED": True,
        "VAS_APPROVED_FLOW_ID": "approved-flow", "RESULT_SCREEN": True}
WA = {"MODE": "live", "TOKEN": "test-token", "PHONE_NUMBER_ID": "test-number",
      "BASE_URL": "https://graph.facebook.com/v26.0", "APP_SECRET": "test-app-secret"}


@override_settings(WHATSAPP=WA, WHATSAPP_FLOW=FLOW)
class VasPublicationGateTests(SimpleTestCase):
    def setUp(self):
        self.document = json.loads((Path(__file__).parent / "flow_assets" / "pin_flow.json").read_text())

    def report(self, document=None, status="PUBLISHED", flow_id="approved-flow"):
        with patch("whatsapp.providers.requests.get", side_effect=[
            _Resp({"id": flow_id, "status": status, "validation_errors": []}),
            _Resp({"data": [{"asset_type": "FLOW_JSON", "download_url": "https://example.test/asset"}]}),
            _Resp(document or self.document),
        ]):
            return _published_flow_report()

    def test_exact_published_contract_and_approved_id_are_required(self):
        report = self.report()
        with patch("whatsapp.providers.published_flow_report", return_value=report):
            self.assertTrue(vas_flow.ready())
        for modified in ({**report, "status": "draft"}, {**report, "flow_id": "old-flow"},
                         {**report, "contract_matches": False}, {**report, "validation_errors": ["invalid"]}):
            with patch("whatsapp.providers.published_flow_report", return_value=modified):
                self.assertFalse(vas_flow.ready())

    def test_equal_screen_properties_cannot_hide_changed_consent_or_routes(self):
        changed = deepcopy(self.document)
        setup = next(s for s in changed["screens"] if s["id"] == vas_flow.SETUP)
        next(c for c in setup["layout"]["children"] if c["type"] == "TextHeading")["text"] = "Unapproved copy"
        report = self.report(changed)
        self.assertFalse(report["contract_matches"])
        self.assertFalse(report["stale"])

    @override_settings(WHATSAPP_FLOW={**FLOW, "VAS_ENROLLMENT_ENABLED": False})
    def test_disabled_vas_does_not_break_legacy_screen_readiness(self):
        old = deepcopy(self.document)
        old["screens"] = [s for s in old["screens"] if not s["id"].startswith("VAS_")]
        report = self.report(old)
        self.assertFalse(report["stale"])
        with patch("whatsapp.providers.published_flow_report") as report_call:
            self.assertFalse(vas_flow.ready())
        report_call.assert_not_called()

    def test_consent_is_literal_true_and_raw_fields_never_complete_a_flow(self):
        setup = next(s for s in self.document["screens"] if s["id"] == vas_flow.SETUP)
        form = next(c for c in setup["layout"]["children"] if c["type"] == "Form")
        footer = next(c for c in form["children"] if c["type"] == "Footer")
        self.assertIs(footer["on-click-action"]["payload"]["consent"], True)
        self.assertIn("agree", footer["label"])
        for screen in self.document["screens"]:
            if screen["id"].startswith("VAS_"):
                self.assertNotIn('"complete"', json.dumps(screen))
        inbound = {target for targets in self.document["routing_model"].values() for target in targets}
        self.assertNotIn(vas_flow.SETUP, inbound)


@override_settings(WEMA_VAS=SETTINGS, BANK_ACCOUNT_PROVIDER="wema_vas", WHATSAPP=WA,
                   WHATSAPP_FLOW=FLOW, RATELIMIT_ENABLE=False)
class VasFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.raw = "12345678901"
        self.msisdn = "2348012345678"
        self.user = User.objects.create(username="vas-private", phone="+" + self.msisdn,
            first_name="Ada", last_name="Eze", phone_verified=True, bvn_verified=True,
            bvn_hash=hash_identifier(self.raw))
        Wallet.objects.create(user=self.user)
        self.link = WhatsAppLink.objects.create(user=self.user, wa_msisdn=self.msisdn, status=WhatsAppLink.ACTIVE)
        self.proof = record_identity_proof(self.user, "bvn", self.raw,
            source=IdentityProof.IDENTITY_PROVIDER_OTP, verified_name="Ada Eze")
        self.ready = patch("whatsapp.vas_flow.ready", return_value=True)
        self.ready.start()
        self.addCleanup(self.ready.stop)

    def start(self):
        with patch("whatsapp.providers.send_flow", return_value={"success": True}) as send:
            vas_flow.start(self.user, self.msisdn)
        return send.call_args.args[1]

    def exchange(self, token, data, screen, action="data_exchange"):
        return flows.handle_flow_request({"flow_token": token, "action": action, "screen": screen, "data": data})

    def consent(self, token):
        response = self.exchange(token, {"consent": True, "identity_type": "bvn"}, vas_flow.SETUP)
        self.assertEqual(response["screen"], vas_flow.IDENTITY)

    def test_verified_identity_enrolls_once_and_duplicate_returns_same_outcome(self):
        token = self.start()
        self.consent(token)
        response = self.exchange(token, {"number": self.raw}, vas_flow.IDENTITY)
        self.assertEqual(response["data"]["status"], "Successful")
        self.assertEqual(VirtualAccount.objects.count(), 1)
        account = VirtualAccount.objects.get()
        self.assertEqual(account.display_name, "Zitch/Ada Eze")
        self.assertIn(":whatsapp:", account.consent_reference)
        self.assertEqual(self.exchange(token, {"number": self.raw}, vas_flow.IDENTITY), response)
        self.assertEqual(VirtualAccount.objects.count(), 1)
        self.assertNotIn(self.raw, json.dumps(PendingAction.objects.get().payload))
        closed = self.exchange(token, {"close": True}, "RESULT")
        self.assertEqual(closed["data"]["extension_message_response"]["params"], {"flow_token": token})

    def test_consent_refusal_and_truthy_non_boolean_never_start_identity_lookup(self):
        for consent in (False, None, "true", 1):
            cache.clear()
            token = self.start()
            with patch("utility.providers.prembly_verify_bvn") as lookup:
                response = self.exchange(token, {"consent": consent, "identity_type": "bvn"}, vas_flow.SETUP)
            lookup.assert_not_called()
            self.assertIn("cancelled", response["data"]["message"])
        self.assertFalse(VirtualAccount.objects.exists())

    def test_token_tampering_and_user_reassignment_are_rejected(self):
        token = self.start()
        self.consent(token)
        other = User.objects.create(username="other")
        for changed in (token[:-1] + ("A" if token[-1] != "A" else "B"), "id" + token[2:]):
            self.exchange(changed, {"number": self.raw}, vas_flow.IDENTITY)
        PendingAction.objects.update(user=other)
        self.exchange(token, {"number": self.raw}, vas_flow.IDENTITY)
        self.assertFalse(VirtualAccount.objects.exists())

    def test_link_revocation_or_phone_change_revokes_the_open_form(self):
        token = self.start()
        self.consent(token)
        self.link.delete()
        self.exchange(token, {"number": self.raw}, vas_flow.IDENTITY)
        self.assertFalse(VirtualAccount.objects.exists())
        self.assertNotIn(self.raw, json.dumps(PendingAction.objects.get().payload))

    def test_pilot_removal_between_consent_and_entry_blocks_enrollment(self):
        token = self.start()
        self.consent(token)
        with override_settings(WEMA_VAS={**SETTINGS, "RELEASE_PHASE": "pilot", "PILOT_USER_IDS": []}):
            response = self.exchange(token, {"number": self.raw}, vas_flow.IDENTITY)
        self.assertEqual(response["data"]["status"], "Not completed")
        self.assertFalse(VirtualAccount.objects.exists())

    def test_no_eligible_pilot_means_no_form_no_app_redirect(self):
        with override_settings(WEMA_VAS={**SETTINGS, "RELEASE_PHASE": "pilot", "PILOT_USER_IDS": []}), \
                patch("whatsapp.providers.send_flow") as send, patch.object(router, "send_cta_url") as redirect, \
                patch.object(router, "reply") as reply:
            vas_flow.start(self.user, self.msisdn)
        send.assert_not_called()
        redirect.assert_not_called()
        self.assertNotIn("app", reply.call_args.args[1].lower())
        self.assertFalse(PendingAction.objects.exists())

    def test_start_preserves_executing_money_and_submitted_web_verification(self):
        for action_type, state in (("transfer", router.EXECUTING_STATE),
                                   ("verification_web", "web_processing"),
                                   ("verification_web", "web_review")):
            with self.subTest(state=state):
                cache.clear()
                pending = PendingAction.objects.create(user=self.user, msisdn=self.msisdn,
                    action_type=action_type, state=state, payload={},
                    expires_at=timezone.now() + timedelta(minutes=15))
                with patch("whatsapp.providers.send_flow") as send, patch.object(router, "reply"):
                    vas_flow.start(self.user, self.msisdn)
                send.assert_not_called()
                self.assertTrue(PendingAction.objects.filter(pk=pending.pk).exists())
                self.assertFalse(PendingAction.objects.filter(action_type="vas_enroll").exists())
                pending.delete()

    def test_typed_chat_identity_is_never_processed_or_forwarded_to_ai(self):
        token = self.start()
        self.consent(token)
        with patch.object(router, "reply") as reply, patch.object(router, "_account_submit_identity") as legacy, \
                patch.object(router, "handle_inbound") as reroute:
            router._advance(PendingAction.objects.get(), self.user, self.msisdn, self.raw)
        legacy.assert_not_called()
        reroute.assert_not_called()
        self.assertNotIn(self.raw, reply.call_args.args[1])
        self.assertTrue(router.is_awaiting_bvn(self.msisdn))
        self.assertFalse(VirtualAccount.objects.exists())

    def test_typed_identity_is_removed_before_the_durable_inbound_queue(self):
        from whatsapp.jobs import _decrypt
        from whatsapp.models import WaMessageLog
        from whatsapp.views import _process
        self.start()
        with patch("whatsapp.jobs.process_inbound_message"):
            _process({"id": "private-entry-message", "from": self.msisdn,
                      "type": "text", "text": {"body": "My BVN is " + self.raw}})
        row = WaMessageLog.objects.get(wa_message_id="private-entry-message")
        payload = _decrypt(row.processing_payload)
        self.assertTrue(payload["vas_private_input"])
        self.assertEqual(payload["body"], "")
        self.assertNotIn(self.raw, json.dumps(payload))
        self.assertNotIn(self.raw, row.text)

    def test_identity_photo_or_voice_never_reaches_media_processing(self):
        from whatsapp.jobs import _decrypt, process_inbound_message
        from whatsapp.models import WaMessageLog
        from whatsapp.views import _process
        self.start()
        for kind in ("image", "audio"):
            with self.subTest(kind=kind):
                message_id = "private-media-" + kind
                with patch("whatsapp.jobs.process_inbound_message"):
                    _process({"id": message_id, "from": self.msisdn, "type": kind,
                        kind: {"id": "private-identity-media", "caption": self.raw}})
                row = WaMessageLog.objects.get(wa_message_id=message_id)
                payload = _decrypt(row.processing_payload)
                self.assertTrue(payload["vas_private_input"])
                self.assertEqual(payload["media_id"], "")
                self.assertEqual(payload["media_caption"], "")
                with patch("whatsapp.media.interpret") as interpret, patch("whatsapp.jobs.reply") as reply:
                    self.assertEqual(process_inbound_message(row.pk), "processed")
                interpret.assert_not_called()
                self.assertNotIn(self.raw, reply.call_args.args[1])

    def provider_code(self, token):
        IdentityProof.objects.filter(user=self.user).delete()
        with patch("utility.providers._prembly_identity_live", return_value=True), \
                patch("utility.providers.prembly_verify_bvn", return_value={"success": True, "first_name": "Ada", "last_name": "Eze", "phone": "08077778888"}), \
                patch.object(router, "sms_live", return_value=True), \
                patch.object(router, "send_sms", return_value={"success": True}) as sms:
            response = self.exchange(token, {"number": self.raw}, vas_flow.IDENTITY)
        self.assertEqual(response["screen"], vas_flow.CODE)
        self.assertEqual(sms.call_args.args[0], "08077778888")
        self.assertFalse(VirtualAccount.objects.exists())
        self.assertFalse(IdentityProof.objects.exists())
        self.assertNotIn(self.raw, json.dumps(PendingAction.objects.get().payload))
        return sms.call_args.args[1].split("Zitch: ")[1][:6]

    def test_queued_media_is_scrubbed_if_private_setup_opens_before_worker_runs(self):
        from whatsapp.jobs import _decrypt, process_inbound_message
        from whatsapp.models import WaMessageLog
        from whatsapp.views import _process
        with patch("whatsapp.jobs.process_inbound_message"):
            _process({"id": "earlier-identity-photo", "from": self.msisdn, "type": "image",
                      "image": {"id": "private-identity-media", "caption": self.raw}})
        row = WaMessageLog.objects.get(wa_message_id="earlier-identity-photo")
        self.assertEqual(_decrypt(row.processing_payload)["media_id"], "private-identity-media")
        self.start()
        with patch("whatsapp.media.interpret") as interpret, \
                patch("whatsapp.jobs.reply", side_effect=RuntimeError("transport unavailable")):
            self.assertEqual(process_inbound_message(row.pk), "retry")
        interpret.assert_not_called()
        row.refresh_from_db()
        self.assertEqual(_decrypt(row.processing_payload), {"vas_private_input": True})
        self.assertEqual(row.text, "[private setup input]")

    def unverified(self):
        IdentityProof.objects.filter(user=self.user).delete()
        User.objects.filter(pk=self.user.pk).update(bvn_verified=False, bvn_hash="", bvn_last4="")
        self.user.refresh_from_db()

    def test_new_candidate_is_not_claimed_until_sms_ownership_succeeds(self):
        self.unverified()
        token = self.start()
        self.consent(token)
        code = self.provider_code(token)
        self.user.refresh_from_db()
        self.assertEqual(self.user.bvn_hash, "")
        self.assertFalse(self.user.bvn_verified)
        self.exchange(token, {"number": code}, vas_flow.CODE)
        self.user.refresh_from_db()
        self.assertEqual(self.user.bvn_hash, hash_identifier(self.raw))
        self.assertTrue(self.user.bvn_verified)
        self.assertEqual(IdentityProof.objects.get().verified_name, "Ada Eze")

    def test_candidate_cannot_replace_an_identity_verified_while_sms_is_pending(self):
        self.unverified()
        previous = "11111111111"
        User.objects.filter(pk=self.user.pk).update(bvn_hash=hash_identifier(previous))
        self.user.refresh_from_db()
        token = self.start()
        self.consent(token)
        code = self.provider_code(token)
        User.objects.filter(pk=self.user.pk).update(bvn_verified=True)
        record_identity_proof(self.user, "bvn", previous,
            source=IdentityProof.IDENTITY_PROVIDER_OTP, verified_name="Ada Eze")
        response = self.exchange(token, {"number": code}, vas_flow.CODE)
        self.assertEqual(response["data"]["status"], "Not completed")
        self.user.refresh_from_db()
        self.assertEqual(self.user.bvn_hash, hash_identifier(previous))
        self.assertTrue(self.user.bvn_verified)
        self.assertFalse(IdentityProof.objects.filter(identity_hash=hash_identifier(self.raw)).exists())

    def test_private_exchange_bounds_provider_and_sms_calls_without_graph_probe(self):
        self.unverified()
        token = self.start()
        self.consent(token)
        with patch("utility.providers._prembly_identity_live", return_value=True), \
                patch("utility.providers.prembly_verify_bvn", return_value={"success": True,
                    "first_name": "Ada", "last_name": "Eze", "phone": "08077778888"}) as lookup, \
                patch.object(router, "sms_live", return_value=True), \
                patch.object(router, "send_sms", return_value={"success": True}) as sms, \
                patch("whatsapp.providers.published_flow_report") as graph:
            response = self.exchange(token, {"number": self.raw}, vas_flow.IDENTITY)
        self.assertEqual(response["screen"], vas_flow.CODE)
        graph.assert_not_called()
        for call in (lookup, sms):
            budget = call.call_args.kwargs["timeout"]
            self.assertEqual(budget.total, 3)
            self.assertEqual(budget.connect_timeout, 1)
            self.assertEqual(budget.read_timeout, 2)
        self.assertIsNot(lookup.call_args.kwargs["timeout"], sms.call_args.kwargs["timeout"])

    def test_mock_sms_delivery_never_creates_an_ownership_challenge(self):
        self.unverified()
        token = self.start()
        self.consent(token)
        with patch("utility.providers._prembly_identity_live", return_value=True), \
                patch("utility.providers.prembly_verify_bvn", return_value={"success": True,
                    "first_name": "Ada", "last_name": "Eze", "phone": "08077778888"}), \
                patch.object(router, "sms_live", return_value=True), \
                patch.object(router, "send_sms", return_value={"success": True, "mock": True}):
            response = self.exchange(token, {"number": self.raw}, vas_flow.IDENTITY)
        self.assertEqual(response["data"]["status"], "Not completed")
        self.assertNotIn("id_otp_hash", PendingAction.objects.get().payload)
        self.user.refresh_from_db()
        self.assertEqual(self.user.bvn_hash, "")

    def test_abandoned_or_expired_candidate_does_not_reserve_an_identity(self):
        from accounts.views import _identity_owned_by_another_user
        other = User.objects.create(username="other-candidate")
        self.unverified()
        for expiry in (False, True):
            cache.clear()
            token = self.start()
            self.consent(token)
            code = self.provider_code(token)
            if expiry:
                PendingAction.objects.filter(action_type="vas_enroll").update(expires_at=timezone.now() - timedelta(seconds=1))
                self.exchange(token, {"number": code}, vas_flow.CODE)
            else:
                router._clear_actions(self.msisdn)
            self.assertFalse(_identity_owned_by_another_user(other, "bvn", self.raw))
            self.assertFalse(IdentityProof.objects.exists())

    def test_provider_exception_does_not_reserve_unverified_candidate(self):
        from accounts.views import _identity_owned_by_another_user
        self.unverified()
        token = self.start()
        self.consent(token)
        with patch("utility.providers._prembly_identity_live", return_value=True), \
                patch("utility.providers.prembly_verify_bvn", side_effect=RuntimeError("provider failed")):
            self.exchange(token, {"number": self.raw}, vas_flow.IDENTITY)
        self.user.refresh_from_db()
        self.assertEqual(self.user.bvn_hash, "")
        other = User.objects.create(username="after-error")
        self.assertFalse(_identity_owned_by_another_user(other, "bvn", self.raw))

    def test_otp_success_rechecks_identity_claim_by_another_user(self):
        self.unverified()
        token = self.start()
        self.consent(token)
        code = self.provider_code(token)
        other = User.objects.create(username="prior-claim", bvn_hash=hash_identifier(self.raw), bvn_verified=True)
        response = self.exchange(token, {"number": code}, vas_flow.CODE)
        self.user.refresh_from_db()
        self.assertEqual(response["data"]["status"], "Not completed")
        self.assertEqual(self.user.bvn_hash, "")
        self.assertFalse(self.user.bvn_verified)
        self.assertFalse(IdentityProof.objects.filter(user=self.user).exists())
        self.assertEqual(User.objects.get(pk=other.pk).bvn_hash, hash_identifier(self.raw))

    def test_intermediate_transport_retries_are_monotonic_without_repeating_work(self):
        token = self.start()
        self.consent(token)
        self.assertEqual(self.exchange(token, {"consent": True, "identity_type": "bvn"}, vas_flow.SETUP)["screen"], vas_flow.IDENTITY)
        IdentityProof.objects.filter(user=self.user).delete()
        concurrent = []
        def lookup(*args, **kwargs):
            concurrent.append(self.exchange(token, {"number": self.raw}, vas_flow.IDENTITY))
            return {"success": True, "first_name": "Ada", "last_name": "Eze", "phone": "08077778888"}
        with patch("utility.providers._prembly_identity_live", return_value=True), \
                patch("utility.providers.prembly_verify_bvn", side_effect=lookup) as verify, \
                patch.object(router, "sms_live", return_value=True), \
                patch.object(router, "send_sms", return_value={"success": True}) as sms:
            response = self.exchange(token, {"number": self.raw}, vas_flow.IDENTITY)
            repeated = self.exchange(token, {"number": self.raw}, vas_flow.IDENTITY)
        self.assertEqual(concurrent[0]["screen"], vas_flow.IDENTITY)
        self.assertEqual(repeated, response)
        verify.assert_called_once()
        sms.assert_called_once()
        code = sms.call_args.args[1].split("Zitch: ")[1][:6]
        advanced = self.exchange(token, {"number": code}, vas_flow.CODE)
        self.assertEqual(advanced["screen"], vas_flow.REENTRY)
        self.assertEqual(self.exchange(token, {"number": code}, vas_flow.CODE), advanced)
        self.assertEqual(IdentityProof.objects.count(), 1)
        self.assertNotIn(self.raw, json.dumps(PendingAction.objects.get().payload))

    def test_identity_replay_with_changed_number_cannot_reuse_the_challenge(self):
        token = self.start()
        self.consent(token)
        self.provider_code(token)
        response = self.exchange(token, {"number": "99999999999"}, vas_flow.IDENTITY)
        self.assertEqual(response["data"]["status"], "Not completed")
        self.assertFalse(IdentityProof.objects.exists())

    def test_missing_proof_uses_provider_phone_sms_then_fresh_identity_entry(self):
        token = self.start()
        self.consent(token)
        code = self.provider_code(token)
        response = self.exchange(token, {"number": code}, vas_flow.CODE)
        self.assertEqual(response["screen"], vas_flow.REENTRY)
        proof = IdentityProof.objects.get()
        self.assertEqual(proof.verified_name, "Ada Eze")
        self.assertFalse(VirtualAccount.objects.exists())
        response = self.exchange(token, {"number": self.raw}, vas_flow.REENTRY)
        self.assertEqual(response["data"]["status"], "Successful")
        self.assertEqual(VirtualAccount.objects.get().display_name, "Zitch/Ada Eze")

    def test_wrong_code_does_not_issue_proof_and_has_fresh_bounded_retry(self):
        token = self.start()
        self.consent(token)
        code = self.provider_code(token)
        wrong = "000000" if code != "000000" else "111111"
        self.assertEqual(self.exchange(token, {"number": wrong}, vas_flow.CODE)["screen"], vas_flow.CODE_RETRY)
        response = self.exchange(token, {"number": wrong}, vas_flow.CODE_RETRY)
        self.assertEqual(response["screen"], "RESULT")
        self.assertFalse(IdentityProof.objects.exists())
        self.assertFalse(VirtualAccount.objects.exists())

    def test_unavailable_lookup_cannot_mock_pass_or_use_partnership(self):
        self.proof.delete()
        token = self.start()
        self.consent(token)
        with patch("utility.providers._prembly_identity_live", return_value=False), \
                patch("utility.providers.prembly_verify_bvn") as lookup, patch.object(router, "send_sms") as sms:
            response = self.exchange(token, {"number": self.raw}, vas_flow.IDENTITY)
        lookup.assert_not_called()
        sms.assert_not_called()
        self.assertEqual(response["data"]["status"], "Not completed")
        self.assertFalse(IdentityProof.objects.exists())

    def test_provider_exception_cannot_disclose_raw_identifier(self):
        self.proof.delete()
        token = self.start()
        self.consent(token)
        with patch("utility.providers._prembly_identity_live", return_value=True), \
                patch("utility.providers.prembly_verify_bvn", side_effect=RuntimeError("bad " + self.raw)), \
                self.assertLogs("zitch.security", level="WARNING") as logs:
            response = self.exchange(token, {"number": self.raw}, vas_flow.IDENTITY)
        self.assertNotIn(self.raw, json.dumps(response))
        self.assertNotIn(self.raw, json.dumps(PendingAction.objects.get().payload))
        self.assertNotIn(self.raw, str(logs.output))

    def test_signed_encrypted_endpoint_enrolls_and_returns_only_encrypted_outcome(self):
        token = self.start()
        self.consent(token)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        aes, iv = os.urandom(16), os.urandom(16)
        payload = {"flow_token": token, "action": "data_exchange", "screen": vas_flow.IDENTITY, "data": {"number": self.raw}}
        encrypted = AESGCM(aes).encrypt(iv, json.dumps(payload).encode(), None)
        enc_key = key.public_key().encrypt(aes, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
        body = json.dumps({"encrypted_flow_data": base64.b64encode(encrypted).decode(),
            "encrypted_aes_key": base64.b64encode(enc_key).decode(), "initial_vector": base64.b64encode(iv).decode()})
        signature = "sha256=" + hmac.new(WA["APP_SECRET"].encode(), body.encode(), hashlib.sha256).hexdigest()
        with override_settings(WHATSAPP_FLOW={**FLOW, "PRIVATE_KEY": private}):
            response = self.client.post("/webhooks/whatsapp/flow", body, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=signature)
        self.assertEqual(response.status_code, 200)
        clear = AESGCM(aes).decrypt(bytes(b ^ 255 for b in iv), base64.b64decode(response.content), None)
        self.assertEqual(json.loads(clear)["data"]["status"], "Successful")
        self.assertNotIn(self.raw, clear.decode())
        self.assertEqual(VirtualAccount.objects.count(), 1)
