"""Tests for the Wema / ALAT client (utility.wema), no real credentials needed:
- MOCK mode (no keys): funding/enquiry/transfer return success stubs offline.
- SIMULATION: mock served even in prod (WEMA_SIMULATION); fails closed without it.
- Simulated LIVE: utility.wema.requests patched to build the real request and parse
  ALAT's two envelope shapes ({status,...} and {result,hasError,...}).
"""
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from utility import wema

WEMA_LIVE = {"BASE_URL": "https://apiplayground.alat.ng", "CHANNEL_ID": "chan-1",
             "KEYS": {"wallet": "subkey", "card": "", "airtime": "", "bills": "", "kyc": ""},
             "SECURITY_INFO": "sec", "SIMULATION": False}
WEMA_NOKEY = {**WEMA_LIVE, "CHANNEL_ID": "", "KEYS": {"wallet": ""}, "SECURITY_INFO": ""}


def _resp(body):
    m = MagicMock()
    m.json.return_value = body
    return m


class WemaMockTests(SimpleTestCase):
    def test_mock_mode_active(self):
        self.assertFalse(wema.wema_live())

    def test_funding_and_enquiry_mock(self):
        r = wema.create_wallet_request("08030000000", "a@b.com", bvn="22222222222")
        self.assertTrue(r["success"])
        self.assertTrue(r["tracking_id"])
        self.assertTrue(wema.validate_wallet_otp("08030000000", "123456", r["tracking_id"], bvn=True)["success"])
        acct = wema.get_account_details("08030000000", bvn=True)
        self.assertTrue(acct["success"])
        self.assertTrue(acct["account_number"])
        self.assertTrue(wema.resolve_account("0123456789", "035")["success"])
        self.assertTrue(wema.get_banks()["success"])

    def test_transfer_and_credit_mock(self):
        self.assertTrue(wema.transfer(1000, "REF-1", "test", source_account="01", destination_account="02",
                                      destination_bank_code="035", destination_bank_name="Wema",
                                      destination_name="ADA")["success"])
        self.assertTrue(wema.credit_wallet(1000, "REF-2", "test", destination_account="01")["success"])


@override_settings(DEBUG=False, TESTING=False)
class WemaSimulationTests(SimpleTestCase):
    def test_prod_without_keys_fails_closed(self):
        with override_settings(WEMA={**WEMA_NOKEY, "SIMULATION": False}):
            self.assertFalse(wema.wema_simulation())
            self.assertFalse(wema.create_wallet_request("080", "a@b.com", nin="1")["success"])
            self.assertFalse(wema.resolve_account("0123456789", "035")["success"])
            self.assertFalse(wema.transfer(1000, "R", "n", source_account="1", destination_account="2",
                                           destination_bank_code="035", destination_bank_name="W",
                                           destination_name="X")["success"])

    def test_simulation_serves_mock_in_prod(self):
        with override_settings(WEMA={**WEMA_NOKEY, "SIMULATION": True}):
            self.assertTrue(wema.wema_simulation())
            self.assertTrue(wema.get_account_details("080")["account_number"])
            self.assertEqual(wema.wema_diagnostics()["status"], "simulation")


@override_settings(WEMA=WEMA_LIVE)
class WemaLiveTests(SimpleTestCase):
    def test_wema_live_true(self):
        self.assertTrue(wema.wema_live())

    @patch("utility.wema.requests.get")
    def test_name_enquiry_live(self, mock_get):
        mock_get.return_value = _resp({"result": {"accountName": "ADA EZE", "bankCode": "035"},
                                       "hasError": False})
        r = wema.resolve_account("0123456789", "035")
        self.assertTrue(r["success"])
        self.assertEqual(r["name"], "ADA EZE")
        # correct headers: subscription key + channel id (access on debit product)
        headers = mock_get.call_args[1]["headers"]
        self.assertEqual(headers["Ocp-Apim-Subscription-Key"], "subkey")
        self.assertEqual(headers["access"], "chan-1")
        self.assertTrue(mock_get.call_args[0][0].endswith("/debit-wallet/api/Shared/AccountNameEnquiry/035/0123456789"))

    @patch("utility.wema.requests.get")
    def test_balance_live(self, mock_get):
        # GetAccountV2 envelope is {result, successful, message} — no status/hasError.
        mock_get.return_value = _resp({"result": {"availableBalance": "8420.10", "walletStatus": "Active"},
                                       "successful": True})
        r = wema.get_balance("0123456789")
        self.assertTrue(r["success"])          # must not be dropped by the envelope mismatch
        self.assertEqual(r["balance_naira"], Decimal("8420.10"))
        self.assertEqual(mock_get.call_args[1]["headers"]["x-api-key"], "chan-1")  # acct-mgt uses x-api-key

    def test_naira_tolerates_formatting(self):
        self.assertEqual(wema._naira("1,000.50"), Decimal("1000.50"))
        self.assertEqual(wema._naira("₦2,500"), Decimal("2500.00"))
        self.assertEqual(wema._naira("3000"), Decimal("3000.00"))
        self.assertIsNone(wema._naira("N/A"))
        self.assertIsNone(wema._naira(None))

    def test_normalize_transaction_credit(self):
        n = wema.normalize_transaction({"referenceId": "R1", "amount": "1,200.00",
                                        "creditType": "Credit", "narration": "in"})
        self.assertTrue(n["is_credit"])
        self.assertEqual(n["reference"], "R1")
        self.assertEqual(n["amount_naira"], Decimal("1200.00"))

    @patch("utility.wema.requests.post")
    def test_transfer_live_sends_securityinfo(self, mock_post):
        mock_post.return_value = _resp({"result": {"status": "SUCCESS", "transactionReference": "REF-1",
                                                    "platformTransactionReference": "WEMA-9"}, "hasError": False})
        r = wema.transfer(1000, "REF-1", "test", source_account="01", destination_account="02",
                          destination_bank_code="035", destination_bank_name="Wema", destination_name="ADA")
        self.assertTrue(r["success"])
        self.assertEqual(r["platform_reference"], "WEMA-9")
        body = mock_post.call_args[1]["json"]
        self.assertEqual(body["securityInfo"], "sec")       # from WEMA_SECURITY_INFO
        self.assertEqual(body["transactionReference"], "REF-1")
        self.assertEqual(body["destinationAccountNumber"], "02")
