"""An archived bank rail rejects new work but still resolves accepted work."""
from decimal import Decimal
from unittest import mock

from django.test import SimpleTestCase, override_settings

from utility import wema


LIVE = {
    "BASE_URL": "https://bank.example", "CHANNEL_ID": "test-channel",
    "KEYS": {"wallet": "test-wallet", "face_account": "test-face",
             "upgrade": "test-upgrade", "remita": "test-remita",
             "bnpl": "test-bnpl", "card": "test-card"},
    "BNPL_MERCHANT_ID": "test-merchant", "BNPL_AUTH_KEY": "test-auth",
    "SIMULATION": False, "VAS_STATUS_LEGEND": "1=success 2=failed",
}


@override_settings(WEMA=LIVE, WEMA_PARTNERSHIP_MODE="archive",
                   BANK_ACCOUNT_PROVIDER="partnership")
class PartnershipArchiveTests(SimpleTestCase):
    def initiating_calls(self):
        return (
            (wema.create_wallet_request, ("08000000000", "test@example.com"),
             {"bvn": "11111111111"}),
            (wema.create_wallet_with_face, ("08000000000", "test@example.com"),
             {"identity_type": "bvn", "identity_value": "11111111111",
              "correlation_id": "existing-face-ref"}),
            (wema.upgrade_tier2, ("0123456789",), {"bvn": "11111111111"}),
            (wema.upgrade_tier3, ("0123456789", "Example address"), {}),
            (wema.transfer, (100, "legacy-ref", "Transfer"),
             {"source_account": "0123456789", "destination_account": "0123456780",
              "destination_bank_code": "035", "destination_bank_name": "Wema",
              "destination_name": "Test Person"}),
            (wema.credit_wallet, (100, "legacy-ref", "Credit"),
             {"destination_account": "0123456789"}),
            (wema.purchase_airtime, (100, "legacy-ref", "08000000000", "MTN"), {}),
            (wema.purchase_data, (100, "legacy-ref", "08000000000", "MTN", "1"), {}),
            (wema.pay_bill, (100, "legacy-ref"), {"package_id": "1", "identifier": "meter"}),
            (wema.pay_remita, (100, "legacy-ref"), {"rrr": "123456789"}),
            (wema.bnpl_consent, ("0123456789", 100, 1, "customer-ref"), {}),
            (wema.bnpl_accept_terms, ("eligibility-ref",), {}),
            (wema.bnpl_liquidate, ("customer-ref",), {"amount": 100}),
            (wema.card_issue, ("Test Person", "customer-ref"), {"account_number": "0123456789"}),
            (wema.card_fund, ("0123456789", 100), {}),
        )

    def assert_new_work_refused(self):
        with mock.patch.object(wema, "_post") as post, mock.patch.object(wema, "_get") as get:
            for function, args, kwargs in self.initiating_calls():
                with self.subTest(operation=function.__name__):
                    result = function(*args, **kwargs)
                    self.assertFalse(result["success"])
                    self.assertFalse(result["pending"])
                    self.assertTrue(result["not_charged"])
                    self.assertEqual(result["code"], "partnership_archived")
            post.assert_not_called()
            get.assert_not_called()

    def test_direct_new_bank_instructions_fail_before_network(self):
        self.assert_new_work_refused()

    @override_settings(WEMA={**LIVE, "SIMULATION": True})
    def test_simulation_cannot_bypass_archive(self):
        self.assert_new_work_refused()

    @override_settings(WEMA_PARTNERSHIP_MODE="active", BANK_ACCOUNT_PROVIDER="wema_vas")
    def test_selecting_vas_also_refuses_new_partnership_business(self):
        self.assert_new_work_refused()

    def test_unknown_mode_and_provider_fail_closed(self):
        for mode, provider in (("archvie", "partnership"), ("active", "typo"), ("", "partnership")):
            with self.subTest(mode=mode, provider=provider), override_settings(
                    WEMA_PARTNERSHIP_MODE=mode, BANK_ACCOUNT_PROVIDER=provider):
                self.assert_new_work_refused()

    @override_settings(WEMA_PARTNERSHIP_MODE="active")
    def test_active_mode_still_submits_real_instructions(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {"status": True, "data": {"trackingId": "bank-attempt"}}
        with mock.patch.object(wema, "_post", return_value=response) as post:
            result = wema.create_wallet_request("08000000000", "test@example.com", bvn="11111111111")
        self.assertTrue(result["success"])
        self.assertEqual(result["tracking_id"], "bank-attempt")
        self.assertEqual(post.call_count, 1)

    def test_existing_transfer_can_reach_terminal_status(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {"hasError": False, "result": {
            "status": "SUCCESS", "transactionReference": "legacy-ref"}}
        with mock.patch.object(wema, "_get", return_value=response) as get:
            result = wema.confirm_transfer_status("legacy-ref")
        self.assertTrue(result["success"])
        get.assert_called_once_with("debit", "/api/IntraBankTransfer/ConfirmClientTransferStatus/legacy-ref")

    def test_existing_credit_can_reach_terminal_status(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {"hasError": False, "result": {
            "status": "SUCCESS", "transactionReference": "legacy-ref"}}
        with mock.patch.object(wema, "_get", return_value=response) as get:
            result = wema.confirm_credit_status("legacy-ref")
        self.assertTrue(result["success"])
        get.assert_called_once_with("credit", "/api/IntraBankTransfer/ConfirmClientTransferStatus/legacy-ref")

    def test_existing_airtime_requery_still_uses_bank_status(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {"hasError": False, "result": {"transactionStatus": 1}}
        with mock.patch.object(wema, "_post", return_value=response) as post:
            result = wema.vas_status("legacy-ref", "airtime")
        self.assertTrue(result["success"])
        post.assert_called_once_with("airtime", "/api/PartnerPayment/CheckTransactionStatus",
                                     {"transactionReference": "legacy-ref", "transactionType": 1})

    def test_existing_otp_can_finish_and_be_resent(self):
        response = mock.Mock(status_code=200, content=b"")
        response.json.return_value = {"status": True}
        with mock.patch.object(wema, "_post", return_value=response) as post:
            result = wema.validate_wallet_otp("08000000000", "123456", "existing-attempt", bvn=True)
            resent = wema.resend_wallet_otp("08000000000", "existing-attempt", bvn=True)
        self.assertTrue(result["success"])
        self.assertTrue(resent["success"])
        self.assertEqual(post.call_count, 2)

    def test_historical_balances_remain_readable(self):
        response = mock.Mock(status_code=200, headers={})
        response.json.return_value = {"successful": True, "result": {"availableBalance": "123.45"}}
        with mock.patch.object(wema, "_get", return_value=response) as get:
            result = wema.get_balance("0123456789")
        self.assertTrue(result["success"])
        self.assertEqual(result["balance_naira"], Decimal("123.45"))
        self.assertEqual(get.call_count, 1)

    def test_card_can_still_be_blocked_and_unaccepted_loan_declined(self):
        response = mock.Mock(status_code=200, content=b"")
        response.json.return_value = {"hasError": False}
        with mock.patch.object(wema, "_post", return_value=response) as post:
            blocked = wema.card_set_status("0123456789", False, masked_pan="5061****1234")
            declined = wema.bnpl_accept_terms("eligibility-ref", accepted=False)
        self.assertTrue(blocked["success"])
        self.assertTrue(declined["success"])
        self.assertEqual(post.call_count, 2)
