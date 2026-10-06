"""The retained biller product never inherits a legacy settlement source.

Funding approval itself is tested by the wallet layer. These boundary tests give
the resolver a canonical source and prove that every transport uses it, while a
refusal cannot reach either a real purchase or the simulation success path.
"""
from decimal import Decimal
from unittest import mock

import requests
from django.test import SimpleTestCase, override_settings

from utility import providers, wema
from wallet.services import LimitExceeded


LIVE = {
    "BASE_URL": "https://bank.example", "CHANNEL_ID": "test-channel",
    "KEYS": {"wallet": "test-wallet", "airtime": "test-airtime",
             "bills": "test-bills", "remita": "test-remita"},
    "SECURITY_INFO": "test-security", "SIMULATION": False,
    "SOURCE_ACCOUNT": "0100000001",
    "VAS_STATUS_LEGEND": "200=success 400=failed",
    "BILLS_STATUS_LEGEND": "200=success 400=failed",
    "REMITA_STATUS_LEGEND": "200=success 400=failed",
}
APPROVED_SOURCE = "0200000002"
SUPPLIED_SOURCE = "0300000003"
AMOUNT = Decimal("500.00")
REFERENCE = "biller-bound-reference"
RESOLVER = "wallet.services.biller_source_for_transaction"


def response(body):
    result = mock.Mock(status_code=200, content=b"{}")
    result.json.return_value = body
    return result


def purchases():
    return (
        (wema.purchase_airtime, (AMOUNT, REFERENCE, "08000000000", "MTN"), {},
         "airtime", "/api/Airtime/Client/PurchaseAirtime", "accountNumber"),
        (wema.purchase_data, (AMOUNT, REFERENCE, "08000000000", "MTN", "7"), {},
         "airtime", "/api/Data/Client/PurchaseData", "accountNumber"),
        (wema.pay_bill, (AMOUNT, REFERENCE), {"package_id": "7", "identifier": "meter"},
         "bills", "/api/Shared/PayBill", "customerAccount"),
        (wema.pay_remita, (AMOUNT, REFERENCE), {"rrr": "123456789"},
         "remita", "/api/RemitaPayment/ProcessRemitaPayment", "customerAccount"),
    )


@override_settings(WEMA=LIVE, WEMA_PARTNERSHIP_MODE="archive",
                   BANK_ACCOUNT_PROVIDER="wema_vas", WEMA_BILLER_MODE="active")
class RetainedBillerBoundaryTests(SimpleTestCase):
    def test_every_direct_purchase_uses_the_resolvers_canonical_source(self):
        for function, args, kwargs, product, path, source_field in purchases():
            with self.subTest(operation=function.__name__), \
                    mock.patch(RESOLVER, return_value=APPROVED_SOURCE) as resolve, \
                    mock.patch.object(wema, "_post", return_value=response({
                        "hasError": False, "result": {"status": "SUCCESS"},
                    })) as post:
                result = function(*args, **kwargs, source_account=SUPPLIED_SOURCE)
            self.assertTrue(result["success"])
            resolve.assert_called_once_with(
                REFERENCE, amount=AMOUNT, source_account=SUPPLIED_SOURCE)
            post.assert_called_once()
            self.assertEqual(post.call_args.args[:2], (product, path))
            self.assertEqual(post.call_args.args[2][source_field], APPROVED_SOURCE)
            self.assertNotEqual(post.call_args.args[2][source_field], LIVE["SOURCE_ACCOUNT"])

    def test_omitted_source_is_resolved_and_never_inherits_the_legacy_pool(self):
        for function, args, kwargs, _product, _path, source_field in purchases():
            with self.subTest(operation=function.__name__), \
                    mock.patch(RESOLVER, return_value=APPROVED_SOURCE) as resolve, \
                    mock.patch.object(wema, "_post", return_value=response({
                        "hasError": False, "result": {"status": "SUCCESS"},
                    })) as post:
                result = function(*args, **kwargs)
            self.assertTrue(result["success"])
            resolve.assert_called_once_with(REFERENCE, amount=AMOUNT, source_account="")
            self.assertEqual(post.call_args.args[2][source_field], APPROVED_SOURCE)

    def test_source_refusal_stops_every_direct_purchase_before_network(self):
        for function, args, kwargs, _product, _path, _field in purchases():
            with self.subTest(operation=function.__name__), \
                    mock.patch(RESOLVER, side_effect=LimitExceeded("Funding approval required")), \
                    mock.patch.object(wema, "_post") as post, \
                    mock.patch.object(wema, "_get") as get:
                result = function(*args, **kwargs, source_account=SUPPLIED_SOURCE)
            self.assertFalse(result["success"])
            self.assertFalse(result.get("pending"))
            self.assertTrue(result["not_charged"])
            self.assertEqual(result["code"], "biller_funding_unavailable")
            post.assert_not_called()
            get.assert_not_called()

    @override_settings(WEMA={**LIVE, "SIMULATION": True})
    def test_simulation_cannot_bypass_source_refusal(self):
        self.test_source_refusal_stops_every_direct_purchase_before_network()

    def test_archive_still_blocks_account_and_transfer_instructions(self):
        with mock.patch(RESOLVER) as resolve, mock.patch.object(wema, "_post") as post:
            account = wema.create_wallet_request("08000000000", "customer@example.com",
                                                 bvn="11111111111")
            transfer = wema.transfer(
                AMOUNT, REFERENCE, "Transfer", source_account=APPROVED_SOURCE,
                destination_account="0400000004", destination_bank_code="035",
                destination_bank_name="Wema", destination_name="Customer")
        for result in (account, transfer):
            self.assertFalse(result["success"])
            self.assertEqual(result["code"], "partnership_archived")
        resolve.assert_not_called()
        post.assert_not_called()

    def test_ambiguous_purchase_stays_pending_after_source_approval(self):
        for function, args, kwargs, _product, _path, _field in purchases():
            with self.subTest(operation=function.__name__), \
                    mock.patch(RESOLVER, return_value=APPROVED_SOURCE), \
                    mock.patch.object(wema, "_post", side_effect=requests.Timeout("response lost")):
                result = function(*args, **kwargs)
            self.assertFalse(result["success"])
            self.assertTrue(result["pending"])
            self.assertFalse(result.get("not_charged", False))

    def test_airtime_wrapper_passes_the_approved_source(self):
        with mock.patch(RESOLVER, return_value=APPROVED_SOURCE) as resolve, \
                mock.patch.object(wema, "purchase_airtime", return_value={
                    "success": True, "status": "SUCCESS",
                }) as purchase:
            result = providers.vtu_purchase("mtn-airtime", {
                "amount": AMOUNT, "phone": "08000000000", "source_account": SUPPLIED_SOURCE,
            }, REFERENCE)
        self.assertTrue(result["success"])
        resolve.assert_called_once_with(REFERENCE, amount=AMOUNT, source_account=SUPPLIED_SOURCE)
        purchase.assert_called_once_with(AMOUNT, REFERENCE, "08000000000", "MTN",
                                         source_account=APPROVED_SOURCE)

    def test_wrappers_refuse_missing_source_approval_before_transport(self):
        with mock.patch(RESOLVER, side_effect=LimitExceeded("Funding approval required")), \
                mock.patch.object(wema, "purchase_airtime") as airtime, \
                mock.patch.object(wema, "pay_remita") as remita:
            results = (
                providers.vtu_purchase("mtn-airtime", {
                    "amount": AMOUNT, "phone": "08000000000",
                }, REFERENCE),
                providers.remita_pay(AMOUNT, REFERENCE, rrr="123456789"),
            )
        for result in results:
            self.assertFalse(result["success"])
            self.assertTrue(result["not_charged"])
            self.assertEqual(result["code"], "biller_funding_unavailable")
        airtime.assert_not_called()
        remita.assert_not_called()

    def test_live_remita_wrapper_still_requires_a_requery_contract(self):
        with mock.patch(RESOLVER, return_value=APPROVED_SOURCE), \
                mock.patch.object(wema, "pay_remita") as purchase:
            result = providers.remita_pay(AMOUNT, REFERENCE, rrr="123456789")
        self.assertFalse(result["success"])
        self.assertFalse(result["pending"])
        self.assertTrue(result["not_charged"])
        self.assertEqual(result["code"], "remita_unavailable")
        purchase.assert_not_called()

    def test_disabled_or_unknown_lifecycle_refuses_new_bills_before_source_resolution(self):
        for mode in ("disabled", "archive", "typo", ""):
            with self.subTest(mode=mode), override_settings(WEMA_BILLER_MODE=mode), \
                    mock.patch(RESOLVER) as resolve, mock.patch.object(wema, "_post") as post:
                for function, args, kwargs, _product, _path, _field in purchases():
                    result = function(*args, **kwargs)
                    self.assertFalse(result["success"])
                    self.assertFalse(result["pending"])
                    self.assertTrue(result["not_charged"])
                    self.assertEqual(result["code"], "biller_unavailable")
            resolve.assert_not_called()
            post.assert_not_called()

    @override_settings(WEMA_BILLER_MODE="disabled")
    def test_catalogue_validation_receipt_and_requery_survive_biller_disable(self):
        with mock.patch(RESOLVER) as resolve, mock.patch.object(wema, "_get", side_effect=[
                response({"hasError": False, "result": []}),
                response({"hasError": False, "result": []}),
                response({"hasError": False, "result": {
                    "isValidated": True, "customerName": "Ada Eze", "amount": "500.00",
                }}),
                response({"hasError": False, "result": {"receipt": "historical receipt"}}),
            ]) as get, mock.patch.object(wema, "_post", side_effect=[
                response({"hasError": False, "result": {"customerName": "Ada Eze"}}),
                response({"hasError": False, "result": {"transactionStatus": 200}}),
                response({"hasError": False, "result": {"transactionStatus": 200}}),
            ]) as post:
            results = (
                wema.get_data_plans("MTN"),
                wema.get_bills(),
                wema.validate_rrr("123456789"),
                wema.remita_receipt("123456789"),
                wema.validate_bill_customer("meter", "7"),
                wema.vas_status(REFERENCE, "airtime"),
                wema.vas_status(REFERENCE, "bill"),
            )
        self.assertTrue(all(result["success"] for result in results))
        self.assertEqual(get.call_count, 4)
        self.assertEqual(post.call_count, 3)
        resolve.assert_not_called()
