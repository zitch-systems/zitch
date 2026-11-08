"""Old setup links and jobs expire without changing identity or allocating funds."""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from whatsapp import flows, jobs
from whatsapp.models import PendingAction, WaMessageLog
from whatsapp.retired_setup import RETIRED_SETUP_MESSAGE
from whatsapp.test_flows import MSISDN, _make_user


@override_settings(WHATSAPP_PROCESS_INLINE=False)
class RetiredSetupTests(TestCase):
    def setUp(self):
        self.user = _make_user()

    def action(self, *, action_type="kyc", state=flows.FLOW_ID_STATE, payload=None):
        return PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type=action_type, state=state,
            payload=payload or {"vas_identity": True, "id_kind": "bvn"},
            expires_at=timezone.now() + timedelta(minutes=5),
        )

    def test_retired_identity_session_cannot_submit_identity_or_ownership_code(self):
        for marker in ("vas_identity", "vas_contacts"):
            with self.subTest(marker=marker):
                pa = self.action(payload={marker: True, "id_kind": "bvn", "id_otp_hash": "old-proof"})
                token = flows.sign_identity_token(pa)
                with patch.object(flows, "_submit_identity") as identity, \
                        patch.object(flows, "_submit_identity_otp") as code:
                    self.assertIsNone(flows.resolve_identity_token(token))
                    response = flows.handle_flow_request({"action": "data_exchange", "flow_token": token,
                                                          "data": {"number": "123456"}})
                identity.assert_not_called()
                code.assert_not_called()
                self.assertIn("expired", str(response))
                pa.delete()

    def test_retired_unlock_cannot_resolve_pin_or_app_approval_token(self):
        pa = self.action(action_type="unlock", state=flows.FLOW_PIN_STATE,
                         payload={"vas_reregister": True})
        self.assertIsNone(flows.resolve_flow_token(flows.sign_flow_token(pa)))
        self.assertIsNone(flows.resolve_approve_token(flows.sign_approve_token(pa)))

    def test_old_provider_token_never_falls_through_to_identity_or_pin(self):
        with patch.object(flows, "_submit_identity") as identity, \
                patch("whatsapp.router.authorise_flow_execution") as execute:
            response = flows.handle_flow_request({"action": "data_exchange", "flow_token": "va1.obsolete",
                                                  "data": {"number": "12345678901"}})
        identity.assert_not_called()
        execute.assert_not_called()
        self.assertIn("expired", str(response))

    def test_authorised_retired_job_is_drained_without_executing(self):
        pa = self.action(action_type="unlock", state="executing", payload={"vas_reregister": True})
        row, _ = jobs.enqueue_flow_execution(pa)
        with patch("whatsapp.router.run_flow_execution") as execute, patch.object(jobs, "reply") as reply:
            self.assertEqual(jobs.process_inbound_message(row.pk), "processed")
        execute.assert_not_called()
        reply.assert_called_once_with(MSISDN, RETIRED_SETUP_MESSAGE)
        self.assertFalse(PendingAction.objects.filter(pk=pa.pk).exists())

    def test_media_queued_before_retirement_is_discarded_before_ai(self):
        row, _ = jobs.enqueue_inbound(message_id="old-setup-media", msisdn=MSISDN, logged_text="[image]",
                                     payload={"media_id": "private-photo", "media_kind": "image"})
        self.action(action_type="vas_enroll", state="flow_vas")
        with patch("whatsapp.media.interpret") as media, patch.object(jobs, "reply") as reply:
            self.assertEqual(jobs.process_inbound_message(row.pk), "processed")
        media.assert_not_called()
        self.assertIn(RETIRED_SETUP_MESSAGE, reply.call_args.args[1])
        row.refresh_from_db()
        self.assertEqual(bytes(row.processing_payload), b"")
        self.assertEqual(row.text, "[private setup input]")

    def test_private_input_from_old_queue_contract_has_no_chat_fallback(self):
        row, _ = jobs.enqueue_inbound(message_id="old-private-marker", msisdn=MSISDN, logged_text="[private]",
                                     payload={"vas_private_input": True})
        with patch.object(jobs, "handle_inbound") as route, patch.object(jobs, "reply") as reply:
            self.assertEqual(jobs.process_inbound_message(row.pk), "processed")
        route.assert_not_called()
        self.assertIn(RETIRED_SETUP_MESSAGE, reply.call_args.args[1])

    def test_webhook_does_not_store_private_input_from_retired_session(self):
        from whatsapp.views import _process

        self.action(action_type="vas_enroll", state="flow_vas")
        with patch("whatsapp.jobs.process_inbound_message"):
            _process({"id": "retired-raw-input", "from": MSISDN, "type": "text",
                      "text": {"body": "12345678901"}})
        row = WaMessageLog.objects.get(wa_message_id="retired-raw-input")
        payload = jobs._decrypt(row.processing_payload)
        self.assertTrue(payload["retired_setup_input"])
        self.assertEqual(payload["body"], "")
        self.assertNotIn("12345678901", row.text)
