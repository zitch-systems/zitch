from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from wallet.models import Transaction
from . import bills
from .client import Reply, ProviderError
from .models import BillerValidation, Instruction
from .payments import digest
from .secrets import seal
from .test_client import CONFIG
from .test_money import MoneyTests

BILL_FIELDS = [
    {"fieldName": "customerId", "fieldDescription": "Enter Meter number", "validation": "Y"},
    {"fieldName": "amount", "fieldDescription": "Enter Amount to Pay", "validation": "Y"},
    {"fieldName": "itemId", "fieldDescription": "Select a plan", "validation": "Y", "isSelectData": "Y",
     "items": [{"itemId": "I1", "itemName": "Plan", "amount": "100"}]},
]


@override_settings(NINEPSB=CONFIG)
class BillTests(TestCase):
    def setUp(self):
        MoneyTests.setUp(self)
        self.enterContext(patch("banking.payments.update_account", return_value=self.bank))

    def validation(self, **changes):
        return BillerValidation.objects.create(user=self.user, reference="approved-customer", biller_id="B1", item_id="I1",
            environment="production",
            amount=100, customer_digest=digest("customer-1"),
            encrypted_result=seal({"customer": "customer-1", "name": "Jane Doe", "other_field": "bound-value"}),
            expires_at=timezone.now()+timedelta(minutes=5), **changes)

    def test_bill_source_and_price_come_from_server_owned_records(self):
        row = self.validation()
        with patch("banking.payments._invoke", return_value=Reply({"status": "success", "responseCode": "200"}, 200)) as send:
            txn = bills.purchase_bill(self.user, row.reference, "07041267880", "bill-once")
        self.assertEqual(txn.transaction_status, Transaction.SUCCESS)
        instruction, payload = send.call_args.args
        self.assertEqual(payload["source"], self.bank.number)
        self.assertEqual(payload["amount"], "100.00")
        self.assertEqual(payload["customer"], "customer-1")
        self.assertEqual(payload["customer_name"], "Jane Doe")
        self.assertEqual(len(instruction.reference), 18)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("400"))

    def test_another_customer_cannot_spend_validation(self):
        row = self.validation()
        other = User.objects.create_user(username="another")
        row.user = other
        row.save()
        with patch("banking.payments._invoke") as send, self.assertRaises(BillerValidation.DoesNotExist):
            bills.purchase_bill(self.user, row.reference, "07041267880", "bill-once")
        send.assert_not_called()
        self.assertFalse(Instruction.objects.exists())

    def test_expired_validation_cannot_reserve_funds(self):
        row = self.validation()
        BillerValidation.objects.filter(pk=row.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
        with patch("banking.payments._invoke") as send, self.assertRaises(ValueError):
            bills.purchase_bill(self.user, row.reference, "07041267880", "bill-once")
        send.assert_not_called()
        self.assertFalse(Instruction.objects.exists())

    def test_uncertain_bill_is_consumed_and_cannot_be_submitted_twice(self):
        row = self.validation()
        with patch("banking.payments._invoke", side_effect=ProviderError("network_unavailable", uncertain=True)) as send:
            txn = bills.purchase_bill(self.user, row.reference, "07041267880", "bill-once")
            with self.assertRaises(ValueError):
                bills.purchase_bill(self.user, row.reference, "07041267880", "bill-once")
        self.assertEqual(send.call_count, 1)
        self.assertEqual(txn.transaction_status, Transaction.PENDING)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("400"))

    def test_data_price_is_checked_again_before_reservation(self):
        def catalog(kind, **params):
            return {"network": "MTN"} if kind == "network" else [{"productId": "P1", "amount": "101"}]
        with patch("banking.bills.catalog", side_effect=catalog), patch("banking.payments._invoke") as send, self.assertRaises(ValueError):
            bills.purchase_topup(self.user, "data", "07041267880", "100", product_id="P1", idempotency_key="data-once")
        send.assert_not_called()
        self.assertFalse(Instruction.objects.exists())

    def test_topup_normalizes_phone_and_does_not_accept_client_network(self):
        with patch("banking.bills.catalog", return_value={"network": "MTN"}) as lookup, \
             patch("banking.payments._invoke", return_value=Reply({"status": "success", "responseCode": "200"}, 200)) as send:
            bills.purchase_topup(self.user, "airtime", "+2347041267880", "100", idempotency_key="airtime-once")
        lookup.assert_called_once_with("network", phone="07041267880")
        self.assertEqual(send.call_args.args[1]["phone"], "07041267880")
        self.assertEqual(send.call_args.args[1]["network"], "MTN")

    def test_client_cannot_choose_an_unlisted_bill_item(self):
        with patch("banking.bills.catalog", return_value=BILL_FIELDS), \
             patch("banking.client.Client.validate_customer") as query, self.assertRaises(ValueError):
            bills.validate(self.user, "B1", "unlisted", "customer-1", "1")
        query.assert_not_called()

    def test_malformed_biller_name_cannot_create_spend_authorization(self):
        for name in (True, ["Jane", "Doe"], {"name": "Jane Doe"}, " ", "J"*161):
            with self.subTest(name=name), patch("banking.bills.catalog", return_value=BILL_FIELDS), \
                    patch("banking.client.Client.validate_customer", return_value=Reply({
                        "status": "success", "responseCode": "200", "data": {"customerName": name}}, 200)), \
                    self.assertRaises(ValueError):
                bills.validate(self.user, "B1", "I1", "customer-1", "100")
        self.assertFalse(BillerValidation.objects.exists())


@override_settings(NINEPSB=CONFIG, SECURE_SSL_REDIRECT=False, RATELIMIT_ENABLE=False,
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class PurchaseApiTests(TestCase):
    def setUp(self):
        from accounts.models import AccessToken
        BillTests.setUp(self)
        self.user.set_transaction_pin("582940")
        self.user.save()
        self.token = AccessToken.issue(self.user).key
        self.row = BillTests.validation(self)

    def post(self, **changes):
        body = {"kind": "bill", "phone": "07041267880", "amount": "100",
            "validation_reference": self.row.reference, "idempotency_key": "stable-bill-intent",
            "transaction_pin": "582940", **changes}
        return self.client.post("/api/banking/purchase/", body, content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + self.token)

    def test_revalidated_bill_replays_same_payment_after_lost_response(self):
        with patch("banking.payments._invoke", return_value=Reply({"status": "success", "responseCode": "202"}, 200)) as send:
            first = self.post()
            replacement = BillerValidation.objects.get(pk=self.row.pk)
            replacement.pk = None
            replacement.reference = "renewed-validation"
            replacement.consumed = False
            replacement.save()
            replay = self.post(validation_reference=replacement.reference, amount="100.00", phone="+2347041267880")
        self.assertEqual(first.status_code, 200, first.content)
        self.assertTrue(first.json()["pending"])
        self.assertTrue(replay.json()["pending"])
        self.assertEqual(first.json()["reference"], replay.json()["reference"])
        self.assertEqual(send.call_count, 1)
        self.assertEqual(Instruction.objects.count(), 1)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 400)

    def test_amount_must_match_customer_review_before_debit(self):
        with patch("banking.payments._invoke") as send:
            response = self.post(amount="99")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "price_changed")
        send.assert_not_called()
        self.assertFalse(Instruction.objects.exists())

    def test_reused_key_cannot_pay_another_customer(self):
        with patch("banking.payments._invoke", return_value=Reply({"status": "success", "responseCode": "202"}, 200)) as send:
            self.post()
            replacement = BillerValidation.objects.get(pk=self.row.pk)
            replacement.pk = None
            replacement.reference = "another-customer"
            replacement.customer_digest = digest("another-customer")
            replacement.consumed = False
            replacement.save()
            response = self.post(validation_reference=replacement.reference)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "idempotency_conflict")
        self.assertEqual(send.call_count, 1)

    def test_pin_is_required_before_dispatch(self):
        with patch("banking.payments._invoke") as send:
            response = self.post(transaction_pin="")
        self.assertGreaterEqual(response.status_code, 400)
        send.assert_not_called()
        self.assertFalse(Instruction.objects.exists())
