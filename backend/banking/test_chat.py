from unittest.mock import patch
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from whatsapp.models import PendingAction, WhatsAppLink
from . import chat
from .client import Client, Reply
from .test_client import CONFIG
from .test_money import MoneyTests
from .test_bills import BILL_FIELDS


@override_settings(NINEPSB=CONFIG, RATELIMIT_ENABLE=False,
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ChatTests(TestCase):
    def setUp(self):
        MoneyTests.setUp(self)
        cache.clear()
        self.msisdn = "2347041267880"
        self.user.set_transaction_pin("582940")
        self.user.save()
        WhatsAppLink.objects.create(user=self.user, wa_msisdn=self.msisdn,
            status=WhatsAppLink.ACTIVE, linked_at=timezone.now())
        self.reply = self.enterContext(patch("whatsapp.router.reply"))
        self.confirm = self.enterContext(patch("whatsapp.router._send_confirm"))
        self.enterContext(patch("whatsapp.router.flows_live", return_value=False))
        self.enterContext(patch("whatsapp.router.sms_live", return_value=False))
        self.enterContext(patch("banking.payments.update_account", return_value=self.bank))

    def current(self):
        return PendingAction.objects.get(user=self.user, action_type=chat.ACTION)

    def test_airtime_uses_bank_network_and_existing_secure_confirmation(self):
        with patch("banking.bills.catalog", return_value={"network": "MTN"}), \
             patch("banking.payments._invoke") as send:
            chat.start(self.user, self.msisdn, "airtime", mobile="me", amount="100")
        pa = self.current()
        self.assertEqual(pa.state, "pin")
        self.assertEqual(pa.payload["phone"], self.user.phone)
        self.assertEqual(pa.payload["network"], "MTN")
        self.assertEqual(self.confirm.call_count, 1)
        send.assert_not_called()

    def test_data_selection_binds_bank_product_and_price(self):
        def catalog(kind, **params):
            return {"network": "MTN"} if kind == "network" else [{"productId": "NEW-PLAN", "amount": "100", "dataBundle": "1GB", "validity": "1 day"}]
        with patch("banking.bills.catalog", side_effect=catalog):
            chat.start(self.user, self.msisdn, "data", mobile="me")
            chat.advance(self.current(), self.user, self.msisdn, "1")
        pa = self.current()
        self.assertEqual(pa.payload["product_id"], "NEW-PLAN")
        self.assertEqual(pa.payload["amount"], "100.00")
        self.assertEqual(pa.state, "pin")

    def test_every_bill_category_uses_the_live_catalog_and_bound_validation(self):
        values = {"categories": [{"id": "NEW", "name": "New bank service"}],
            "billers": [{"id": "B1", "name": "Biller"}],
            "fields": BILL_FIELDS}
        with patch("banking.bills.catalog", side_effect=lambda kind, **kw: values[kind]), \
             patch("banking.bills.validate", return_value={"validation_reference": "SERVER-REF", "customer_name": "Jane Doe"}) as validate:
            chat.start(self.user, self.msisdn, "bill")
            for answer in ("1", "1", "1", "METER-123", "me"):
                chat.advance(self.current(), self.user, self.msisdn, answer)
        pa = self.current()
        self.assertEqual(pa.payload["validation_reference"], "SERVER-REF")
        self.assertEqual(pa.state, "pin")
        validate.assert_called_once_with(self.user, "B1", "I1", "METER-123", "100.00", inputs={})

    def test_no_plan_biller_collects_required_names_then_validates_once(self):
        values = {"categories": [{"id": "4", "name": "Subscriptions"}],
            "billers": [{"id": "B1", "name": "No-plan biller"}],
            "fields": BILL_FIELDS[:2] + [
                {"fieldName": "firstname", "fieldDescription": "First name", "validation": "Y"},
                {"fieldName": "lastname", "fieldDescription": "Last name", "validation": "Y"}]}
        with patch("banking.bills.catalog", side_effect=lambda kind, **kw: values[kind]), \
             patch("banking.bills.validate", return_value={"validation_reference": "BOUND", "customer_name": "Jane Doe"}) as validate:
            chat.start(self.user, self.msisdn, "bill")
            for answer in ("1", "1", "CUSTOMER-1", "Jane", "Doe", "me", "100"):
                chat.advance(self.current(), self.user, self.msisdn, answer)
        pa = self.current()
        self.assertEqual(pa.state, "pin")
        self.assertEqual(pa.payload["item_id"], "")
        validate.assert_called_once_with(self.user, "B1", "", "CUSTOMER-1", "100.00",
            inputs={"firstname": "Jane", "lastname": "Doe"})
        self.assertEqual(self.confirm.call_count, 1)

    def test_electricity_vend_options_without_prices_request_customer_amount(self):
        values = {"categories": [{"id": "1", "name": "Electricity"}],
            "billers": [{"id": "BP-IKEJA", "name": "Electricity biller"}],
            "fields": BILL_FIELDS[:2] + [{**BILL_FIELDS[2],
                "items": [{"itemId": "VT01", "itemName": "PREPAID"}, {"itemId": "VT02", "itemName": "POSTPAID"}]}]}
        with patch("banking.bills.catalog", side_effect=lambda kind, **kw: values[kind]), \
             patch("banking.bills.validate", return_value={"validation_reference": "BOUND", "customer_name": "Jane Doe"}) as validate:
            chat.start(self.user, self.msisdn, "bill")
            for answer in ("1", "1", "1", "CUSTOMER-1", "me"):
                chat.advance(self.current(), self.user, self.msisdn, answer)
            self.assertEqual(self.current().state, "amount")
            validate.assert_not_called()
            chat.advance(self.current(), self.user, self.msisdn, "150")
        self.assertEqual(self.current().state, "pin")
        validate.assert_called_once_with(self.user, "BP-IKEJA", "VT01", "CUSTOMER-1", "150.00", inputs={})

    def test_replayed_authorized_chat_action_does_not_resend(self):
        with patch("banking.bills.catalog", return_value={"network": "MTN"}):
            chat.start(self.user, self.msisdn, "airtime", mobile="me", amount="100")
            pa = self.current()
            with patch("banking.payments._invoke", return_value=Reply({"status": "success", "responseCode": "200"}, 200)) as send, \
                 patch("whatsapp.router._vtu_outcome") as outcome:
                chat.execute(pa, self.user, self.msisdn)
                chat.execute(pa, self.user, self.msisdn)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(outcome.call_count, 2)
        self.assertTrue(outcome.call_args.kwargs["replay"])
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 400)

    def test_disabled_vas_cannot_start_catalog_or_payment(self):
        with override_settings(NINEPSB={**CONFIG, "VAS_ENABLED": False}), patch("banking.bills.catalog") as catalog:
            chat.start(self.user, self.msisdn, "airtime")
        catalog.assert_not_called()
        self.assertFalse(PendingAction.objects.filter(action_type=chat.ACTION).exists())

    def test_electricity_journey_delivers_bound_receipt_and_replays_without_another_debit(self):
        def success(data):
            return Reply({"status": "success", "responseCode": "200", "data": data}, 200)

        with patch.object(Client, "categories", return_value=success([{"id": "1", "name": "Electricity"}])), \
             patch.object(Client, "billers", return_value=success([{"id": "B1", "name": "Electricity Biller"}])), \
             patch.object(Client, "fields", return_value=success(BILL_FIELDS)), \
             patch.object(Client, "validate_customer", return_value=success({"customerName": "Jane Doe", "otherField": "bound-value"})), \
             patch.object(Client, "pay_bill", return_value=success({"isToken": True,
                 "token": "11111111111111111111", "otherField": "Units Purchased: 3.93"})) as send, \
             patch("whatsapp.router._flow_pin_ok", return_value=True) as authorize, \
             patch("whatsapp.router.reply_receipt") as receipt:
            chat.start(self.user, self.msisdn, "bill")
            for answer in ("1", "1", "1", "METER-123", "me"):
                chat.advance(self.current(), self.user, self.msisdn, answer)
            send.assert_not_called()
            pa = self.current()
            chat.advance(pa, self.user, self.msisdn, "authorized-flow-result")
            chat.execute(pa, self.user, self.msisdn)
        authorize.assert_called_once()
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args.args[1:], ("100.00", self.bank.number))
        self.assertEqual(send.call_args.kwargs["customer"], "METER-123")
        self.assertEqual(send.call_args.kwargs["other_field"], "bound-value")
        self.assertEqual(receipt.call_count, 2)
        self.assertIn(("Token", "11111111111111111111"), receipt.call_args.args[2])
        self.assertIn(("Units", "3.93"), receipt.call_args.args[2])
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 400)

    def test_receipt_recovery_is_owner_scoped_and_never_calls_the_bank(self):
        from accounts.models import User
        from .test_bills import BillTests
        from . import bills

        row = BillTests.validation(self)
        with patch.object(Client, "pay_bill", return_value=Reply({"status": "success", "responseCode": "200",
                "data": {"isToken": True, "token": "11111111111111111111"}}, 200)):
            txn = bills.purchase_bill(self.user, row.reference, self.user.phone, "receipt-test")
        with patch("whatsapp.router.reply_receipt") as receipt, patch.object(Client, "request") as bank:
            chat.payment_receipt(self.user, self.msisdn, txn.reference)
            self.assertIn(("Token", "11111111111111111111"), receipt.call_args.args[2])
            receipt.reset_mock()
            other = User.objects.create_user(username="other-chat")
            WhatsAppLink.objects.create(user=other, wa_msisdn="2348011112222", status=WhatsAppLink.ACTIVE)
            chat.payment_receipt(other, "2348011112222", txn.reference)
            receipt.assert_not_called()
            self.assertNotIn("11111111111111111111", str(self.reply.call_args))
            bank.assert_not_called()

    def test_receipt_command_requires_idle_reauthentication_including_whitespace_variants(self):
        from whatsapp.router import handle_inbound
        for text in ("receipt ZTC-123", "RECEIPT\tZTC-123", "receipt\nZTC-123"):
            with self.subTest(text=text), patch("whatsapp.router._needs_reauth", return_value=True), \
                    patch("whatsapp.router._send_unlock") as unlock, patch("banking.chat.payment_receipt") as receipt:
                handle_inbound(self.msisdn, text)
                unlock.assert_called_once()
                receipt.assert_not_called()

    def test_pending_receipt_cannot_reveal_a_token(self):
        from .models import Instruction
        from .test_bills import BillTests
        from . import bills
        row = BillTests.validation(self)
        with patch.object(Client, "pay_bill", return_value=Reply({"status": "pending", "responseCode": "202",
                "data": {"isToken": True, "token": "11111111111111111111"}}, 200)):
            txn = bills.purchase_bill(self.user, row.reference, self.user.phone, "pending-receipt")
        self.assertEqual(Instruction.objects.get().state, "pending")
        with patch("whatsapp.router.reply_receipt") as receipt:
            chat.payment_receipt(self.user, self.msisdn, txn.reference)
        receipt.assert_not_called()
        self.assertIn("still being confirmed", self.reply.call_args.args[1])
        self.assertNotIn("11111111111111111111", self.reply.call_args.args[1])
