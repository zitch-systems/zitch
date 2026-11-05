"""Provider outcome evidence reaches the audit stream without customer data."""
from types import SimpleNamespace

from django.test import SimpleTestCase

from .client import Reply
from .payments import _log_provider_reply


class PaymentOutcomeLoggingTests(SimpleTestCase):
    def test_pending_and_terminal_results_reach_the_application_audit_logger(self):
        instruction = SimpleNamespace(transaction=SimpleNamespace(reference="ZTCH-SYNTHETIC-ABCDEF"),
                                      product="vas", operation="airtime")
        for decision, code, phase in (("pending", "202", "submit"), ("failed", "200", "status")):
            with self.subTest(decision=decision), self.assertLogs("zitch", level="INFO") as records:
                response = Reply({"status": "SUCCESS", "responseCode": code}, 200)
                _log_provider_reply(instruction, response, {}, True, phase=phase, decision=decision)
            line = " ".join(records.output)
            self.assertIn("suffix=ABCDEF", line)
            self.assertIn("decision=" + decision, line)
            self.assertIn("phase=" + phase, line)
            self.assertIn("code=" + code, line)
            self.assertNotIn(instruction.transaction.reference, line)

    def test_provider_messages_names_accounts_and_credentials_are_excluded(self):
        private = ["PRIVATE-HOLDER", "08011112222", "1100000001", "PRIVATE-BEARER-TOKEN"]
        instruction = SimpleNamespace(transaction=SimpleNamespace(reference="ZTCH-SYNTHETIC-ABCDEF"),
                                      product="vas", operation="airtime")
        data = {"recipient": private[1], "debitAccount": private[2], "name": private[0],
                "accessToken": private[3], "status": private[3], "responseCode": private[3]}
        response = Reply({"status": private[0], "message": " ".join(private), "responseCode": private[3]}, 200)
        with self.assertLogs("zitch", level="INFO") as records:
            _log_provider_reply(instruction, response, data, False, phase="status", decision="pending")
        line = " ".join(records.output)
        for value in private:
            self.assertNotIn(value, line)
        self.assertIn("code=absent", line)
        self.assertIn("envelope=unrecognized", line)
        self.assertIn("echoes_match=False", line)
