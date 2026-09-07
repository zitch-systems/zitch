from django.test import SimpleTestCase

from common.logging import redact_log_text


class LogRedactionTests(SimpleTestCase):
    def test_redacts_email_phone_account_and_security_value(self):
        rendered = redact_log_text(
            "email=ada@example.com phone=08012345678 account=0123456789 "
            "securityInfo=private-payout-secret"
        )
        for secret in ("ada@example.com", "08012345678", "0123456789",
                       "private-payout-secret"):
            self.assertNotIn(secret, rendered)
        self.assertIn("a***@example.com", rendered)
        self.assertIn("account=[redacted]", rendered)

    def test_preserves_non_sensitive_reference_and_event_name(self):
        rendered = redact_log_text("wema_txn_cb ref=ZTCHABC123 outcome=pending")
        self.assertEqual(rendered, "wema_txn_cb ref=ZTCHABC123 outcome=pending")
