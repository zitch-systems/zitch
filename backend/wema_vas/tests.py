"""Bank authentication, money isolation, replay safety and retained evidence."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone

from accounts.models import User
from wallet.models import Transaction, TransactionAlertDelivery, Wallet
from wallet.services import LimitExceeded, credit, get_or_create_wallet, wallet_expected_balance

from .identity import decrypt_identity, encrypt_identity
from .models import MigrationApproval, Receipt, VirtualAccount
from .services import ReplayConflict, assert_can_spend, process_notification

TOKEN = "bank-only-test-token-" + "a" * 48
KEY = Fernet.generate_key().decode()
LIVE = {"ENABLED": True, "MODE": "live", "PREFIX": "999", "TOKEN": TOKEN,
        "IDENTITY_KEYS": [KEY], "REQUIRE_HTTPS": True, "TRUST_TLS_PROXY": False}
VALIDATION = {**LIVE, "MODE": "validation", "PREFIX": "711"}


def account_fixture(suffix="1", mode="live"):
    user = User.objects.create_user(username="vas-customer-" + suffix, phone="0809990000" + suffix,
                                    first_name="Ada", email="vas" + suffix + "@zitch.test")
    get_or_create_wallet(user)
    prefix = "711" if mode == "validation" else "999"
    account = VirtualAccount.objects.create(
        user=user, number=prefix + "000000" + suffix, display_name="Zitch/Ada " + suffix,
        encrypted_identity=encrypt_identity(bvn="12345678901", phone="234809990000" + suffix),
        verification_reference="proof-" + suffix, consent_reference="consent-" + suffix,
        verified_at=timezone.now(), mode=mode, prefix=prefix,
    )
    return user, account


def payload(account, **changes):
    body = {"craccount": account.number, "craccountname": account.display_name,
            "originatoraccountnumber": "0000000000", "originatorname": "Test Sender",
            "bankcode": "000001", "bankname": "Source Bank", "paymentreference": "PAYMENT-1",
            "sessionid": "SESSION-1", "amount": "1250.00", "narration": "Funding",
            "created_at": "2026-01-20T16:15:14.983+01:00"}
    return {**body, **changes}


@override_settings(WEMA_VAS=LIVE, ROOT_URLCONF="wema_vas.urls", TESTING=True,
                   ALLOWED_HOSTS=["testserver"], SECURE_SSL_REDIRECT=False,
                   TXN_ALERTS={"EMAIL": True, "SMS": False, "WHATSAPP": False, "PUSH": False})
class VasApiTests(TestCase):
    def setUp(self):
        self.user, self.account = account_fixture()

    def post(self, route, body=None, **kwargs):
        return self.client.post("/vas/" + route, json.dumps(body or {"accountnumber": self.account.number}),
                                content_type="application/json", secure=True,
                                HTTP_AUTHORIZATION="Bearer " + TOKEN, **kwargs)

    def test_lookup_and_kyc_return_verified_encrypted_identity_only_to_bank(self):
        result = self.post("account-lookup")
        self.assertEqual(result.json(), {"accountname": self.account.display_name, "status": "00",
                                         "status_desc": "Okay", "bvn": "12345678901", "nin": ""})
        self.assertEqual(result["Cache-Control"], "no-store")
        self.assertNotIn("12345678901", self.account.encrypted_identity)
        self.assertEqual(self.post("kyc-details").json()["walletbalance"], "0.00")

    def test_notification_atomically_credits_ledger_and_durable_alert_once(self):
        data = payload(self.account)
        first = self.post("transaction-notification", data)
        second = self.post("transaction-notification", data)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(first.json()["status"], "00")
        self.assertEqual(Receipt.objects.count(), 1)
        self.assertEqual(Transaction.objects.count(), 1)
        self.assertEqual(TransactionAlertDelivery.objects.count(), 1)
        self.user.wallet.refresh_from_db()
        self.assertEqual(self.user.wallet.balance, Decimal("1250.00"))
        self.assertEqual(wallet_expected_balance(self.user.pk), self.user.wallet.balance)

    def test_decimal_and_timezone_normalization_are_exact_replays(self):
        first = self.post("transaction-notification", payload(self.account, amount="1250"))
        replay = self.post("transaction-notification", payload(self.account, amount="1250.0", created_at="2026-01-20T15:15:14.983Z"))
        self.assertEqual(first.json(), replay.json())
        self.assertEqual(Transaction.objects.count(), 1)

    def test_conflicting_session_fields_never_credit_or_acknowledge(self):
        self.post("transaction-notification", payload(self.account))
        for changes in ({"amount": "2000.00"}, {"paymentreference": "PAYMENT-OTHER"},
                        {"originatoraccountnumber": "1111111111"}, {"created_at": "2026-01-19T16:15:14.983+01:00"}):
            with self.subTest(changes=changes):
                self.assertEqual(self.post("transaction-notification", payload(self.account, **changes)).status_code, 409)
        self.assertEqual(Transaction.objects.count(), 1)

    def test_payment_reference_reused_for_different_session_rolls_back_entire_credit(self):
        self.post("transaction-notification", payload(self.account))
        result = self.post("transaction-notification", payload(self.account, sessionid="DIFFERENT-SESSION"))
        self.assertEqual(result.status_code, 409)
        self.assertEqual(Receipt.objects.count(), 1)
        self.assertEqual(Transaction.objects.count(), 1)
        self.assertEqual(TransactionAlertDelivery.objects.count(), 1)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("1250.00"))

    def test_cross_account_session_collision_cannot_credit_other_customer(self):
        other_user, other_account = account_fixture("2")
        self.post("transaction-notification", payload(self.account))
        result = self.post("transaction-notification", payload(other_account))
        self.assertEqual(result.status_code, 409)
        self.assertEqual(Wallet.objects.get(user=other_user).balance, 0)
        self.assertEqual(Transaction.objects.count(), 1)

    def test_receipt_failure_rolls_back_balance_ledger_and_outbox(self):
        with patch("wema_vas.services.Receipt.objects.create", side_effect=IntegrityError("forced conflict")):
            self.assertEqual(self.post("transaction-notification", payload(self.account)).status_code, 409)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, 0)
        self.assertFalse(Transaction.objects.exists())
        self.assertFalse(TransactionAlertDelivery.objects.exists())

    def test_block_idempotence_and_held_funds_never_become_spendable(self):
        first = self.post("block-account", {"accountnumber": self.account.number, "blockreason": "bank case 1"})
        self.assertEqual(first.status_code, 200)
        self.account.refresh_from_db()
        blocked_at = self.account.blocked_at
        self.post("block-account", {"accountnumber": self.account.number, "blockreason": "second request"})
        self.account.refresh_from_db()
        self.assertEqual(self.account.blocked_at, blocked_at)
        self.assertEqual(self.account.block_reason, "bank case 1")
        self.assertEqual(self.post("account-lookup").json()["status"], "07")
        first = self.post("transaction-notification", payload(self.account))
        repeat = self.post("transaction-notification", payload(self.account))
        self.assertEqual(first.status_code, 503)
        self.assertEqual(first.json(), repeat.json())
        self.assertEqual(Receipt.objects.get().state, Receipt.HELD)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, 0)
        self.assertFalse(Transaction.objects.exists())
        self.assertEqual(self.post("mini-statement").json(), {"transactions": []})
        with self.assertRaises(LimitExceeded):
            assert_can_spend(self.user)

    def test_previously_accepted_duplicate_remains_acknowledged_after_block(self):
        first = self.post("transaction-notification", payload(self.account))
        self.post("block-account", {"accountnumber": self.account.number, "blockreason": "review"})
        self.assertEqual(self.post("transaction-notification", payload(self.account)).json(), first.json())
        self.assertEqual(Transaction.objects.count(), 1)

    def test_inactive_user_inflow_is_held_even_if_account_active(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.assertEqual(self.post("transaction-notification", payload(self.account)).status_code, 503)
        self.assertEqual(Receipt.objects.get().state, Receipt.HELD)
        self.assertFalse(Transaction.objects.exists())

    def test_legacy_late_credit_is_not_reported_as_vas_collection_money(self):
        credit(self.user, "650", "funding", meta={"provider": "wema"})
        self.post("transaction-notification", payload(self.account))
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("1900"))
        self.assertEqual(self.post("kyc-details").json()["walletbalance"], "1250.00")
        statement = self.post("mini-statement").json()["transactions"]
        self.assertEqual(len(statement), 1)
        self.assertEqual(statement[0]["amount"], "1250.00")

    def test_statement_uses_ten_lagos_calendar_days_from_last_receipt(self):
        for i, date in enumerate(("2026-01-01T23:30:00+01:00", "2026-01-02T00:00:00+01:00", "2026-01-11T23:59:59+01:00")):
            self.post("transaction-notification", payload(self.account, sessionid=f"S-{i}", paymentreference=f"P-{i}", created_at=date))
        statement = self.post("mini-statement").json()["transactions"]
        self.assertEqual(len(statement), 2)
        self.assertTrue(all(row["direction"] == "Credit" for row in statement))

    def test_all_vas_spending_stays_closed_pending_outward_contract(self):
        with self.assertRaisesMessage(LimitExceeded, "awaiting payment activation"):
            assert_can_spend(self.user)

    def test_invalid_wire_amounts_are_rejected_without_financial_writes(self):
        for value in ("0", "-1", "1.001", "1e3", 50, True, "NaN", "1000000000000", "١٠"):
            with self.subTest(value=value):
                self.assertEqual(self.post("transaction-notification", payload(self.account, amount=value)).status_code, 400)
        self.assertFalse(Receipt.objects.exists())
        self.assertFalse(Transaction.objects.exists())

    def test_unknown_account_and_wrong_name_fail_closed(self):
        self.assertEqual(self.post("transaction-notification", payload(self.account, craccount="9999999999")).json()["status"], "07")
        self.assertEqual(self.post("transaction-notification", payload(self.account, craccountname="Wrong")).status_code, 400)
        self.assertFalse(Receipt.objects.exists())

    def test_future_or_invalid_dates_are_rejected(self):
        for value in ("invalid", "2026-99-99T12:00:00", (timezone.now() + timedelta(days=1)).isoformat()):
            self.assertEqual(self.post("transaction-notification", payload(self.account, created_at=value)).status_code, 400)

    def test_wallet_overflow_never_partially_posts(self):
        Wallet.objects.filter(user=self.user).update(balance=Decimal("999999999999.00"))
        self.assertEqual(self.post("transaction-notification", payload(self.account)).status_code, 400)
        self.assertFalse(Transaction.objects.exists())
        self.assertFalse(Receipt.objects.exists())

    def test_disabled_routes_are_hidden(self):
        with override_settings(WEMA_VAS={**LIVE, "ENABLED": False}):
            self.assertEqual(self.post("account-lookup").status_code, 404)

    def test_auth_tls_method_and_content_type_gates(self):
        path = "/vas/account-lookup"
        self.assertEqual(self.client.post(path, {}, secure=True).status_code, 401)
        self.assertEqual(self.client.get(path, secure=True, HTTP_AUTHORIZATION="Bearer " + TOKEN).status_code, 405)
        self.assertEqual(self.client.post(path, {}, secure=True, HTTP_AUTHORIZATION="Bearer " + TOKEN).status_code, 415)
        self.assertEqual(self.client.post(path, {}, HTTP_AUTHORIZATION="Bearer " + TOKEN).status_code, 403)
        self.assertEqual(self.client.post(path, {}, HTTP_X_FORWARDED_PROTO="https", HTTP_AUTHORIZATION="Bearer " + TOKEN).status_code, 403)

    def test_duplicate_json_keys_and_large_bodies_are_rejected(self):
        for raw in ('{"accountnumber":"9990000001","accountnumber":"9990000002"}', '[1]', 'null'):
            result = self.client.post("/vas/account-lookup", raw, content_type="application/json", secure=True, HTTP_AUTHORIZATION="Bearer " + TOKEN)
            self.assertEqual(result.status_code, 400)
        self.assertEqual(self.post("account-lookup", {"value": "x" * 20000}).status_code, 413)

    def test_wrong_keys_never_return_partial_identity(self):
        with override_settings(WEMA_VAS={**LIVE, "IDENTITY_KEYS": [Fernet.generate_key().decode()]}):
            self.assertEqual(self.post("kyc-details").status_code, 503)
            self.assertEqual(self.post("account-lookup").status_code, 503)

    def test_token_and_live_prefix_configuration_fail_closed(self):
        for settings in ({**LIVE, "TOKEN": "short"}, {**LIVE, "PREFIX": "711"}):
            with override_settings(WEMA_VAS=settings):
                self.assertEqual(self.post("account-lookup").status_code, 503)

    def test_fernet_rotation_can_read_previous_key(self):
        other = Fernet.generate_key().decode()
        with override_settings(WEMA_VAS={**LIVE, "IDENTITY_KEYS": [other, KEY]}):
            self.assertEqual(decrypt_identity(self.account.encrypted_identity)["bvn"], "12345678901")

    def test_account_and_receipt_identity_are_immutable(self):
        self.account.number = "9990000009"
        with self.assertRaises(ValidationError):
            self.account.save()
        self.account.refresh_from_db()
        self.post("transaction-notification", payload(self.account))
        row = Receipt.objects.get()
        row.amount = Decimal("1")
        with self.assertRaises(ValidationError):
            row.save()


@override_settings(WEMA_VAS=VALIDATION, ROOT_URLCONF="wema_vas.urls", TESTING=True,
                   ALLOWED_HOSTS=["testserver"], SECURE_SSL_REDIRECT=False)
class VasValidationTests(TestCase):
    def test_validation_credit_never_touches_customer_wallet_ledger_or_alerts(self):
        user, account = account_fixture(mode="validation")
        body = payload(account)
        self.assertEqual(process_notification(body)[0]["status"], "00")
        self.assertEqual(process_notification(body)[0]["status"], "00")
        account.refresh_from_db()
        self.assertEqual(account.validation_balance, Decimal("1250"))
        self.assertEqual(Wallet.objects.get(user=user).balance, 0)
        self.assertFalse(Transaction.objects.exists())
        self.assertFalse(TransactionAlertDelivery.objects.exists())
        self.assertEqual(Receipt.objects.get().state, Receipt.VALIDATION)
        # The simulated balance creates no spendable ledger credit and does not
        # turn this profile into a live VAS customer. Other rails keep their gates.
        self.assertIsNone(assert_can_spend(user))


@skipUnlessDBFeature("has_select_for_update")
@override_settings(WEMA_VAS=LIVE, TESTING=True, TXN_ALERTS={})
class VasPostgresConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.user, self.account = account_fixture()

    def race(self, bodies):
        barrier = Barrier(len(bodies))
        def receive(body):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                try:
                    return process_notification(body)[0]["status"]
                except ReplayConflict:
                    return "conflict"
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=len(bodies)) as pool:
            return list(pool.map(receive, bodies))

    def test_simultaneous_identical_notifications_book_one_credit(self):
        self.assertEqual(self.race([payload(self.account)] * 2), ["00", "00"])
        self.assertEqual(Transaction.objects.count(), 1)
        self.assertEqual(Receipt.objects.count(), 1)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("1250"))

    def test_cross_account_payment_collision_credits_only_one_wallet(self):
        _, other = account_fixture("2")
        outcomes = self.race([payload(self.account), payload(other, sessionid="OTHER-SESSION")])
        self.assertCountEqual(outcomes, ["00", "conflict"])
        self.assertEqual(Transaction.objects.count(), 1)
        self.assertEqual(Receipt.objects.count(), 1)
        self.assertEqual(sum(Wallet.objects.values_list("balance", flat=True)), Decimal("1250"))

    def test_database_prevents_receipt_rewrite_and_delete(self):
        process_notification(payload(self.account))
        for action in (lambda: Receipt.objects.update(amount=Decimal("1")), lambda: Receipt.objects.all().delete()):
            with self.assertRaises(IntegrityError), transaction.atomic():
                action()

    def test_database_retains_approval_evidence_and_account_ownership(self):
        MigrationApproval.objects.create(user=self.user, reference="cutover-evidence", approved_by="bank-and-operator", legacy_account_number="0123456789")
        for action in (lambda: MigrationApproval.objects.update(reference="replacement"),
                       lambda: VirtualAccount.objects.update(number="9999999999")):
            with self.assertRaises(IntegrityError), transaction.atomic():
                action()
