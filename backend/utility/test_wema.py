"""Tests for the Wema / ALAT client (utility.wema), no real credentials needed:
- MOCK mode (no keys): funding/enquiry/transfer return success stubs offline.
- SIMULATION: mock served even in prod (WEMA_SIMULATION); fails closed without it.
- Simulated LIVE: utility.wema.requests patched to build the real request and parse
  ALAT's two envelope shapes ({status,...} and {result,hasError,...}).
"""
import json
import os
from decimal import Decimal
from unittest.mock import MagicMock, patch

import requests
from django.test import Client, SimpleTestCase, override_settings

from utility import wema

WEMA_LIVE = {"BASE_URL": "https://apiplayground.alat.ng", "CHANNEL_ID": "chan-1",
             "KEYS": {"wallet": "subkey", "card": "", "airtime": "", "bills": "", "kyc": ""},
             "SECURITY_INFO": "sec", "SIMULATION": False}
WEMA_NOKEY = {**WEMA_LIVE, "CHANNEL_ID": "", "KEYS": {"wallet": ""}, "SECURITY_INFO": ""}
WEMA_VAS = {**WEMA_LIVE, "KEYS": {"wallet": "subkey", "airtime": "airkey", "bills": "billkey"},
            "SOURCE_ACCOUNT": "0100000001"}


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

    def test_vas_mock(self):
        self.assertTrue(wema.purchase_airtime(500, "R", "08030000000", "MTN", source_account="01")["success"])
        self.assertTrue(wema.purchase_data(500, "R2", "08030000000", "MTN", "PKG", source_account="01")["success"])
        self.assertTrue(wema.get_data_plans("MTN")["success"])
        self.assertTrue(wema.get_bills()["success"])
        self.assertTrue(wema.validate_bill_customer("1234567890", "PKG")["success"])
        self.assertTrue(wema.pay_bill(2000, "R3", package_id="PKG", identifier="1234567890",
                                      source_account="01")["success"])


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

    @patch("utility.wema.requests.get")
    def test_failed_name_enquiry_carries_the_gateway_reason(self, mock_get):
        # Without this the caller falls back to a bare "Could not verify this account
        # number", which hides whether the account or our bank_code is the problem.
        mock_get.return_value = _resp(
            {"result": {}, "hasError": True,
             "errorMessage": "Account enquiry failed, confirm that the account number is valid"})
        r = wema.resolve_account("0123456789", "058")
        self.assertFalse(r["success"])
        self.assertEqual(r["message"],
                         "Account enquiry failed, confirm that the account number is valid")

    @patch("utility.wema.requests.get")
    def test_a_provider_branded_enquiry_reason_is_not_leaked(self, mock_get):
        mock_get.return_value = _resp({"result": {}, "hasError": True,
                                       "errorMessage": "Kindly download ALAT to continue"})
        self.assertEqual(wema.resolve_account("0123456789", "058")["message"], "Request failed")

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

    @override_settings(WEMA={**WEMA_LIVE, "SECURITY_INFO": ""})
    @patch("utility.wema.requests.post")
    def test_transfer_never_sends_a_blank_securityinfo(self, mock_post):
        # ALAT rejects a money-movement call carrying an empty securityInfo
        # ("Security Info must not be empty"), so an environment with keys but no
        # WEMA_SECURITY_INFO falls back to a stable SECRET_KEY-derived value.
        mock_post.return_value = _resp({"result": {"status": "SUCCESS", "transactionReference": "REF-2"},
                                        "hasError": False})
        wema.transfer(1000, "REF-2", "test", source_account="01", destination_account="02",
                      destination_bank_code="035", destination_bank_name="Wema", destination_name="ADA")
        sent = mock_post.call_args[1]["json"]["securityInfo"]
        self.assertTrue(sent)
        # Stable across calls, and the same value the auth callback compares against.
        self.assertEqual(sent, wema.security_info_value())

    @override_settings(WEMA={**WEMA_LIVE, "SECURITY_INFO": "  sec  "})
    def test_configured_security_info_wins_and_is_trimmed(self):
        self.assertEqual(wema.security_info_value(), "sec")

    @patch("utility.wema.requests.post", side_effect=requests.Timeout("socket timed out"))
    def test_transfer_timeout_is_ambiguous_and_never_refundable(self, _mock_post):
        result = wema.transfer(
            1000, "REF-TIMEOUT", "test", source_account="01",
            destination_account="02", destination_bank_code="035",
            destination_bank_name="Wema", destination_name="ADA",
        )
        self.assertFalse(result["success"])
        self.assertTrue(result["pending"])
        self.assertNotIn("socket timed out", result["message"])

    @patch("utility.wema.requests.post")
    def test_transfer_envelope_success_does_not_override_failed_status(self, mock_post):
        mock_post.return_value = _resp(
            {"result": {"status": "DECLINED", "transactionReference": "REF-BAD"},
             "hasError": False}
        )
        result = wema.transfer(
            1000, "REF-BAD", "test", source_account="01",
            destination_account="02", destination_bank_code="035",
            destination_bank_name="Wema", destination_name="ADA",
        )
        self.assertFalse(result["success"])
        self.assertFalse(result["pending"])

    @patch("utility.wema.requests.post")
    def test_transfer_unknown_accepted_status_remains_pending(self, mock_post):
        mock_post.return_value = _resp(
            {"result": {"status": "QUEUED_AT_SWITCH", "transactionReference": "REF-WAIT"},
             "hasError": False}
        )
        result = wema.transfer(
            1000, "REF-WAIT", "test", source_account="01",
            destination_account="02", destination_bank_code="035",
            destination_bank_name="Wema", destination_name="ADA",
        )
        self.assertFalse(result["success"])
        self.assertTrue(result["pending"])

    @patch("utility.wema.requests.get")
    def test_status_requery_pending_is_not_delivery_success(self, mock_get):
        mock_get.return_value = _resp(
            {"result": {"data": {"status": "PENDING",
                                    "transactionReference": "REF-WAIT"}},
             "hasError": False}
        )
        result = wema.confirm_transfer_status("REF-WAIT")
        self.assertFalse(result["success"])
        self.assertTrue(result["pending"])


class WemaKycTests(SimpleTestCase):
    """ALAT has NO standalone BVN/NIN/vNIN lookup — identity is verified by the
    name-matched NUBAN account-creation flow (see wallet.views.wema_wallet_verify_otp
    + the provisioning test). holder_name_mismatch is that name-match control; the
    verify_* entry points only format-check and (in production) redirect the caller to
    account setup. Identity never mock-passes in production."""

    def test_holder_name_match_is_order_tolerant(self):
        # Same person, order + extra middle name -> NOT a mismatch.
        self.assertFalse(wema.holder_name_mismatch("Eze Ada Chidinma", "ADA EZE"))

    def test_holder_name_clear_mismatch(self):
        # No shared tokens -> a clear mismatch (blocks the provisioning tier lift).
        self.assertTrue(wema.holder_name_mismatch("Ada Eze", "JOHN DOE"))

    def test_holder_name_missing_side_fails_closed(self):
        self.assertTrue(wema.holder_name_mismatch("", "JOHN DOE"))
        self.assertTrue(wema.holder_name_mismatch("Ada Eze", ""))

    def test_one_shared_token_is_not_enough_for_kyc(self):
        self.assertTrue(wema.holder_name_mismatch("Ada Eze", "Ada Johnson"))

    def test_verify_format_checks(self):
        self.assertFalse(wema.verify_bvn("123")["success"])            # not 11 digits
        self.assertFalse(wema.verify_nin("12ab5678901")["success"])   # non-digit
        self.assertFalse(wema.verify_vnin("short")["success"])        # not 16 chars

    def test_verify_mock_passes_in_dev(self):
        # dev/tests keep the mock so the offline KYC flow still exercises.
        self.assertTrue(wema.verify_bvn("22222222222")["success"])
        self.assertTrue(wema.verify_nin("12345678901")["success"])
        self.assertTrue(wema.verify_vnin("1234567890123456")["success"])

    @override_settings(DEBUG=False, TESTING=False)
    def test_verify_redirects_to_account_setup_in_prod(self):
        # No standalone lookup on ALAT: production routes the user to account setup
        # (otp_required) instead of hitting a non-existent verify endpoint.
        r = wema.verify_bvn("22222222222", name="Ada Eze")
        self.assertFalse(r["success"])
        self.assertTrue(r["otp_required"])
        self.assertFalse(wema.verify_nin("12345678901")["success"])
        self.assertFalse(wema.verify_vnin("1234567890123456")["success"])


class WemaProbeTests(SimpleTestCase):
    def test_probe_without_keys_makes_no_live_call(self):
        # No keys configured -> config + hint only, never raises.
        r = wema.wema_probe()
        self.assertIn("config", r)
        self.assertIn("hint", r)
        self.assertNotIn("banks", r)

    @override_settings(WEMA=WEMA_VAS)
    @patch("utility.wema.requests.get")
    def test_probe_runs_auth_checks_with_keys(self, mock_get):
        # GetAllBanks (debit product) uses the {result, hasError} envelope.
        mock_get.return_value = _resp({"result": [{"bankName": "Wema Bank", "bankCode": "035"}],
                                       "hasError": False})
        r = wema.wema_probe()
        self.assertIn("banks", r)
        self.assertTrue(r["banks"]["ok"])
        self.assertIn("airtime_product", r)


class WemaDiagnoseViewTests(SimpleTestCase):
    """Mutating diagnostics use authenticated POST; no secrets/PII in URLs."""

    def setUp(self):
        self.client = Client()
        self._patch = patch.dict(os.environ, {"WEMA_DIAG_TOKEN": "test-token"})
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_forwards_otp_and_tracking_id_to_probe(self):
        with patch("utility.wema.wema_probe", return_value={}) as mock_probe:
            self.client.post("/wema-diagnose", data=json.dumps({
                "phone": "08030000000", "otp": "123456", "tracking_id": "WEMA-TRK-1",
            }), content_type="application/json", HTTP_AUTHORIZATION="Bearer test-token")
        mock_probe.assert_called_once_with("", "", "08030000000", bvn="", nin="",
                                           otp="123456", tracking_id="WEMA-TRK-1")

    def test_head_is_refused_so_a_prefetch_cannot_spend_the_otp(self):
        # Django runs a function view for HEAD exactly as for GET, and this endpoint
        # starts account creation, sends an OTP and validates one. An OTP is
        # single-use, so an automatic HEAD (browser prefetch, Slack unfurl) would
        # consume it and the operator would see a failure for a step that already
        # succeeded. Observed in production 2026-07-28.
        with patch("utility.wema.wema_probe", return_value={}) as mock_probe:
            r = self.client.head("/wema-diagnose", HTTP_AUTHORIZATION="Bearer test-token")
        self.assertEqual(r.status_code, 405)
        mock_probe.assert_not_called()          # no live bank call was made

    def test_get_is_refused_and_query_token_is_never_accepted(self):
        self.assertEqual(self.client.get("/wema-diagnose", {"token": "test-token"}).status_code, 405)

    def test_diag_token_also_opens_it(self):
        # It used to read WEMA_DIAG_TOKEN alone, so a deploy that set only DIAG_TOKEN
        # got a 404 here while the other three probes worked — indistinguishable from
        # "the route isn't deployed", which is what these endpoints exist to answer.
        with patch.dict(os.environ, {"WEMA_DIAG_TOKEN": "", "DIAG_TOKEN": "shared"}), \
                patch("utility.wema.wema_probe", return_value={}):
            r = self.client.post("/wema-diagnose", data=b"{}", content_type="application/json",
                                 HTTP_AUTHORIZATION="Bearer shared")
        self.assertEqual(r.status_code, 200)

    def test_disabled_only_when_neither_token_is_set(self):
        with patch.dict(os.environ, {"WEMA_DIAG_TOKEN": "", "DIAG_TOKEN": ""}):
            self.assertEqual(self.client.post("/wema-diagnose", data=b"{}",
                                              content_type="application/json").status_code, 404)

    def test_trailing_slash_also_resolves(self):
        with patch("utility.wema.wema_probe", return_value={}):
            r = self.client.post("/wema-diagnose/", data=b"{}", content_type="application/json",
                                 HTTP_AUTHORIZATION="Bearer test-token")
        self.assertEqual(r.status_code, 200)

    def test_missing_bvn_or_phone_skips_wallet_create(self):
        # Mirrors the real report: hitting the URL without both phone and bvn must
        # not silently attempt wallet creation (wema_probe itself already guards
        # this — asserted here at the view boundary too).
        with patch("utility.wema.wema_probe", return_value={}) as mock_probe:
            self.client.post("/wema-diagnose", data=b"{}", content_type="application/json",
                             HTTP_AUTHORIZATION="Bearer test-token")
        mock_probe.assert_called_once_with("", "", "", bvn="", nin="", otp="", tracking_id="")


@override_settings(WEMA=WEMA_VAS)
class WemaVasLiveTests(SimpleTestCase):
    @patch("utility.wema.requests.post")
    def test_airtime_live_sends_nuban_and_securityinfo(self, mock_post):
        mock_post.return_value = _resp({"result": {"status": "SUCCESS", "transactionReference": "R"},
                                        "hasError": False})
        r = wema.purchase_airtime(500, "R", "08030000000", "MTN", source_account="0155500011")
        self.assertTrue(r["success"])
        body = mock_post.call_args[1]["json"]
        self.assertEqual(body["accountNumber"], "0155500011")   # debits the user's own NUBAN
        self.assertEqual(body["securityInfo"], "sec")
        self.assertEqual(mock_post.call_args[1]["headers"]["access"], "chan-1")   # VAS uses `access`
        self.assertEqual(mock_post.call_args[1]["headers"]["Ocp-Apim-Subscription-Key"], "airkey")
        self.assertTrue(mock_post.call_args[0][0].endswith("/airtime-data/api/Airtime/Client/PurchaseAirtime"))

    @patch("utility.wema.requests.post")
    def test_airtime_pending_status_maps_to_pending(self, mock_post):
        mock_post.return_value = _resp({"result": {"status": "PROCESSING"}, "hasError": False})
        r = wema.purchase_airtime(500, "R", "08030000000", "MTN", source_account="0155500011")
        self.assertFalse(r["success"])
        self.assertTrue(r["pending"])       # never refund a maybe-delivered buy

    @patch("utility.wema.requests.post")
    def test_vas_status_integer_code_stays_pending(self, mock_post):
        # The PartnerPayment status-check returns an INTEGER transactionStatus whose
        # legend ALAT doesn't publish -> pending (never auto-settle/refund). The
        # requery also sends transactionType as an integer (1 = airtime).
        mock_post.return_value = _resp({"result": {"transactionReference": "R", "transactionStatus": 3},
                                        "hasError": False})
        r = wema.vas_status("R", "airtime")
        self.assertFalse(r["success"])
        self.assertTrue(r["pending"])
        self.assertEqual(mock_post.call_args[1]["json"]["transactionType"], 1)

    @patch("utility.wema.requests.post")
    def test_vas_status_string_success_settles(self, mock_post):
        mock_post.return_value = _resp({"result": {"status": "SUCCESS", "transactionReference": "R"},
                                        "hasError": False})
        self.assertTrue(wema.vas_status("R", "airtime")["success"])


class WemaCatalogueTests(SimpleTestCase):
    """The ALAT data/bills catalogues are nested; the client flattens them to rows
    keyed by the id PurchaseData / PayBill expect."""

    def test_flatten_data_plans_nested(self):
        result = [{"networkProvider": "MTN", "dataPackages": [
            {"id": 101, "name": "1GB Daily", "amount": 350, "validity_Period": "1 day"},
            {"id": 102, "name": "2GB", "amount": 600}]},
            {"networkProvider": "Glo", "dataPackages": [{"id": 201, "name": "1.5GB", "amount": 500}]}]
        rows = wema._flatten_data_plans(result)
        self.assertEqual(len(rows), 3)
        mtn = [r for r in rows if r["network"] == "MTN"][0]
        self.assertEqual(mtn["code"], "101")            # code = dataPackage id
        self.assertEqual(mtn["amount"], 350)
        self.assertEqual(len(wema._flatten_data_plans(result, "glo")), 1)   # network filter

    def test_flatten_bills_nested(self):
        result = [{"id": 1, "name": "Cable TV", "billers": [
            {"id": 10, "name": "DStv", "identifier": "smartcard", "requiredValidation": True,
             "charge": 100, "packages": [
                 {"id": 1001, "name": "DStv Compact", "amount": 10500},
                 {"id": 1002, "name": "DStv Premium", "amount": 24500}]},
            {"id": 11, "name": "Ikeja Electric", "packages": []}]}]
        rows = wema._flatten_bills(result)
        self.assertEqual(len(rows), 3)                  # 2 DStv packages + biller-without-packages
        compact = [r for r in rows if r["name"] == "DStv Compact"][0]
        self.assertEqual(compact["code"], "1001")       # code = package id
        self.assertEqual(compact["biller"], "DStv")
        self.assertEqual(compact["category"], "Cable TV")
        ie = [r for r in rows if r["biller"] == "Ikeja Electric"][0]
        self.assertEqual(ie["code"], "11")              # no packages -> biller id

    @override_settings(WEMA=WEMA_VAS)
    @patch("utility.wema.requests.get")
    def test_get_data_plans_flattens_live_and_sends_no_network_param(self, mock_get):
        mock_get.return_value = _resp({"result": [
            {"networkProvider": "MTN", "dataPackages": [{"id": 101, "name": "1GB", "amount": 350}]}],
            "hasError": False})
        r = wema.get_data_plans("MTN")
        self.assertTrue(r["success"])
        self.assertEqual(r["plans"][0]["code"], "101")
        # GetDataPlans takes no network query param (all networks come back nested).
        self.assertTrue(mock_get.call_args[0][0].endswith("/airtime-data/api/Data/GetDataPlans"))
        self.assertFalse(mock_get.call_args[1].get("params"))


class WemaStatusLegendTests(SimpleTestCase):
    def test_failed_pending_are_unsettled(self):
        for st in ("Failed", "Pending", "Reversed", "Declined"):
            n = wema.normalize_transaction({"referenceId": "R", "amount": "100",
                                            "creditType": "Credit", "status": st})
            self.assertFalse(n["settled"], st)

    def test_success_or_absent_is_settled(self):
        for st in ("Successfull", "Default", ""):
            n = wema.normalize_transaction({"referenceId": "R", "amount": "100",
                                            "creditType": "Credit", "status": st})
            self.assertTrue(n["settled"], repr(st))


WEMA_CARD = {**WEMA_LIVE, "KEYS": {"wallet": "subkey", "card": "cardkey"},
             "CARD_PRODUCT_KEY": "cardprod"}


@override_settings(WEMA=WEMA_CARD)
class WemaCardTests(SimpleTestCase):
    """The virtual card is keyed by the NUBAN and lives on the real card-management
    product; reversible freeze / incremental top-up aren't offered by ALAT."""

    @patch("utility.wema.requests.post")
    def test_issue_uses_partnercard_endpoint_and_nuban(self, mock_post):
        mock_post.return_value = _resp({"status": True, "message": "ok",
                                        "data": {"maskedPan": "506100******1234", "expiry": "01/29"}})
        r = wema.card_issue("ADA EZE", "42", account_number="0155500011", email="a@b.com")
        self.assertTrue(r["success"])
        self.assertEqual(r["card_token"], "0155500011")     # keyed by NUBAN
        self.assertEqual(r["last4"], "1234")
        body = mock_post.call_args[1]["json"]
        self.assertEqual(body["accountNo"], "0155500011")
        self.assertEqual(body["cardKey"], "cardprod")
        self.assertEqual(mock_post.call_args[1]["headers"]["x-api-key"], "chan-1")   # card uses x-api-key
        self.assertTrue(mock_post.call_args[0][0].endswith(
            "/card-management/api/Partner/partnerCard/virtualCard"))

    def test_issue_requires_account_number(self):
        self.assertFalse(wema.card_issue("ADA EZE", "42")["success"])   # no NUBAN -> fail closed

    @patch("utility.wema.requests.get")
    def test_reveal_uses_account_details_endpoint(self, mock_get):
        mock_get.return_value = _resp({"status": True,
                                       "data": {"cardPan": "5061000000001234", "cvv": "123"}})
        r = wema.card_reveal("0155500011")
        self.assertTrue(r["success"])
        self.assertEqual(r["cvv"], "123")
        self.assertTrue(mock_get.call_args[0][0].endswith(
            "/api/Partner/partnerCard/virtual-card-details/0155500011"))

    @patch("utility.wema.requests.post")
    def test_block_hotlists_by_account(self, mock_post):
        mock_post.return_value = _resp({"successful": True, "message": "blocked"})
        r = wema.card_set_status("0155500011", active=False, masked_pan="506100******1234")
        self.assertTrue(r["success"])
        self.assertEqual(mock_post.call_args[1]["params"]["accountNumber"], "0155500011")
        self.assertTrue(mock_post.call_args[0][0].endswith("/api/Partner/partnerCard/hotlistCard"))

    def test_unfreeze_and_topup_report_unsupported(self):
        # ALAT virtual cards have no reversible freeze / incremental top-up.
        self.assertFalse(wema.card_set_status("0155500011", active=True)["success"])
        self.assertFalse(wema.card_fund("0155500011", 5000)["success"])


@override_settings(WEMA=WEMA_LIVE)
class WemaAccountLifecycleTests(SimpleTestCase):
    @patch("utility.wema.requests.post")
    def test_lift_debit_restriction(self, mock_post):
        mock_post.return_value = _resp({"status": True, "message": "PND lifted"})
        r = wema.lift_debit_restriction("0155500011", bvn=True)
        self.assertTrue(r["success"])
        body = mock_post.call_args[1]["json"]
        self.assertEqual(body["pndType"], "LiftPnd")
        self.assertEqual(body["accountNumber"], "0155500011")
        self.assertTrue(mock_post.call_args[0][0].endswith(
            "/account-creation/api/CustomerAccount/PartnerDebitRestrictionManagement"))

    @patch("utility.wema.requests.get")
    def test_get_kyc_status(self, mock_get):
        mock_get.return_value = _resp({"status": True, "data": {
            "accountTier": "Tier 1", "accountStatus": "Active", "restrictionStatus": "None",
            "addressVerificationStatus": "Pending", "accountName": "ADA EZE"}})
        r = wema.get_kyc_status("0155500011")
        self.assertTrue(r["success"])
        self.assertEqual(r["tier"], "Tier 1")
        self.assertEqual(r["restriction_status"], "None")
        self.assertTrue(mock_get.call_args[0][0].endswith(
            "/account-upgrade/api/partnership/partner-account-kyc-status"))

    @patch("utility.wema.requests.post")
    def test_upgrade_tier2_sends_identity(self, mock_post):
        mock_post.return_value = _resp({"status": True, "message": "ok"})
        r = wema.upgrade_tier2("0155500011", bvn="22222222222", nin="12345678901", live_image="b64")
        self.assertTrue(r["success"])
        body = mock_post.call_args[1]["json"]
        self.assertEqual(body["accountNumber"], "0155500011")
        self.assertEqual(body["bvn"], "22222222222")
        self.assertTrue(mock_post.call_args[0][0].endswith(
            "/account-upgrade/api/partnership/partner-account-upgrade-tier2"))

    @patch("utility.wema.requests.post")
    def test_upgrade_tier3_maps_residential_address(self, mock_post):
        mock_post.return_value = _resp({"status": True})
        r = wema.upgrade_tier3("0155500011", {"fullAddress": "1 Marina, Lagos", "state": "Lagos"})
        self.assertTrue(r["success"])
        addr = mock_post.call_args[1]["json"]["residentialAddress"]
        self.assertEqual(addr["fullAddress"], "1 Marina, Lagos")
        self.assertEqual(addr["country"], "Nigeria")   # defaulted


@override_settings(WEMA=WEMA_LIVE)
class WemaNipChargesTests(SimpleTestCase):
    @patch("utility.wema.requests.get")
    def test_charges_and_fee_bands(self, mock_get):
        mock_get.return_value = _resp({"result": {"chargeFees": [
            {"chargeFeeName": "<=5000", "transactionType": 1, "charge": 10, "lower": 0, "upper": 5000},
            {"chargeFeeName": "5001-50000", "transactionType": 1, "charge": 25, "lower": 5001, "upper": 50000},
            {"chargeFeeName": ">50000", "transactionType": 1, "charge": 50, "lower": 50001, "upper": 0}]},
            "hasError": False})
        r = wema.get_nip_charges()
        self.assertTrue(r["success"])
        charges = r["charges"]
        self.assertEqual(wema.nip_fee_for(3000, charges), Decimal("10.00"))
        self.assertEqual(wema.nip_fee_for(20000, charges), Decimal("25.00"))
        self.assertEqual(wema.nip_fee_for(100000, charges), Decimal("50.00"))  # open-ended upper
        self.assertTrue(mock_get.call_args[0][0].endswith("/debit-wallet/api/Shared/GetNIPCharges"))

    def test_nip_fee_no_band_returns_none(self):
        bands = [{"lower": Decimal("500"), "upper": Decimal("1000"), "charge": Decimal("5")}]
        self.assertIsNone(wema.nip_fee_for(100, bands))


WEMA_REMITA = {**WEMA_LIVE, "KEYS": {"wallet": "subkey", "remita": "remitakey"},
               "SOURCE_ACCOUNT": "0100000001"}


@override_settings(WEMA=WEMA_REMITA)
class WemaRemitaTests(SimpleTestCase):
    @patch("utility.wema.requests.get")
    def test_validate_rrr(self, mock_get):
        mock_get.return_value = _resp({"result": {"isValidated": True, "name": "ADA EZE",
                                                  "amount": "5000"}, "hasError": False})
        r = wema.validate_rrr("120000000001")
        self.assertTrue(r["success"])
        self.assertEqual(r["amount"], Decimal("5000.00"))
        self.assertTrue(mock_get.call_args[0][0].endswith(
            "/remita-payment/api/RemitaPayment/ValidateRrr/120000000001/chan-1"))

    @patch("utility.wema.requests.post")
    def test_pay_remita_sends_rrr_securityinfo_nuban(self, mock_post):
        mock_post.return_value = _resp({"result": {"status": "SUCCESS", "transactionReference": "R"},
                                        "hasError": False})
        r = wema.pay_remita(5000, "R", rrr="120000000001", source_account="0155500011", name="ADA")
        self.assertTrue(r["success"])
        body = mock_post.call_args[1]["json"]
        self.assertEqual(body["rrr"], "120000000001")
        self.assertEqual(body["customerAccount"], "0155500011")   # debits the user's NUBAN
        self.assertEqual(body["securityInfo"], "sec")
        self.assertEqual(mock_post.call_args[1]["headers"]["access"], "chan-1")   # remita uses `access`
        self.assertTrue(mock_post.call_args[0][0].endswith(
            "/remita-payment/api/RemitaPayment/ProcessRemitaPayment"))

    def test_vas_status_remita_stays_pending(self):
        # No Remita status endpoint on ALAT -> a timed-out payment stays pending
        # (never mis-routed to the airtime status endpoint / auto-settled).
        r = wema.vas_status("R", "remita")
        self.assertFalse(r["success"])
        self.assertTrue(r["pending"])


WEMA_BNPL = {**WEMA_LIVE, "KEYS": {"wallet": "subkey", "bnpl": "bnplkey"},
             "BNPL_MERCHANT_ID": "merch-1", "BNPL_AUTH_KEY": "merch-auth"}


@override_settings(WEMA=WEMA_BNPL)
class WemaBnplTests(SimpleTestCase):
    @patch("utility.wema.requests.get")
    def test_offers_uses_merchant_auth(self, mock_get):
        mock_get.return_value = _resp([{"productId": 1, "productName": "BNPL 30d", "maximumTenor": 30}])
        r = wema.bnpl_offers()
        self.assertTrue(r["success"])
        self.assertEqual(r["offers"][0]["productName"], "BNPL 30d")
        h = mock_get.call_args[1]["headers"]
        self.assertEqual(h["x-merchant-id"], "merch-1")
        self.assertEqual(h["x-merchant-authorization-key"], "merch-auth")
        self.assertEqual(h["Ocp-Apim-Subscription-Key"], "bnplkey")
        self.assertNotIn("access", h)      # BNPL does NOT use the channel header
        self.assertTrue(mock_get.call_args[0][0].endswith("/alat-bnpl/api/Eligibility/productoffers"))

    @patch("utility.wema.requests.post")
    def test_consent_returns_eligibility_id(self, mock_post):
        mock_post.return_value = _resp({"successful": True, "message": "ok",
                                        "response": {"accountName": "ADA", "eligibilityId": "E-1"}})
        r = wema.bnpl_consent("0155500011", 20000, 30, "CUST-1")
        self.assertTrue(r["success"])
        self.assertEqual(r["eligibility_id"], "E-1")
        body = mock_post.call_args[1]["json"]
        self.assertEqual(body["productAmount"], 20000.0)
        self.assertEqual(body["tenor"], 30)
        self.assertTrue(mock_post.call_args[0][0].endswith("/alat-bnpl/api/Eligibility/ConsentRequest"))

    def test_bnpl_live_requires_merchant_creds(self):
        self.assertTrue(wema._bnpl_live())
        with override_settings(WEMA={**WEMA_BNPL, "BNPL_MERCHANT_ID": ""}):
            self.assertFalse(wema._bnpl_live())


class WemaSubscriptionKeyTests(SimpleTestCase):
    """The ALAT Wallet Services subscription bundles 13 APIs — including Airtime and
    Data, Bills Payment, Remita-Payment and Partnership Account KYC — so none of those
    needs its own key. A dedicated per-product key still wins if Wema issues one, and
    products that ARE their own subscription never borrow the wallet key."""

    @override_settings(WEMA=WEMA_LIVE)  # wallet keyed; the rest blank
    def test_bundled_products_fall_back_to_wallet_key(self):
        # `card` is included: the portal has one Card Management API and the
        # virtual-card operations are part of it, so the wallet key authenticates it.
        for product in ("airtime", "bills", "remita", "kyc", "card"):
            self.assertEqual(wema._sub_key(product), "subkey", product)

    @override_settings(WEMA=WEMA_LIVE)
    def test_wallet_covered_products_unchanged(self):
        for product in ("wallet_nin", "wallet_bvn", "acct_mgt", "upgrade", "credit", "debit"):
            self.assertEqual(wema._sub_key(product), "subkey", product)

    @override_settings(WEMA=WEMA_LIVE)
    def test_own_subscription_products_never_borrow_wallet_key(self):
        # BNPL is genuinely its own APIM subscription with its own merchant auth.
        # Unkeyed it must resolve empty so the rail stays disabled rather than
        # silently authenticating on a key that does not cover it.
        self.assertEqual(wema._sub_key("bnpl"), "")

    @override_settings(WEMA=WEMA_VAS)  # dedicated airtime/bills keys present
    def test_dedicated_key_wins_over_wallet_fallback(self):
        self.assertEqual(wema._sub_key("airtime"), "airkey")
        self.assertEqual(wema._sub_key("bills"), "billkey")

    @override_settings(WEMA=WEMA_LIVE)
    def test_vas_live_on_wallet_key_alone(self):
        for product in ("airtime", "bills", "remita"):
            self.assertTrue(wema._vas_live(product), product)

    @override_settings(WEMA=WEMA_LIVE)
    def test_card_authenticates_on_wallet_key_but_is_not_auto_selected(self):
        # The wallet key DOES authenticate Card-Management (the product is bundled),
        # but that alone must not move the card backend onto the Wema rail, which has
        # no reversible freeze and no top-up. Auto-selection needs a dedicated key.
        self.assertTrue(wema._card_live())
        self.assertFalse(wema.card_opted_in())

    @override_settings(WEMA=WEMA_CARD)  # dedicated WEMA_CARD_KEY present
    def test_dedicated_card_key_opts_in(self):
        self.assertTrue(wema._card_live())
        self.assertTrue(wema.card_opted_in())


@override_settings(WEMA=WEMA_LIVE)
class WemaWalletCreateEnvelopeTests(SimpleTestCase):
    """ALAT answers wallet creation with status=True even when it provisions nothing
    and returns no OTP tracking id. Without that id the OTP round-trip cannot be
    completed, so such a response must not be reported as success."""

    @patch("utility.wema.requests.post")
    def test_ok_without_tracking_id_is_not_success(self, mock_post):
        # Verbatim sandbox response for an identity that already has an ALAT account.
        mock_post.return_value = _resp({
            "data": None,
            "message": "Hi, Kindly download ALAT and sign in to see Wema Bank account details.",
            "status": True, "code": 0, "statusCode": 200, "errors": None,
        })
        r = wema.create_wallet_request("08030000000", "a@b.com", nin="12345678901")
        self.assertFalse(r["success"])
        self.assertEqual(r["tracking_id"], "")
        # White-label: our own wording, never the gateway's — which names the provider
        # and tells the customer to go and use the bank's app instead.
        self.assertNotIn("ALAT", r["message"].upper())
        self.assertIn("contact support", r["message"])
        # the untouched envelope is still available for logs / support
        self.assertIn("download ALAT", r["raw"]["message"])

    @patch("utility.wema.requests.post")
    def test_tracking_id_under_data_envelope_succeeds(self, mock_post):
        mock_post.return_value = _resp({
            "data": {"otpTrackingID": "TRK-1", "otpDestination": "0803*****00"},
            "message": "OTP sent", "status": True, "statusCode": 200,
        })
        r = wema.create_wallet_request("08030000000", "a@b.com", nin="12345678901")
        self.assertTrue(r["success"])
        self.assertEqual(r["tracking_id"], "TRK-1")


class WemaMessageWhiteLabelTests(SimpleTestCase):
    """Zitch never shows the upstream bank's brand to customers, so any gateway
    message naming the provider is replaced with a neutral string before it can
    reach a client. ~19 view call sites pass this message straight through, so the
    guard lives at the single extraction point rather than at each of them."""

    def test_provider_named_message_is_replaced(self):
        for text in ("Hi, Kindly download ALAT and sign in to see Wema Bank account details.",
                     "Please visit any WEMA BANK branch",
                     "Your ALATbyWEMA profile is incomplete"):
            self.assertEqual(wema._msg({"message": text}), "Request failed", text)

    def test_neutral_message_passes_through(self):
        # Genuinely useful gateway text must survive — it is what the customer needs.
        for text in ("Insufficient funds", "Invalid account number",
                     "Transaction has been escalated"):  # 'escalated' contains 'alat'
            self.assertEqual(wema._msg({"message": text}), text)

    def test_error_message_fields_are_also_scrubbed(self):
        self.assertEqual(wema._msg({"errorMessage": "Download the ALAT app"}), "Request failed")
        self.assertEqual(wema._msg({"errorMessages": ["Wema could not process this"]}),
                         "Request failed")

    def test_empty_envelope_still_defaults(self):
        self.assertEqual(wema._msg({}), "Request failed")


@override_settings(WEMA=WEMA_VAS)
class WemaConformanceFixTests(SimpleTestCase):
    """Fixes from the 2026-07-27 portal-conformance audit."""

    # --- bills packageId must go out as an int32, not a JSON string ---
    @patch("utility.wema.requests.post")
    def test_validate_customer_sends_integer_packageid(self, mock_post):
        mock_post.return_value = _resp({"result": {"customerName": "ADA"}, "hasError": False})
        wema.validate_bill_customer("1234567890", "42")   # code stored as a string
        self.assertEqual(mock_post.call_args[1]["json"]["packageId"], 42)  # int, not "42"

    @patch("utility.wema.requests.post")
    def test_pay_bill_sends_integer_packageid(self, mock_post):
        mock_post.return_value = _resp({"result": {"status": "SUCCESS"}, "hasError": False})
        wema.pay_bill(2000, "R", package_id="7", identifier="1234567890", source_account="01")
        self.assertEqual(mock_post.call_args[1]["json"]["packageId"], 7)

    def test_as_int_leaves_non_numeric_visible(self):
        # A blank/garbage wema_code must not be coerced to 0 — fail visibly instead.
        self.assertEqual(wema._as_int(""), "")
        self.assertEqual(wema._as_int("abc"), "abc")
        self.assertEqual(wema._as_int("15"), 15)

    # --- _parse_vas must not settle without a positive signal ---
    @patch("utility.wema.requests.post")
    def test_paybill_empty_result_stays_pending(self, mock_post):
        # hasError=false with no result payload is NOT a delivery confirmation.
        mock_post.return_value = _resp({"hasError": False, "result": None})
        r = wema.pay_bill(2000, "R", package_id="7", identifier="1", source_account="01")
        self.assertFalse(r["success"])
        self.assertTrue(r["pending"])       # re-queried, never auto-settled

    @patch("utility.wema.requests.post")
    def test_airtime_failed_status_refunds(self, mock_post):
        # A FAILED buy must refund (success=False, pending=False), not settle.
        mock_post.return_value = _resp({"result": {"status": "FAILED"}, "hasError": False})
        r = wema.purchase_airtime(500, "R", "08030000000", "MTN", source_account="01")
        self.assertFalse(r["success"])
        self.assertFalse(r["pending"])

    @patch("utility.wema.requests.post")
    def test_airtime_success_still_settles(self, mock_post):
        # Regression guard: a real success is unchanged.
        mock_post.return_value = _resp({"result": {"status": "SUCCESS"}, "hasError": False})
        self.assertTrue(wema.purchase_airtime(500, "R", "0803", "MTN", source_account="01")["success"])

    # --- _msg reads the account-creation errors[] array ---
    def test_msg_reads_errors_array(self):
        self.assertEqual(wema._msg({"message": None, "errors": ["Invalid OTP supplied"]}),
                         "Invalid OTP supplied")
        # message still wins when present
        self.assertEqual(wema._msg({"message": "Enter the OTP", "errors": ["x"]}), "Enter the OTP")

    def test_msg_survives_dotnet_validation_error_object(self):
        """ALAT is .NET: a 400 answers with ValidationProblemDetails, where `errors`
        is an object keyed by field, not an array. Indexing [0] raised KeyError: 0
        and 500'd the account callback in production on 2026-07-29."""
        data = {"errors": {"accountNumber": ["The accountNumber field is required."]},
                "title": "One or more validation errors occurred.", "status": 400}
        self.assertEqual(wema._msg(data), "The accountNumber field is required.")

    def test_msg_normalises_every_error_field_shape(self):
        """Whatever the gateway types these as, _msg returns a string and never raises
        — a message helper must not be able to take down a money path."""
        for field in ("message", "errorMessage", "errorMessages", "errors"):
            for value, expected in (
                ("Insufficient funds", "Insufficient funds"),       # bare string
                (["Insufficient funds"], "Insufficient funds"),     # array
                ({"amount": ["Insufficient funds"]}, "Insufficient funds"),  # .NET object
                ({"amount": "Insufficient funds"}, "Insufficient funds"),    # object of strings
                ([], "Request failed"),                             # empty
                ({}, "Request failed"),
                (None, "Request failed"),
            ):
                with self.subTest(field=field, value=value):
                    self.assertEqual(wema._msg({field: value}), expected)

    def test_msg_rejects_a_non_dict_envelope(self):
        """_ok already guards this; _msg did not, so a bare list or string body would
        AttributeError instead of degrading to the neutral message."""
        for body in ([], "", "boom", ["boom"], None, 0):
            with self.subTest(body=body):
                self.assertEqual(wema._msg(body), "Request failed")

    def test_msg_still_strips_the_provider_brand_from_nested_shapes(self):
        """White-labelling must survive normalisation: a branded string reached
        through a dict or array is dropped exactly as a top-level one is."""
        self.assertEqual(wema._msg({"errors": {"acct": ["Download the ALAT app"]}}),
                         "Request failed")
        self.assertEqual(wema._msg({"errorMessages": ["Wema could not process this"]}),
                         "Request failed")
