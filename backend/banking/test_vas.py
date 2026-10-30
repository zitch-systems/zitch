"""VAS contract regressions derived from the supplied PDF/Postman structures.

All identifiers, tokens and money here are synthetic. These are not live UAT.
"""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from accounts.models import AccessToken, User
from wallet.models import Transaction
from wallet.services import wallet_expected_balance
from . import bills
from .client import Client, ProviderError, Reply
from .models import BillerValidation, Instruction
from .payments import requery
from .test_bills import BILL_FIELDS
from .test_client import CONFIG
from .vas import fields_schema, receipt


def success(data):
    return Reply({"status": "success", "responseCode": "200", "data": data}, 200)


@override_settings(NINEPSB=CONFIG)
class VasWireTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_all_documented_vas_routes_and_authentication_shape(self):
        # Explicit paths from the documents, not paths constructed by the code.
        client = Client("vas")
        ref = "Z90000000000000001"
        with patch.object(Client, "_send", side_effect=[
            success({"accessToken": "synthetic-bearer", "expiresIn": 7200000}),
            *[success({}) for _ in range(11)],
        ]) as send:
            client.network("+2347041267880")
            client.data_plans("07041267880")
            client.topup("airtime", ref, "100", "1100000001", "07041267880", "MTN")
            client.topup("data", ref, "200", "1100000001", "07041267880", "MTN", product_id="MTN-200MB-2")
            client.vas_status(ref)
            client.categories()
            client.billers("1")
            client.fields("BP-IKEJA")
            client.validate_customer("BP-IKEJA", "CUSTOMER-1", item="VT01", amount="100",
                firstname="Jane", lastname="Doe")
            client.pay_bill(ref, "100", "1100000001", biller="BP-IKEJA", item="VT01",
                customer="CUSTOMER-1", mobile="07041267880", name="Jane Doe", other_field="bound-value")
            client.vas_status(ref, bill=True)
        calls = send.call_args_list
        self.assertEqual(calls[0].args, ("POST", "https://bank.example.test/identity/api/v1/authenticate"))
        self.assertEqual(calls[0].kwargs["payload"], {"username": "vas", "password": "vas"})
        routes = [("GET", "topup/network"), ("GET", "topup/dataPlans"), ("POST", "topup/airtime"),
            ("POST", "topup/data"), ("GET", "topup/status"), ("GET", "billspayment/categories"),
            ("GET", "billspayment/billers/1"), ("GET", "billspayment/fields/BP-IKEJA"),
            ("POST", "billspayment/validate"), ("POST", "billspayment/pay"), ("GET", "billspayment/status")]
        for call, (method, path) in zip(calls[1:], routes):
            self.assertEqual(call.args, (method, "https://bank.example.test/vas/api/v1/" + path))
            self.assertEqual(call.kwargs["token"], "synthetic-bearer")
        self.assertEqual(calls[1].kwargs["params"], {"phone": "07041267880"})
        self.assertEqual(calls[5].kwargs["params"], {"transReference": ref})
        self.assertEqual(calls[-1].kwargs["params"], {"transReference": ref})
        self.assertEqual(calls[4].kwargs["payload"]["productId"], "MTN-200MB-2")
        self.assertEqual(calls[9].kwargs["payload"], {"billerId": "BP-IKEJA", "customerId": "CUSTOMER-1",
            "itemId": "VT01", "amount": "100", "firstname": "Jane", "lastname": "Doe"})
        self.assertEqual(calls[10].kwargs["payload"]["otherField"], "bound-value")

    def test_failure_envelope_cannot_cache_a_token(self):
        for body in ({"status": "failed", "responseCode": "200"},
                     {"status": "success", "responseCode": "400"}, {"status": "success"}):
            with self.subTest(body=body), patch.object(Client, "_send", return_value=Reply({**body,
                    "data": {"accessToken": "untrusted"}}, 200)), self.assertRaises(ProviderError):
                Client("vas").token()
            self.assertIsNone(cache.get(Client("vas")._cache_key()))

    def test_duplicate_code_300_cannot_refund_the_original_instruction(self):
        reply = Reply({"status": "failed", "responseCode": "300",
            "message": "Duplicate Slip/Instrument No. 'SYNTHETIC' detected."}, 200)
        self.assertEqual(reply.payment_state(product="vas"), "pending")
        self.assertEqual(reply.payment_state(product="vas", query=True), "pending")

    def test_only_explicit_receipt_fields_are_returned(self):
        self.assertEqual(receipt({"isToken": False, "token": "not-a-token", "otherField": "private"}), {})
        self.assertEqual(receipt({"isToken": True, "token": ["11111111111111111111"]}), {})
        self.assertEqual(receipt({"isToken": True, "token": "11111111111111111111"}),
            {"token": "11111111111111111111"})
        self.assertEqual(receipt({"description": "successful\nToken:\n11111111111111111111\nUnits Purchased: 7.8"}, query=True),
            {"token": "11111111111111111111", "electricity_units": "7.8"})
        self.assertEqual(receipt({"description": "Reference: 11111111111111111111"}, query=True), {})
        self.assertEqual(receipt({"isToken": False, "description": "Token: 11111111111111111111"}, query=True), {})
        self.assertNotIn("token", receipt({"isToken": True, "token": "11111111111111111111",
            "description": "Token: 22222222222222222222"}, query=True))

    def test_malformed_catalog_cannot_turn_into_an_open_price_biller(self):
        variants = [[], {}, [{"fieldName": "unknown", "validation": "Y"}],
            [BILL_FIELDS[0], BILL_FIELDS[0]],
            [BILL_FIELDS[0], {**BILL_FIELDS[2], "items": []}],
            [BILL_FIELDS[0], {**BILL_FIELDS[2], "items": [{"itemId": "I1", "itemName": "Plan", "amount": "NaN"}]}],
            [BILL_FIELDS[0], {**BILL_FIELDS[2], "items": [None]}],
            [BILL_FIELDS[0], {"fieldName": "firstname", "validation": True}]]
        for value in variants:
            with self.subTest(value=value), self.assertRaises(ProviderError):
                fields_schema(value)

    def test_wrong_product_cannot_reach_authentication_or_a_money_endpoint(self):
        with patch.object(Client, "_send") as send:
            with self.assertRaises(ValueError):
                Client().categories()
            with self.assertRaises(ValueError):
                Client().topup("airtime", "Z90000000000000001", "100", "1100000001", "07041267880", "MTN")
            with self.assertRaises(ValueError):
                Client("vas").wallet("1100000001")
        send.assert_not_called()

    def test_switching_environment_cannot_reuse_a_cached_token_or_catalog(self):
        with patch.object(Client, "_send", side_effect=[
                success({"accessToken": "production-only", "expiresIn": 3600}),
                success({"accessToken": "sandbox-only", "expiresIn": 3600})]) as send:
            first_key = Client("vas")._cache_key()
            self.assertEqual(Client("vas").token(), "production-only")
            with override_settings(NINEPSB={**CONFIG, "ENVIRONMENT": "sandbox"}):
                self.assertNotEqual(Client("vas")._cache_key(), first_key)
                self.assertEqual(Client("vas").token(), "sandbox-only")
            self.assertEqual(send.call_count, 2)

    def test_immediate_electricity_receipt_parses_units_without_exposing_other_field(self):
        self.assertEqual(receipt({"isToken": True, "token": "11111111111111111111",
            "otherField": "Units Purchased: 3.93"}),
            {"token": "11111111111111111111", "electricity_units": "3.93"})
        self.assertEqual(receipt({"otherField": "Customer: private\r\nUnits Purchased: 3.93\r\n"}),
            {"electricity_units": "3.93"})
        self.assertNotIn("electricity_units", receipt({"otherField": "Units Purchased: 3.93",
            "description": "Units Purchased: 7.8"}, query=True))


@override_settings(NINEPSB=CONFIG, SECURE_SSL_REDIRECT=False, RATELIMIT_ENABLE=False,
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class VasJourneyTests(TestCase):
    def setUp(self):
        from .test_money import MoneyTests
        MoneyTests.setUp(self)
        self.user.set_transaction_pin("582940")
        self.user.save()
        self.token = AccessToken.issue(self.user).key
        self.enterContext(patch("banking.payments.update_account", return_value=self.bank))
        cache.clear()

    def post(self, path, body, token=None):
        return self.client.post(path, body, content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + (token or self.token))

    def validate(self, fields=None, *, item="I1", inputs=None, reply=None):
        with patch.object(Client, "fields", return_value=success(fields if fields is not None else BILL_FIELDS)), \
             patch.object(Client, "validate_customer", return_value=reply or success({
                 "customerName": "Jane Doe", "otherField": "bound-value"})) as validate:
            result = self.post("/api/banking/bills/validate/", {"biller_id": "B1", "item_id": item,
                "customer": "CUSTOMER-1", "amount": "100", "inputs": inputs or {}})
        return result, validate

    def purchase(self, validation, **changes):
        return self.post("/api/banking/purchase/", {"kind": "bill", "amount": "100", "phone": "07041267880",
            "validation_reference": validation, "transaction_pin": "582940", "idempotency_key": "once", **changes})

    def assert_balance(self, expected):
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal(expected))
        self.assertEqual(wallet_expected_balance(self.user), Decimal(expected))

    def test_no_plan_bill_validates_and_pays_with_server_bound_name_and_source(self):
        result, validate = self.validate(BILL_FIELDS[:2], item="")
        self.assertEqual(result.status_code, 200, result.content)
        validate.assert_called_once_with("B1", "CUSTOMER-1", amount=Decimal("100.00"))
        with patch.object(Client, "token", return_value="synthetic"), \
             patch.object(Client, "_send", return_value=success({"isToken": False})) as send:
            response = self.purchase(result.json()["validation_reference"])
        self.assertTrue(response.json()["success"], response.content)
        body = send.call_args.kwargs["payload"]
        self.assertNotIn("itemId", body)
        self.assertEqual(body["debitAccount"], self.bank.number)
        self.assertEqual(body["customerName"], "Jane Doe")
        self.assertEqual(body["otherField"], "bound-value")
        self.assertEqual(body["amount"], "100")
        self.assertEqual(len(body["transactionReference"]), 18)
        self.assert_balance("400")

    def test_required_names_are_collected_and_validation_n_fields_are_omitted(self):
        fields = [BILL_FIELDS[0], {**BILL_FIELDS[1], "validation": "N"},
            {**BILL_FIELDS[2], "validation": "N"},
            {"fieldName": "firstname", "validation": "Y"}, {"fieldName": "lastname", "validation": "Y"}]
        result, validate = self.validate(fields, inputs={"firstname": "Jane", "lastname": "Doe"})
        self.assertEqual(result.status_code, 200, result.content)
        validate.assert_called_once_with("B1", "CUSTOMER-1", firstname="Jane", lastname="Doe")
        row = BillerValidation.objects.get()
        self.assertEqual(row.item_id, "I1")
        self.assertEqual(row.amount, 100)
        self.assertEqual(row.environment, "production")

    def test_missing_required_names_cannot_create_a_payment_authorization(self):
        result, validate = self.validate(BILL_FIELDS + [{"fieldName": "firstname", "validation": "Y"}])
        self.assertEqual(result.status_code, 400)
        validate.assert_not_called()
        self.assertFalse(BillerValidation.objects.exists())
        self.assert_balance("500")

    def test_unpriced_electricity_selection_uses_customer_amount(self):
        fields = deepcopy(BILL_FIELDS)
        fields[2]["items"] = [{"itemId": "VT01", "itemName": "PREPAID"}, {"itemId": "VT02", "itemName": "POSTPAID"}]
        result, validate = self.validate(fields, item="VT01")
        self.assertEqual(result.status_code, 200, result.content)
        validate.assert_called_once_with("B1", "CUSTOMER-1", item="VT01", amount=Decimal("100.00"))

    def test_changed_validation_price_and_untrusted_other_field_are_rejected(self):
        for extra in ({"amount": "101"}, {"amount": "nan"}, {"otherField": {"untrusted": True}}):
            with self.subTest(extra=extra):
                result, _ = self.validate(reply=success({"customerName": "Jane Doe", **extra}))
                self.assertGreaterEqual(result.status_code, 400)
        self.assertFalse(BillerValidation.objects.exists())

    def test_authentication_failure_does_not_reserve_or_dispatch_money(self):
        with patch.object(Client, "_send", return_value=Reply({"status": "failed", "responseCode": "400"}, 400)) as send:
            result = self.post("/api/banking/bills/validate/", {"biller_id": "B1", "customer": "CUSTOMER-1", "amount": "100"})
        self.assertEqual(result.status_code, 503)
        self.assertEqual(send.call_count, 1)
        self.assertFalse(BillerValidation.objects.exists())
        self.assertFalse(Instruction.objects.exists())
        self.assert_balance("500")

    def test_duplicate_then_not_found_retains_reservation_until_confirmed_failure(self):
        result, _ = self.validate()
        ref = result.json()["validation_reference"]
        with patch.object(Client, "pay_bill", return_value=Reply({"status": "failed", "responseCode": "300"}, 200)) as send:
            first, again = self.purchase(ref), self.purchase(ref)
        self.assertTrue(first.json()["pending"])
        self.assertTrue(again.json()["pending"])
        self.assertEqual(send.call_count, 1)
        instruction = Instruction.objects.get()
        with patch.object(Client, "vas_status", return_value=Reply({"status": "failed", "responseCode": "404"}, 404)):
            self.assertTrue(requery(instruction)["pending"])
        self.assert_balance("400")
        instruction.refresh_from_db()
        with patch.object(Client, "vas_status", return_value=success({"transactionStatus": "failed"})) as query:
            self.assertFalse(requery(instruction)["success"])
            instruction.refresh_from_db()
            requery(instruction)
        self.assertEqual(query.call_count, 1)
        self.assertEqual(instruction.state, "failed")
        self.assert_balance("500")

    def test_interrupted_bill_recovers_status_token_and_units_for_its_owner(self):
        result, _ = self.validate()
        ref = result.json()["validation_reference"]
        with patch.object(Client, "pay_bill", side_effect=ProviderError("network_unavailable", uncertain=True)) as send:
            response = self.purchase(ref)
            self.assertTrue(response.json()["pending"])
            self.purchase(ref)
        self.assertEqual(send.call_count, 1)
        instruction = Instruction.objects.get()
        token = "11111111111111111111"
        with patch.object(Client, "vas_status", return_value=success({"transactionStatus": "success",
                "description": "successful\nToken:\n" + token + "\nUnits Purchased: 7.8"})) as query:
            settled = requery(instruction)
        query.assert_called_once_with(instruction.reference, bill=True)
        self.assertTrue(settled["success"])
        self.assertEqual(settled["token"], token)
        txn = Transaction.objects.get(pk=instruction.transaction_id)
        self.assertEqual(txn.meta["electricity_units"], "7.8")
        replay = self.purchase(ref)
        self.assertEqual(replay.json()["token"], token)
        detail = self.post("/api/transaction/status/", {"reference": txn.reference})
        self.assertEqual(detail.json()["transaction"]["token"], token)
        self.assertEqual(detail.json()["transaction"]["electricity_units"], "7.8")
        other = User.objects.create_user(username="outsider")
        denied = self.post("/api/transaction/status/", {"reference": txn.reference}, AccessToken.issue(other).key)
        self.assertEqual(denied.status_code, 404)
        self.assertNotIn(token, denied.content.decode())
        self.assert_balance("400")

    def test_pending_receipt_does_not_expose_token_until_confirmed(self):
        result, _ = self.validate()
        with patch.object(Client, "pay_bill", return_value=Reply({"status": "pending", "responseCode": "202",
                "data": {"isToken": True, "token": "11111111111111111111"}}, 200)):
            response = self.purchase(result.json()["validation_reference"])
        self.assertTrue(response.json()["pending"])
        self.assertNotIn("11111111111111111111", response.content.decode())
        txn = Instruction.objects.get().transaction
        detail = self.post("/api/transaction/status/", {"reference": txn.reference})
        self.assertNotIn("token", detail.json()["transaction"])

    def test_foreign_environment_validation_cannot_authorize_a_debit(self):
        result, _ = self.validate()
        BillerValidation.objects.update(environment="sandbox")
        with patch.object(Client, "pay_bill") as send:
            response = self.purchase(result.json()["validation_reference"])
        self.assertEqual(response.status_code, 400)
        send.assert_not_called()
        self.assertFalse(Instruction.objects.exists())
        self.assert_balance("500")

    def test_delivered_bill_recovers_missing_token_without_reopening_money(self):
        result, _ = self.validate()
        with patch.object(Client, "pay_bill", return_value=success({"isToken": True, "token": ""})):
            response = self.purchase(result.json()["validation_reference"])
        self.assertTrue(response.json()["success"])
        self.assertTrue(response.json()["receipt_pending"])
        instruction = Instruction.objects.get()
        self.assertEqual(instruction.state, "success")
        self.assertTrue(instruction.receipt_pending)
        self.assertTrue(instruction.encrypted_request)
        for reply in (success({"transactionStatus": "failed"}), Reply({}, 404),
                      success({"transactionStatus": "success", "reference": "OTHER"})):
            with self.subTest(reply=reply), patch.object(Client, "vas_status", return_value=reply):
                outcome = requery(instruction)
            self.assertTrue(outcome["success"])
            self.assertTrue(outcome["receipt_pending"])
            instruction.refresh_from_db()
            self.assertEqual(instruction.state, "success")
            self.assert_balance("400")
        with patch.object(Client, "vas_status", side_effect=ProviderError("network_unavailable")):
            self.assertTrue(requery(instruction)["success"])
        Instruction.objects.filter(pk=instruction.pk).update(next_check=timezone.now()-timedelta(seconds=1))
        from .reconciliation import sweep
        with patch.object(Client, "vas_status", return_value=success({"transactionStatus": "success",
                "description": "successful\nToken:\n11111111111111111111\nUnits Purchased: 7.8"})) as query:
            checked = sweep()
        self.assertEqual(checked["instructions"], 1)
        self.assertEqual(query.call_count, 1)
        instruction.refresh_from_db()
        self.assertFalse(instruction.receipt_pending)
        self.assertEqual(instruction.encrypted_request, "")
        self.assertIsNone(instruction.next_check)
        self.assertEqual(instruction.transaction.meta["token"], "11111111111111111111")
        detail = self.post("/api/transaction/status/", {"reference": instruction.transaction.reference})
        self.assertFalse(detail.json()["transaction"]["receipt_pending"])
        self.assert_balance("400")

    def test_contradictory_success_cannot_settle_or_refund(self):
        result, _ = self.validate()
        with patch.object(Client, "pay_bill", return_value=success({"amount": "101", "isToken": True,
                "token": "11111111111111111111"})):
            response = self.purchase(result.json()["validation_reference"])
        self.assertTrue(response.json()["pending"])
        instruction = Instruction.objects.get()
        self.assertEqual(instruction.last_error, "vas_response_mismatch")
        for extra in ({"transactionReference": instruction.reference, "reference": "OTHER"},
                      {"amount": "101"}, {"debitAccount": "1100000099"}, {"billerId": "OTHER"}):
            with self.subTest(extra=extra), patch.object(Client, "vas_status", return_value=success({
                    "transactionStatus": "success", **extra})):
                self.assertTrue(requery(instruction)["pending"])
            instruction.refresh_from_db()
        self.assert_balance("400")

    def test_topup_echo_must_match_selected_recipient_amount_network_and_product(self):
        for index, extra in enumerate(({"recipient": "08011112222"}, {"amount": "101"},
                {"network": "OTHER"}, {"productId": "OTHER"}, {"transactionId": "OTHER"})):
            with self.subTest(extra=extra), patch("banking.bills.catalog", side_effect=lambda kind, **kw:
                    {"network": "MTN"} if kind == "network" else [{"productId": "P1", "amount": "50"}]), \
                 patch.object(Client, "topup", return_value=success(extra)):
                txn = bills.purchase_topup(self.user, "data", "07041267880", "50", product_id="P1", idempotency_key="data-"+str(index))
                self.assertEqual(txn.transaction_status, Transaction.PENDING)
        self.assert_balance("250")

    def test_immediate_units_and_token_survive_api_replay_and_history(self):
        result, _ = self.validate()
        ref = result.json()["validation_reference"]
        with patch.object(Client, "pay_bill", return_value=success({"isToken": True,
                "token": "11111111111111111111", "otherField": "Units Purchased: 3.93"})) as send:
            first, replay = self.purchase(ref), self.purchase(ref)
        for response in (first, replay):
            self.assertEqual(response.json()["token"], "11111111111111111111")
            self.assertEqual(response.json()["electricity_units"], "3.93")
        self.assertEqual(send.call_count, 1)
        detail = self.post("/api/transaction/status/", {"reference": first.json()["reference"]})
        self.assertEqual(detail.json()["transaction"]["electricity_units"], "3.93")
        self.assert_balance("400")

    def test_sandbox_topup_cannot_be_replayed_as_a_production_payment(self):
        body = {"kind": "airtime", "amount": "100", "phone": "07041267880",
            "transaction_pin": "582940", "idempotency_key": "environment-bound"}
        with patch("banking.bills.catalog", return_value={"network": "MTN"}), \
                patch.object(Client, "topup", return_value=success({})) as send:
            first = self.post("/api/banking/purchase/", body)
            self.assertTrue(first.json()["success"])
            with override_settings(NINEPSB={**CONFIG, "ENVIRONMENT": "sandbox", "SANDBOX_CUSTOMERS_ENABLED": True}):
                other_environment = self.post("/api/banking/purchase/", body)
        self.assertEqual(other_environment.status_code, 409)
        self.assertEqual(other_environment.json()["code"], "idempotency_conflict")
        self.assertEqual(send.call_count, 1)
        self.assert_balance("400")
