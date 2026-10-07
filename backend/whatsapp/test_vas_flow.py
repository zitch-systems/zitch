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
from whatsapp import flows, router, vas_capsule, vas_flow
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

    def test_setup_contract_declares_and_renders_every_mode_specific_field(self):
        setup = next(s for s in self.document["screens"] if s["id"] == vas_flow.SETUP)
        self.assertEqual(set(setup["data"]), {"title", "purpose", "consent_text", "error"})
        # Examples belong to the immutable published contract. Actual consent
        # is server-supplied data, bound to its current version in the session.
        self.assertEqual(set(setup["data"]), set(vas_flow._setup_data("validation")))
        text_nodes = {child["text"] for child in setup["layout"]["children"]
                      if child["type"] in {"TextHeading", "TextBody"}}
        self.assertEqual(text_nodes, {"${data.title}", "${data.purpose}",
                                      "${data.consent_text}", "${data.error}"})
        for mode in ("live", "validation"):
            with self.subTest(mode=mode):
                data = vas_flow._setup_data(mode)
                self.assertEqual(set(data), set(setup["data"]))
                self.assertIn("Prembly", data["purpose"])
                self.assertIn("encrypted form", data["purpose"])
                self.assertIn("Wema Bank", data["purpose"])
                self.assertIn("SMS", data["consent_text"])
                self.assertIn("registered email", data["consent_text"])
                self.assertIn("Close this form to decline", data["consent_text"])

    def test_validation_consent_is_concise_and_distinct_from_live_funding_consent(self):
        validation = vas_flow._setup_data("validation")
        live = vas_flow._setup_data("live")
        self.assertEqual(validation["title"], "Set up your Zitch account")
        self.assertIn("bank integration validation", validation["purpose"])
        self.assertIn("Funding becomes available after account activation", validation["purpose"])
        self.assertIn("for bank integration validation", validation["consent_text"])
        self.assertNotIn("711", json.dumps(validation))
        self.assertNotIn("TEST ONLY", json.dumps(validation))
        self.assertEqual(live["title"], "Set up your Zitch account")
        self.assertIn("operate your funding account", live["purpose"])
        self.assertNotIn("711", json.dumps(live))
        with self.assertRaises(ValueError):
            vas_flow._setup_data("unknown")

    def test_published_code_routes_support_single_entry_without_contract_change(self):
        for screen in (vas_flow.CODE, vas_flow.CODE_RETRY):
            self.assertIn("RESULT", self.document["routing_model"][screen])


@override_settings(WEMA_VAS=SETTINGS, BANK_ACCOUNT_PROVIDER="wema_vas", WHATSAPP=WA,
                   WHATSAPP_FLOW=FLOW, RATELIMIT_ENABLE=False)
class VasFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.raw = "12345678901"
        self.msisdn = "2348012345678"
        self.user = User.objects.create(username="vas-private", phone="+" + self.msisdn,
            email="signup@example.test", email_verified=True,
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
        self.sent_flow = send.call_args.kwargs
        return send.call_args.args[1]

    def mode_settings(self, mode):
        if mode == "live":
            return SETTINGS
        return {**SETTINGS, "MODE": "validation", "PREFIX": "711", "RELEASE_PHASE": "closed",
                "ENABLE_ENROLLMENT": False, "ENABLE_VALIDATION_ENROLLMENT": True,
                "VALIDATION_USER_IDS": [self.user.pk]}

    def exchange(self, token, data, screen, action="data_exchange"):
        return flows.handle_flow_request({"flow_token": token, "action": action, "screen": screen, "data": data})

    def consent(self, token, kind="bvn"):
        response = self.exchange(token, {"consent": True, "identity_type": kind}, vas_flow.SETUP)
        self.assertEqual(response["screen"], vas_flow.IDENTITY)

    def test_open_and_init_use_the_same_signed_mode_specific_consent(self):
        for mode in ("validation", "live"):
            with self.subTest(mode=mode), override_settings(WEMA_VAS=self.mode_settings(mode)):
                cache.clear()
                token = self.start()
                initial = self.exchange(token, {}, vas_flow.SETUP, action="INIT")
                pa = PendingAction.objects.get(action_type="vas_enroll")
                self.assertEqual(pa.payload["mode"], mode)
                self.assertEqual(pa.payload["consent_version"], vas_flow.consent_version(mode))
                self.assertEqual(self.sent_flow["screen_data"], vas_flow._setup_data(mode))
                self.assertEqual(initial, {"screen": vas_flow.SETUP, "data": self.sent_flow["screen_data"]})
                self.assertEqual(self.sent_flow["header"], "Set up your Zitch account")
                self.assertIn("privately", self.sent_flow["body"])
                self.assertNotIn("test", self.sent_flow["body"].lower())

    def test_mode_change_before_or_after_consent_closes_the_bound_session(self):
        for initial_mode, changed_mode in (("validation", "live"), ("live", "validation")):
            for accepted in (False, True):
                with self.subTest(mode=initial_mode, accepted=accepted):
                    cache.clear()
                    with override_settings(WEMA_VAS=self.mode_settings(initial_mode)):
                        token = self.start()
                        if accepted:
                            self.consent(token)
                    with override_settings(WEMA_VAS=self.mode_settings(changed_mode)), \
                            patch("whatsapp.vas_flow.enroll_customer") as enroll, \
                            patch("utility.providers.prembly_verify_bvn") as lookup:
                        response = self.exchange(token, {"number": self.raw} if accepted else {
                            "consent": True, "identity_type": "bvn"},
                            vas_flow.IDENTITY if accepted else vas_flow.SETUP)
                    self.assertEqual(response, flows._close_flow(token))
                    enroll.assert_not_called()
                    lookup.assert_not_called()
                    self.assertFalse(VirtualAccount.objects.exists())

    def test_consent_version_change_revokes_an_open_session(self):
        token = self.start()
        self.consent(token)
        with patch("whatsapp.vas_flow.consent_version", return_value="updated-consent"), \
                patch("whatsapp.vas_flow.enroll_customer") as enroll:
            response = self.exchange(token, {"number": self.raw}, vas_flow.IDENTITY)
        self.assertEqual(response, flows._close_flow(token))
        enroll.assert_not_called()

    def test_stored_mode_and_consent_version_are_signed(self):
        for key, changed in (("mode", "validation"), ("consent_version", "changed-consent")):
            with self.subTest(key=key):
                cache.clear()
                token = self.start()
                pa = PendingAction.objects.get(action_type="vas_enroll")
                pa.payload[key] = changed
                pa.save(update_fields=["payload"])
                self.assertIsNone(vas_flow._resolve(token))

    def test_verified_identity_enrolls_once_and_duplicate_returns_same_outcome(self):
        token = self.start()
        self.consent(token)
        response = self.exchange(token, {"number": self.raw}, vas_flow.IDENTITY)
        self.assertEqual(response["data"]["status"], "Successful")
        self.assertEqual(VirtualAccount.objects.count(), 1)
        account = VirtualAccount.objects.get()
        self.assertEqual(account.display_name, "Zitch/Ada Eze")
        self.assertIn(":whatsapp:", account.consent_reference)
        self.assertTrue(account.consent_reference.startswith(vas_flow.consent_version("live") + ":"))
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

    def provider_code(self, token, kind="bvn"):
        IdentityProof.objects.filter(user=self.user).delete()
        with patch("utility.providers._prembly_identity_live", return_value=True), \
                patch("utility.providers.prembly_verify_" + kind, return_value={"success": True, "first_name": "Ada", "last_name": "Eze", "phone": "08077778888"}), \
                patch.object(router, "sms_live", return_value=True), \
                patch.object(router, "send_sms", return_value={"success": True}) as sms:
            response = self.exchange(token, {"number": self.raw}, vas_flow.IDENTITY)
        self.assertEqual(response["screen"], vas_flow.CODE)
        self.assertEqual(sms.call_args.args[0], "08077778888")
        self.assertFalse(VirtualAccount.objects.exists())
        self.assertFalse(IdentityProof.objects.exists())
        self.assertNotIn(self.raw, json.dumps(PendingAction.objects.get().payload))
        return sms.call_args.args[1].split("Zitch: ")[1][:6]

    def legacy_reentry(self, token, code):
        """Model an already displayed screen from before the one-entry release."""
        pa = PendingAction.objects.get(action_type="vas_enroll")
        outcome, _message = router.kyc_flow_identity_otp(pa, code)
        self.assertEqual(outcome, "ok")
        pa.refresh_from_db()
        vas_capsule.discard(pa)
        pa.payload.pop(vas_capsule.FIELD, None)
        pa.payload.update({"vas_step": "reentry", "screen": vas_flow.REENTRY})
        pa.save(update_fields=["payload"])

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
        lookup_budget = lookup.call_args.kwargs["timeout"]
        self.assertEqual(lookup_budget.total, 6)
        self.assertEqual(lookup_budget.connect_timeout, 1)
        self.assertEqual(lookup_budget.read_timeout, 5.5)
        sms_budget = sms.call_args.kwargs["timeout"]
        self.assertEqual(sms_budget.total, 3)
        self.assertEqual(sms_budget.connect_timeout, 1)
        self.assertEqual(sms_budget.read_timeout, 2)
        self.assertIsNot(lookup.call_args.kwargs["timeout"], sms.call_args.kwargs["timeout"])

    def test_slow_lookup_can_succeed_and_delivery_uses_remaining_exchange_budget(self):
        self.unverified()
        token = self.start()
        self.consent(token)
        clock = [100.0]

        def lookup(*_args, **kwargs):
            # A legitimate response that exceeded the old two-second read limit.
            self.assertGreater(kwargs["timeout"].read_timeout, 5)
            clock[0] += 5.5
            return {"success": True, "first_name": "Ada", "last_name": "Eze",
                    "phone": "08077778888", "email": "holder@record.example"}

        def sms(*_args, **kwargs):
            self.assertEqual(kwargs["timeout"].total, 2)
            clock[0] += 1.5
            return {"success": True}

        with patch("whatsapp.vas_identity.monotonic", side_effect=lambda: clock[0]), \
                patch("utility.providers._prembly_identity_live", return_value=True), \
                patch("utility.providers.prembly_verify_bvn", side_effect=lookup) as provider, \
                patch.object(router, "sms_live", return_value=True), \
                patch.object(router, "email_live", return_value=True), \
                patch.object(router, "send_sms", side_effect=sms) as send_sms, \
                patch.object(router, "send_email", return_value={"success": True}) as email:
            response = self.exchange(token, {"number": self.raw}, vas_flow.IDENTITY)
            replay = self.exchange(token, {"number": self.raw}, vas_flow.IDENTITY)
        self.assertEqual(response["screen"], vas_flow.CODE)
        self.assertEqual(replay, response)
        self.assertEqual(email.call_args.kwargs["timeout"].total, 0.5)
        self.assertEqual(email.call_args.kwargs["timeout"].connect_timeout, 0.5)
        self.assertEqual(email.call_args.kwargs["timeout"].read_timeout, 0.5)
        provider.assert_called_once()
        send_sms.assert_called_once()
        email.assert_called_once()
        self.assertFalse(IdentityProof.objects.exists())
        self.assertFalse(VirtualAccount.objects.exists())

    def test_exhausted_lookup_budget_never_sends_an_ownership_code_or_retries(self):
        self.unverified()
        token = self.start()
        self.consent(token)
        clock = [100.0]

        def lookup(*_args, **_kwargs):
            # Requests cannot enforce a hard wall clock across all body reads.
            clock[0] += 7.4
            return {"success": True, "first_name": "Ada", "last_name": "Eze",
                    "phone": "08077778888", "email": "holder@record.example"}

        with patch("whatsapp.vas_identity.monotonic", side_effect=lambda: clock[0]), \
                patch("utility.providers._prembly_identity_live", return_value=True), \
                patch("utility.providers.prembly_verify_bvn", side_effect=lookup) as provider, \
                patch.object(router, "sms_live", return_value=True), \
                patch.object(router, "send_sms") as sms, \
                patch.object(router, "send_email") as email, \
                self.assertLogs("whatsapp", level="WARNING") as logs:
            response = self.exchange(token, {"number": self.raw}, vas_flow.IDENTITY)
            replay = self.exchange(token, {"number": self.raw}, vas_flow.IDENTITY)
        self.assertEqual(response["data"]["status"], "Not completed")
        self.assertEqual(replay, response)
        self.assertIn("category=sms_budget_exhausted", " ".join(logs.output))
        for secret in (self.raw, "08077778888", "holder@record.example"):
            self.assertNotIn(secret, " ".join(logs.output))
        provider.assert_called_once()
        sms.assert_not_called()
        email.assert_not_called()
        self.assertNotIn("id_otp_hash", PendingAction.objects.get().payload)
        self.assertFalse(IdentityProof.objects.exists())
        self.assertFalse(VirtualAccount.objects.exists())

    def test_email_budget_exhaustion_preserves_the_accepted_sms_challenge(self):
        self.unverified()
        token = self.start()
        self.consent(token)
        clock = [100.0]

        def lookup(*_args, **_kwargs):
            clock[0] += 5.5
            return {"success": True, "first_name": "Ada", "last_name": "Eze",
                    "phone": "08077778888", "email": "holder@record.example"}

        def sms(*_args, **_kwargs):
            clock[0] += 1.9
            return {"success": True}

        with patch("whatsapp.vas_identity.monotonic", side_effect=lambda: clock[0]), \
                patch("utility.providers._prembly_identity_live", return_value=True), \
                patch("utility.providers.prembly_verify_bvn", side_effect=lookup), \
                patch.object(router, "sms_live", return_value=True), \
                patch.object(router, "email_live", return_value=True), \
                patch.object(router, "send_sms", side_effect=sms) as sent, \
                patch.object(router, "send_email") as email:
            response = self.exchange(token, {"number": self.raw}, vas_flow.IDENTITY)
        email.assert_not_called()
        self.assertEqual(response["screen"], vas_flow.CODE)
        self.assertIn("Email delivery was unavailable", response["data"]["summary"])
        code = sent.call_args.args[1].split("Zitch: ")[1][:6]
        self.assertEqual(self.exchange(token, {"number": code}, vas_flow.CODE)["screen"], "RESULT")

    def test_lookup_failure_log_does_not_copy_provider_messages_or_contacts(self):
        self.unverified()
        token = self.start()
        self.consent(token)
        private = self.raw + " holder@record.example 08077778888"
        with patch("utility.providers._prembly_identity_live", return_value=True), \
                patch("utility.providers.prembly_verify_bvn", return_value={
                    "success": False, "message": private, "raw": {"phone": private}}), \
                self.assertLogs("zitch.security", level="WARNING") as logs:
            response = self.exchange(token, {"number": self.raw}, vas_flow.IDENTITY)
        self.assertEqual(response["data"]["status"], "Not completed")
        self.assertIn("category=lookup_unavailable", " ".join(logs.output))
        self.assertNotIn(private, " ".join(logs.output))

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

    def test_record_email_receives_same_code_and_only_masked_destinations_persist(self):
        self.unverified()
        token = self.start()
        self.consent(token)
        with patch("utility.providers._prembly_identity_live", return_value=True), \
                patch("utility.providers.prembly_verify_bvn", return_value={"success": True,
                    "first_name": "Ada", "last_name": "Eze", "phone": "08077778888",
                    "email": "holder@record.example"}), \
                patch.object(router, "sms_live", return_value=True), \
                patch.object(router, "email_live", return_value=True), \
                patch.object(router, "send_sms", return_value={"success": True}) as sms, \
                patch.object(router, "send_email", return_value={"success": True}) as email:
            response = self.exchange(token, {"number": self.raw}, vas_flow.IDENTITY)
        code = sms.call_args.args[1].split("Zitch: ")[1][:6]
        self.assertEqual(email.call_args.args[0], "holder@record.example")
        self.assertIn(code, email.call_args.args[2])
        self.assertEqual(email.call_args.kwargs["timeout"].total, 2)
        self.assertIn("SMS and email", response["data"]["summary"])
        pa = PendingAction.objects.get()
        self.assertEqual(pa.payload["id_otp_delivery"]["delivery_channels"], ["sms", "email"])
        for value in (self.raw, code, "holder@record.example", self.user.email):
            self.assertNotIn(value, json.dumps(pa.payload))
        self.exchange(token, {"number": code}, vas_flow.CODE)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "signup@example.test")
        self.assertTrue(self.user.bvn_verified)

    def test_unaccepted_record_email_preserves_sms_without_claiming_email_delivery(self):
        self.unverified()
        token = self.start()
        self.consent(token)
        with patch("utility.providers._prembly_identity_live", return_value=True), \
                patch("utility.providers.prembly_verify_bvn", return_value={"success": True,
                    "first_name": "Ada", "last_name": "Eze", "phone": "08077778888",
                    "email": "holder@record.example"}), \
                patch.object(router, "sms_live", return_value=True), \
                patch.object(router, "email_live", return_value=True), \
                patch.object(router, "send_sms", return_value={"success": True}) as sms, \
                patch.object(router, "send_email", side_effect=RuntimeError("holder@record.example refused")):
            response = self.exchange(token, {"number": self.raw}, vas_flow.IDENTITY)
        self.assertEqual(response["screen"], vas_flow.CODE)
        self.assertNotIn("SMS and email", response["data"]["summary"])
        self.assertIn("Email delivery was unavailable", response["data"]["summary"])
        self.assertNotIn("holder@record.example", json.dumps(response))
        self.assertTrue(PendingAction.objects.get().payload["id_otp_delivery"]["delivery_partial"])
        code = sms.call_args.args[1].split("Zitch: ")[1][:6]
        self.assertEqual(self.exchange(token, {"number": code}, vas_flow.CODE)["screen"], "RESULT")

    def test_absent_record_email_never_falls_back_to_signup_email(self):
        self.unverified()
        token = self.start()
        self.consent(token)
        with patch.object(router, "email_live", return_value=True), patch.object(router, "send_email") as email:
            self.provider_code(token)
        email.assert_not_called()
        self.assertEqual(PendingAction.objects.get().payload["id_otp_delivery"]["delivery_status"]["email"], "not_available")

    def test_failed_sms_never_sends_email_or_arms_a_code(self):
        self.unverified()
        token = self.start()
        self.consent(token)
        with patch("utility.providers._prembly_identity_live", return_value=True), \
                patch("utility.providers.prembly_verify_bvn", return_value={"success": True,
                    "first_name": "Ada", "last_name": "Eze", "phone": "08077778888",
                    "email": "holder@record.example"}), \
                patch.object(router, "sms_live", return_value=True), \
                patch.object(router, "email_live", return_value=True), \
                patch.object(router, "send_sms", return_value={"success": False}), \
                patch.object(router, "send_email") as email:
            response = self.exchange(token, {"number": self.raw}, vas_flow.IDENTITY)
        email.assert_not_called()
        self.assertEqual(response["data"]["status"], "Not completed")
        self.assertNotIn("id_otp_hash", PendingAction.objects.get().payload)

    def test_contact_email_change_revokes_open_setup(self):
        token = self.start()
        self.consent(token)
        User.objects.filter(pk=self.user.pk).update(email="changed@example.test")
        with patch("whatsapp.vas_flow.enroll_customer") as enroll:
            self.exchange(token, {"number": self.raw}, vas_flow.IDENTITY)
        enroll.assert_not_called()

    def test_validation_result_reports_pending_activation_without_sample_number(self):
        with override_settings(WEMA_VAS=self.mode_settings("validation")), \
                patch("whatsapp.vas_flow.enroll_customer", wraps=vas_flow.enroll_customer) as enroll:
            token = self.start()
            self.consent(token)
            response = self.exchange(token, {"number": self.raw}, vas_flow.IDENTITY)
        account = VirtualAccount.objects.get()
        self.assertEqual(response["data"]["status"], "Account activation pending")
        self.assertEqual(account.mode, "validation")
        self.assertTrue(account.number.startswith("711"))
        self.assertNotIn(account.number, json.dumps(response))
        self.assertNotIn("test", json.dumps(response).lower())
        self.assertNotIn("funding account is ready", response["data"]["message"])
        version = vas_flow.consent_version("validation")
        self.assertTrue(account.consent_reference.startswith(version + ":whatsapp:"))
        self.assertEqual(enroll.call_args.kwargs["expected_mode"], "validation")
        self.assertEqual(enroll.call_args.kwargs["expected_consent_version"], version)
        pa = PendingAction.objects.get(action_type="vas_enroll")
        self.assertEqual(pa.payload["mode"], "validation")
        self.assertEqual(pa.payload["consent_version"], version)

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
        self.assertEqual(advanced["screen"], "RESULT")
        self.assertEqual(self.exchange(token, {"number": code}, vas_flow.CODE), advanced)
        self.assertEqual(IdentityProof.objects.count(), 1)
        self.assertEqual(VirtualAccount.objects.count(), 1)
        self.assertNotIn(self.raw, json.dumps(PendingAction.objects.get().payload))

    def test_identity_replay_with_changed_number_cannot_reuse_the_challenge(self):
        token = self.start()
        self.consent(token)
        self.provider_code(token)
        response = self.exchange(token, {"number": "99999999999"}, vas_flow.IDENTITY)
        self.assertEqual(response["data"]["status"], "Not completed")
        self.assertFalse(IdentityProof.objects.exists())

    def test_missing_proof_enrolls_after_provider_phone_sms_without_identity_reentry(self):
        token = self.start()
        self.consent(token)
        code = self.provider_code(token)
        response = self.exchange(token, {"number": code}, vas_flow.CODE)
        self.assertEqual(response["screen"], "RESULT")
        proof = IdentityProof.objects.get()
        self.assertEqual(proof.verified_name, "Ada Eze")
        self.assertEqual(response["data"]["status"], "Successful")
        self.assertEqual(VirtualAccount.objects.get().display_name, "Zitch/Ada Eze")

    def test_nin_also_finishes_with_one_identity_entry_and_one_ownership_code(self):
        self.unverified()
        token = self.start()
        self.consent(token, "nin")
        code = self.provider_code(token, "nin")
        response = self.exchange(token, {"number": code}, vas_flow.CODE)
        self.assertEqual(response["screen"], "RESULT")
        self.assertEqual(response["data"]["status"], "Successful")
        self.user.refresh_from_db()
        self.assertTrue(self.user.nin_verified)
        self.assertEqual(self.user.nin_hash, hash_identifier(self.raw))
        self.assertEqual(VirtualAccount.objects.count(), 1)
        self.assertEqual(IdentityProof.objects.get().identity_type, "nin")

    def test_wrong_code_then_correct_retry_finishes_without_reentry(self):
        self.unverified()
        token = self.start()
        self.consent(token)
        code = self.provider_code(token)
        wrong = "000000" if code != "000000" else "111111"
        response = self.exchange(token, {"number": wrong}, vas_flow.CODE)
        self.assertEqual(response["screen"], vas_flow.CODE_RETRY)
        self.assertFalse(IdentityProof.objects.exists())
        self.assertFalse(VirtualAccount.objects.exists())
        response = self.exchange(token, {"number": code}, vas_flow.CODE_RETRY)
        self.assertEqual(response["screen"], "RESULT")
        self.assertEqual(response["data"]["status"], "Successful")
        self.assertEqual(self.exchange(token, {"number": code}, vas_flow.CODE_RETRY), response)
        self.assertEqual(IdentityProof.objects.count(), 1)
        self.assertEqual(VirtualAccount.objects.count(), 1)

    def test_code_delivery_preserves_the_original_bound_session_deadline(self):
        self.unverified()
        token = self.start()
        self.consent(token)
        original_expiry = PendingAction.objects.get(action_type="vas_enroll").expires_at
        later = timezone.now() + timedelta(minutes=4)
        with patch("whatsapp.router.timezone.now", return_value=later):
            code = self.provider_code(token)
            pa = PendingAction.objects.get(action_type="vas_enroll")
            self.assertEqual(pa.expires_at, original_expiry)
            response = self.exchange(token, {"number": code}, vas_flow.CODE)
        self.assertEqual(response["data"]["status"], "Successful")
        self.assertEqual(VirtualAccount.objects.count(), 1)

    def test_capsule_loss_or_tampering_stops_before_consuming_ownership_code(self):
        for change in ("missing", "tampered", "legacy"):
            with self.subTest(change=change):
                cache.clear()
                self.unverified()
                token = self.start()
                self.consent(token)
                code = self.provider_code(token)
                pa = PendingAction.objects.get(action_type="vas_enroll")
                key = vas_capsule._key(pa.payload[vas_capsule.FIELD])
                if change == "missing":
                    cache.delete(key)
                elif change == "tampered":
                    cache.set(key, "changed-ciphertext")
                else:
                    pa.payload.pop(vas_capsule.FIELD)
                    pa.save(update_fields=["payload"])
                with patch.object(router, "kyc_flow_identity_otp") as confirm:
                    response = self.exchange(token, {"number": code}, vas_flow.CODE)
                confirm.assert_not_called()
                self.assertEqual(response["data"]["status"], "Not completed")
                self.assertIn("restart", response["data"]["message"])
                self.assertFalse(IdentityProof.objects.exists())
                self.assertFalse(VirtualAccount.objects.exists())

    def test_capsule_storage_outage_stops_before_paid_lookup_or_code_delivery(self):
        self.unverified()
        token = self.start()
        self.consent(token)
        with patch("whatsapp.vas_capsule.cache.add", side_effect=RuntimeError("cache unavailable")), \
                patch("utility.providers.prembly_verify_bvn") as lookup, \
                patch.object(router, "send_sms") as sms:
            response = self.exchange(token, {"number": self.raw}, vas_flow.IDENTITY)
        self.assertEqual(response["data"]["status"], "Not completed")
        lookup.assert_not_called()
        sms.assert_not_called()
        self.assertFalse(IdentityProof.objects.exists())
        self.assertFalse(VirtualAccount.objects.exists())

    def test_capsule_cleanup_waits_for_commit_and_runs_on_success_cancel_and_delete(self):
        for outcome in ("success", "cancel", "delete", "expired"):
            with self.subTest(outcome=outcome):
                cache.clear()
                VirtualAccount.objects.all().delete()
                self.unverified()
                token = self.start()
                self.consent(token)
                code = self.provider_code(token)
                pa = PendingAction.objects.get(action_type="vas_enroll")
                key = vas_capsule._key(pa.payload[vas_capsule.FIELD])
                with self.captureOnCommitCallbacks(execute=True) as callbacks:
                    if outcome == "success":
                        self.exchange(token, {"number": code}, vas_flow.CODE)
                    elif outcome == "cancel":
                        self.exchange(token, {"close": True}, vas_flow.CODE)
                    elif outcome == "expired":
                        PendingAction.objects.filter(pk=pa.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
                        with patch.object(router, "reply"):
                            router._announce_timeout(self.msisdn)
                    else:
                        router._clear_actions(self.msisdn)
                    self.assertIsNotNone(cache.get(key))
                self.assertTrue(callbacks)
                self.assertIsNone(cache.get(key))

    def test_cleanup_outage_does_not_replace_committed_success_with_an_error(self):
        self.unverified()
        token = self.start()
        self.consent(token)
        code = self.provider_code(token)
        with self.assertLogs("zitch.security", level="WARNING") as logs, \
                patch("whatsapp.vas_capsule.cache.delete", side_effect=RuntimeError("cache unavailable")), \
                self.captureOnCommitCallbacks(execute=True):
            response = self.exchange(token, {"number": code}, vas_flow.CODE)
        self.assertEqual(response["data"]["status"], "Successful")
        self.assertEqual(VirtualAccount.objects.count(), 1)
        self.assertNotIn(self.raw, " ".join(logs.output))

    def test_old_reentry_transport_replay_never_generates_a_new_reentry_screen(self):
        token = self.start()
        self.consent(token)
        code = self.provider_code(token)
        self.legacy_reentry(token, code)
        response = self.exchange(token, {"number": self.raw}, vas_flow.IDENTITY)
        self.assertEqual(response["screen"], "RESULT")
        self.assertIn("restart", response["data"]["message"])
        self.assertEqual(IdentityProof.objects.count(), 1)
        self.assertFalse(VirtualAccount.objects.exists())

    def test_already_displayed_legacy_reentry_can_finish_with_its_intact_proof(self):
        token = self.start()
        self.consent(token)
        code = self.provider_code(token)
        self.legacy_reentry(token, code)
        response = self.exchange(token, {"number": self.raw}, vas_flow.REENTRY)
        self.assertEqual(response["screen"], "RESULT")
        self.assertEqual(response["data"]["status"], "Successful")
        self.assertEqual(VirtualAccount.objects.count(), 1)

    def test_mistyped_reentry_preserves_proof_and_restart_needs_no_new_lookup(self):
        token = self.start()
        self.consent(token)
        code = self.provider_code(token)
        self.legacy_reentry(token, code)
        wrong = "99999999999"
        with patch("whatsapp.vas_flow.enroll_customer") as enroll, \
                self.assertLogs("zitch.security", level="WARNING") as logs:
            response = self.exchange(token, {"number": wrong}, vas_flow.REENTRY)
        enroll.assert_not_called()
        self.assertEqual(response["data"]["status"], "Not completed")
        self.assertIn("does not match the one you just verified", response["data"]["message"])
        self.assertIn("reply 6", response["data"]["message"])
        self.assertIn("verification is saved", response["data"]["message"])
        self.assertEqual(IdentityProof.objects.count(), 1)
        self.assertFalse(VirtualAccount.objects.exists())
        self.user.refresh_from_db()
        self.assertEqual(self.user.bvn_hash, hash_identifier(self.raw))
        self.assertTrue(self.user.bvn_verified)
        self.assertIn("category=reentry_mismatch", " ".join(logs.output))
        exposed = json.dumps(response) + json.dumps(PendingAction.objects.get().payload) + " ".join(logs.output)
        for value in (self.raw, wrong, hash_identifier(self.raw), self.msisdn):
            self.assertNotIn(value, exposed)
        # Terminal transport retries cannot turn the mistyped exchange into an
        # allocation. A fresh signed consent may use the intact named proof.
        self.assertEqual(self.exchange(token, {"number": self.raw}, vas_flow.REENTRY), response)
        cache.clear()
        token = self.start()
        self.consent(token)
        with patch("utility.providers.prembly_verify_bvn") as lookup, \
                patch.object(router, "send_sms") as sms:
            response = self.exchange(token, {"number": self.raw}, vas_flow.IDENTITY)
        lookup.assert_not_called()
        sms.assert_not_called()
        self.assertEqual(response["data"]["status"], "Successful")
        self.assertEqual(VirtualAccount.objects.count(), 1)

    def test_reentry_without_saved_proof_cannot_promise_saved_verification(self):
        token = self.start()
        self.consent(token)
        code = self.provider_code(token)
        self.legacy_reentry(token, code)
        IdentityProof.objects.all().delete()
        with patch("whatsapp.vas_flow.enroll_customer") as enroll:
            response = self.exchange(token, {"number": "99999999999"}, vas_flow.REENTRY)
        enroll.assert_not_called()
        self.assertEqual(response["data"]["status"], "Not completed")
        self.assertIn("needs review", response["data"]["message"])
        self.assertNotIn("saved", response["data"]["message"])
        self.assertFalse(VirtualAccount.objects.exists())

    def test_changed_verified_identity_after_code_cannot_be_replaced_by_reentry(self):
        token = self.start()
        self.consent(token)
        code = self.provider_code(token)
        self.legacy_reentry(token, code)
        changed = hash_identifier("99999999999")
        User.objects.filter(pk=self.user.pk).update(bvn_hash=changed)
        with patch("whatsapp.vas_flow.enroll_customer") as enroll, \
                self.assertLogs("zitch.security", level="WARNING") as logs:
            response = self.exchange(token, {"number": self.raw}, vas_flow.REENTRY)
        enroll.assert_not_called()
        self.assertEqual(response["data"]["status"], "Not completed")
        self.assertIn("already verified on this profile", response["data"]["message"])
        self.assertIn("category=verified_identity_mismatch", " ".join(logs.output))
        self.user.refresh_from_db()
        self.assertEqual(self.user.bvn_hash, changed)
        self.assertFalse(VirtualAccount.objects.exists())

    def test_another_profiles_identity_remains_blocked_without_disclosing_ownership(self):
        wrong = "99999999999"
        other = User.objects.create(username="different-owner", bvn_hash=hash_identifier(wrong), bvn_verified=True)
        token = self.start()
        self.consent(token)
        with patch("whatsapp.vas_flow.enroll_customer") as enroll, \
                patch("utility.providers.prembly_verify_bvn") as lookup, \
                self.assertLogs("zitch.security", level="WARNING") as logs:
            response = self.exchange(token, {"number": wrong}, vas_flow.IDENTITY)
        enroll.assert_not_called()
        lookup.assert_not_called()
        self.assertEqual(response["data"]["status"], "Not completed")
        self.assertIn("could not be confirmed", response["data"]["message"])
        self.assertNotIn(other.username, json.dumps(response))
        self.assertIn("category=identity_conflict", " ".join(logs.output))
        self.assertNotIn(wrong, " ".join(logs.output))
        self.assertFalse(VirtualAccount.objects.exists())

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
