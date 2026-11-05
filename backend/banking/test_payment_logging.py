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


class UpgradeStatusLoggingTests(SimpleTestCase):
    """An upgrade can wait at the bank for hours; the wait must leave evidence."""

    def request(self):
        return SimpleNamespace(reference="ZU-SYNTHETIC-ABCDEF", tier=2, state="submitted")

    def test_an_undecided_reply_records_what_the_bank_reported(self):
        from .upgrades import _log_upgrade_status
        bank = SimpleNamespace(number="1100000001")
        response = Reply({"status": "SUCCESS", "responseCode": "00",
                          "data": {"status": "PENDING", "accountNumber": "1100000001"}}, 200)
        with self.assertLogs("zitch", level="INFO") as records:
            _log_upgrade_status(self.request(), response, response.data, bank, "undecided")
        line = " ".join(records.output)
        self.assertIn("suffix=ABCDEF", line)
        self.assertIn("decision=undecided", line)
        self.assertIn("reported=PENDING", line)
        self.assertIn("stored=submitted", line)
        self.assertIn("tier=2", line)
        self.assertIn("account_echo=match", line)

    def test_an_unrecognised_status_is_named_as_such_rather_than_echoed(self):
        from .upgrades import _log_upgrade_status
        bank = SimpleNamespace(number="1100000001")
        response = Reply({"status": "SUCCESS", "data": {"status": "AWAITING-COMPLIANCE-REVIEW"}}, 200)
        with self.assertLogs("zitch", level="INFO") as records:
            _log_upgrade_status(self.request(), response, response.data, bank, "undecided")
        line = " ".join(records.output)
        self.assertIn("reported=unrecognized", line)
        self.assertNotIn("AWAITING-COMPLIANCE-REVIEW", line)
        self.assertIn("account_echo=absent", line)

    def test_a_mismatched_account_echo_is_reported_without_the_numbers(self):
        from .upgrades import _log_upgrade_status
        bank = SimpleNamespace(number="1100000001")
        response = Reply({"status": "SUCCESS", "data": {"status": "APPROVED",
                                                        "accountNumber": "2200000002"}}, 200)
        with self.assertLogs("zitch", level="INFO") as records:
            _log_upgrade_status(self.request(), response, response.data, bank, "unconfirmed")
        line = " ".join(records.output)
        self.assertIn("account_echo=mismatch", line)
        for private in ("1100000001", "2200000002", "ZU-SYNTHETIC-ABCDEF"):
            self.assertNotIn(private, line)
