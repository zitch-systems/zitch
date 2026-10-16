"""An app-issued invitation cannot outlive account or channel revocation."""
import json
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from whatsapp.auth_proof import credentials
from whatsapp.linking import issue_link, redeem_link
from whatsapp.models import PendingAction, WaMessageLog, WhatsAppLink
from whatsapp.tests import make_user


MSISDN = "2348011112222"


class LinkCapabilityBoundaryTests(TestCase):
    def setUp(self):
        self.user, self.token = make_user(phone="08011112222")

    def issue(self):
        return issue_link(self.user, credentials(self.user))

    def test_credential_changes_revoke_unfinished_channel_link(self):
        for field, replacement in (("transaction_pin", "changed-pin"),
                                   ("password", "changed-password"),
                                   ("email", "changed@zitch.test"),
                                   ("phone", "08011112223"),
                                   ("phone_verified", False)):
            with self.subTest(field=field):
                self.user.refresh_from_db()
                link = self.issue()
                original = getattr(self.user, field)
                type(self.user).objects.filter(pk=self.user.pk).update(**{field: replacement})
                outcome, _ = redeem_link(link.link_code, MSISDN)
                self.assertEqual(outcome, "revoked")
                self.assertFalse(WhatsAppLink.objects.filter(status=WhatsAppLink.ACTIVE).exists())
                link.refresh_from_db()
                self.assertEqual(link.link_code, "")
                type(self.user).objects.filter(pk=self.user.pk).update(**{field: original})

    def test_freeze_or_pin_reset_revokes_outstanding_link(self):
        for field, value in (("is_active", False), ("pin_reset_required", True)):
            with self.subTest(field=field):
                self.user.refresh_from_db()
                link = self.issue()
                original = getattr(self.user, field)
                type(self.user).objects.filter(pk=self.user.pk).update(**{field: value})
                self.assertEqual(redeem_link(link.link_code, MSISDN)[0], "revoked")
                self.assertFalse(WhatsAppLink.objects.filter(status=WhatsAppLink.ACTIVE).exists())
                type(self.user).objects.filter(pk=self.user.pk).update(**{field: original})

    def test_wrong_country_with_same_last_ten_digits_cannot_link(self):
        link = self.issue()
        self.assertEqual(redeem_link(link.link_code, "448011112222")[0], "wrong_number")
        self.assertFalse(WhatsAppLink.objects.filter(status=WhatsAppLink.ACTIVE).exists())

    def test_expired_and_legacy_unsigned_codes_cannot_link(self):
        link = self.issue()
        WhatsAppLink.objects.filter(pk=link.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(redeem_link(link.link_code, MSISDN)[0], "revoked")
        WhatsAppLink.objects.filter(pk=link.pk).update(link_code="A" * 32,
            expires_at=timezone.now() + timedelta(minutes=10))
        self.assertEqual(redeem_link("A" * 32, MSISDN)[0], "revoked")
        self.assertFalse(WhatsAppLink.objects.filter(status=WhatsAppLink.ACTIVE).exists())

    def test_pin_proof_changed_during_verification_cannot_issue_link(self):
        from common.http import evaluate_transaction_pin

        def verify_then_reset(user, pin):
            result = evaluate_transaction_pin(user, pin)
            type(user).objects.filter(pk=user.pk).update(password="reset-during-proof")
            return result

        with patch("whatsapp.views.evaluate_transaction_pin", side_effect=verify_then_reset):
            response = self.client.post("/api/whatsapp/link/start/",
                data=json.dumps({"access_token": self.token, "transaction_pin": "1234"}),
                content_type="application/json")
        self.assertEqual(response.status_code, 409)
        self.assertFalse(WhatsAppLink.objects.filter(user=self.user).exists())

    def test_unlink_also_revokes_pending_invitation(self):
        WhatsAppLink.objects.create(user=self.user, wa_msisdn=MSISDN, status=WhatsAppLink.ACTIVE)
        link = self.issue()
        response = self.client.post("/api/whatsapp/link/unlink/",
            data=json.dumps({"access_token": self.token}), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WhatsAppLink.objects.filter(user=self.user).exists())
        self.assertEqual(redeem_link(link.link_code, MSISDN)[0], "missing")

    def test_link_capability_is_not_written_to_support_message_log(self):
        from whatsapp.views import _process

        code = self.issue().link_code
        for index, body in enumerate((code, "LINK " + code)):
            with patch("whatsapp.jobs.process_inbound_message"):
                _process({"id": f"link-redaction-{index}", "from": MSISDN,
                          "type": "text", "text": {"body": body}})
            row = WaMessageLog.objects.get(wa_message_id=f"link-redaction-{index}")
            self.assertNotIn(code, row.text)
            self.assertEqual(row.text, "[WhatsApp link code]")

    def test_valid_link_is_single_use_and_preserves_privacy_preferences(self):
        old = WhatsAppLink.objects.create(user=self.user, wa_msisdn="2348099990001",
            status=WhatsAppLink.ACTIVE, ai_enabled=False, marketing_opt_in=False)
        pending = PendingAction.objects.create(user=self.user, msisdn=old.wa_msisdn,
            action_type="transfer", state="flow_pin", expires_at=timezone.now() + timedelta(minutes=10))
        executing = PendingAction.objects.create(user=self.user, msisdn=old.wa_msisdn,
            action_type="transfer", state="executing", expires_at=timezone.now() + timedelta(minutes=10))
        link = self.issue()
        code = link.link_code
        self.assertEqual(len(code), 64)
        self.assertEqual(redeem_link(code, MSISDN)[0], "linked")
        self.assertEqual(redeem_link(code, MSISDN)[0], "missing")
        link.refresh_from_db()
        self.assertFalse(link.ai_enabled)
        self.assertFalse(link.marketing_opt_in)
        pending.refresh_from_db()
        executing.refresh_from_db()
        self.assertTrue(pending.expired)
        self.assertFalse(executing.expired)
        self.assertFalse(WhatsAppLink.objects.filter(pk=old.pk).exists())
