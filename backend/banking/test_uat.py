import json
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from .client import ProviderError, Reply
from .test_client import CONFIG
from .uat import sandbox_checks


@override_settings(NINEPSB={**CONFIG, "ENVIRONMENT": "sandbox"})
class SandboxCheckTests(SimpleTestCase):
    def test_production_is_refused_before_authentication(self):
        with override_settings(NINEPSB=CONFIG), patch("banking.uat.Client") as client, self.assertRaises(ValueError):
            sandbox_checks()
        client.assert_not_called()

    def test_failed_credentials_block_product_calls_and_never_certify_uat(self):
        with patch("banking.client.Client.token", side_effect=ProviderError("authentication_failed")), \
                patch("banking.client.Client.request") as request:
            result = sandbox_checks()
        request.assert_not_called()
        self.assertFalse(result["connectivity_passed"])
        self.assertFalse(result["uat_complete"])
        self.assertFalse(result["activation_ready"])
        self.assertEqual(sum(r["status"] == "failed" for r in result["checks"]), 2)

    def test_successful_reads_remain_distinct_from_identity_and_payment_uat(self):
        replies = {
            "get_banks": Reply({"status": "SUCCESS", "data": {"bankList": [
                {"bankCode": "120001", "bankName": "9 Payment Service Bank"}]}}, 200),
            "wallet_enquiry": Reply({"status": "SUCCESS", "data": {"number": "1100000001",
                "name": "Test Customer", "availableBalance": "100", "ledgerBalance": "100"}}, 200),
            "other_banks_enquiry": Reply({"code": "00", "customer": {"account": {
                "number": "1100000001", "bank": "120001", "name": "Test Customer"}}}, 200),
            "billspayment/categories": Reply({"status": "success", "responseCode": "200", "data": [{"id": "1"}]}, 200),
            "topup/network": Reply({"status": "success", "responseCode": "200", "data": {"network": "MTN"}}, 200),
            "topup/dataPlans": Reply({"status": "success", "responseCode": "200", "data": [{"id": "2"}]}, 200),
        }
        with patch("banking.client.Client.token", return_value="private-token"), \
                patch("banking.client.Client.request", side_effect=lambda path, *a, **kw: replies[path]) as request:
            result = sandbox_checks({"account_number": "1100000001", "mobile": "08010000001"})
        self.assertEqual(request.call_count, 6)
        self.assertTrue(result["connectivity_passed"])
        self.assertFalse(result["uat_complete"])
        self.assertFalse(result["activation_ready"])
        encoded = json.dumps(result)
        for private in ("private-token", "1100000001", "08010000001", "Test Customer"):
            self.assertNotIn(private, encoded)

    def test_identity_fields_cannot_be_smuggled_into_connectivity_checks(self):
        with patch("banking.uat.Client") as client, self.assertRaises(ValueError):
            sandbox_checks({"bvn": "12345678901"})
        client.assert_not_called()
