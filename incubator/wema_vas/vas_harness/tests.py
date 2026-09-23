import importlib
import os
import socket
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import OperationalError, connection, connections
from django.test import Client, SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from .contracts import InvalidPayload, search_findings, search_request
from .fixtures import CUSTOMERS, seed_customers
from .models import Inflow, VirtualAccount
from .services import reconcile_snapshot

TOKEN = "synthetic-test-token-never-used-in-production-0123456789"
NUMBER = "7110000001"
ENDPOINTS = ["account-lookup", "transaction-notification", "mini-statement", "kyc-details", "block-account"]


def payload(**updates):
    result = {
        "originatoraccountnumber": "0000000000", "originatorname": "SYNTHETIC SENDER",
        "bankcode": "000000", "bankname": "SYNTHETIC BANK", "amount": "3100.05",
        "narration": "Synthetic test only", "paymentreference": "SIM-PAYMENT-001",
        "sessionid": "SIM-SESSION-001", "craccount": NUMBER,
        "craccountname": CUSTOMERS[NUMBER]["name"],
        "created_at": "2026-01-20T16:15:14.983",
    }
    result.update(updates)
    return result


class APIHelpers:
    def post(self, route, data=None, **extra):
        return self.client.post("/vas/" + route, data or {"accountnumber": NUMBER},
                                content_type="application/json", HTTP_AUTHORIZATION="Bearer " + TOKEN, **extra)


@override_settings(VAS_ENABLED=True, VAS_TOKEN=TOKEN)
class ContractTests(APIHelpers, TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_customers()

    def test_all_three_static_accounts_return_identity_and_vendor_prefix(self):
        for number, expected in CUSTOMERS.items():
            with self.subTest(number=number):
                result = self.post("account-lookup", {"accountnumber": number}).json()
                self.assertEqual(result, {"accountname": expected["name"], "bvn": expected["bvn"],
                                         "nin": expected["nin"], "status": "00", "status_desc": "Okay"})
                self.assertTrue(result["bvn"] or result["nin"])
                self.assertNotIn("amount", result)

    def test_invalid_unknown_or_production_accounts_are_not_found(self):
        for route in ["account-lookup", "kyc-details", "mini-statement", "block-account"]:
            for number in ["1234567890", "7119999999", "abc", "", 7110000001, "٧١١٠٠٠٠٠٠١"]:
                with self.subTest(route=route, number=number):
                    result = self.post(route, {"accountnumber": number, "blockreason": "test"})
                    self.assertEqual(result.json(), {"status": "07", "status_desc": "Invalid Account"})

    def test_non_fixture_account_cannot_be_saved(self):
        with self.assertRaises(ValidationError):
            VirtualAccount.objects.create(number="7119999999")

    def test_synthetic_inflow_to_balance_kyc_and_statement(self):
        result = self.post("transaction-notification", payload())
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["status"], "00")
        self.assertEqual(self.post("kyc-details").json()["walletbalance"], "3100.05")
        transactions = self.post("mini-statement").json()["transactions"]
        self.assertEqual(len(transactions), 1)
        self.assertEqual(transactions[0]["direction"], "Credit")
        self.assertEqual(transactions[0]["accountNo"], "0000000000")
        self.assertEqual(transactions[0]["amount"], "3100.05")

    def test_duplicate_is_acknowledged_with_same_reference_only_once(self):
        first = self.post("transaction-notification", payload()).json()
        for _ in range(5):
            self.assertEqual(self.post("transaction-notification/", payload()).json(), first)
        self.assertEqual(Inflow.objects.count(), 1)
        self.assertEqual(VirtualAccount.objects.get(number=NUMBER).simulated_balance, Decimal("3100.05"))

    def test_numerically_equal_amount_is_same_replay(self):
        first = self.post("transaction-notification", payload(amount="10")).json()
        self.assertEqual(self.post("transaction-notification", payload(amount="10.00")).json(), first)

    def test_same_session_changed_amount_or_destination_or_source_rejected(self):
        self.post("transaction-notification", payload())
        for changes in [{"amount": "3100.06"}, {"paymentreference": "DIFFERENT"},
                        {"originatoraccountnumber": "0000000001"},
                        {"craccount": "7110000002", "craccountname": CUSTOMERS["7110000002"]["name"]}]:
            with self.subTest(changes=changes):
                self.assertEqual(self.post("transaction-notification", payload(**changes)).status_code, 409)
        self.assertEqual(Inflow.objects.count(), 1)
        self.assertEqual(VirtualAccount.objects.get(number="7110000002").simulated_balance, Decimal("0"))

    def test_same_payment_reference_with_different_session_is_not_a_new_credit(self):
        self.post("transaction-notification", payload())
        self.assertEqual(self.post("transaction-notification", payload(sessionid="SIM-SESSION-002")).status_code, 409)
        self.assertEqual(Inflow.objects.count(), 1)

    def test_invalid_amounts_are_rejected_without_rows(self):
        for amount in ["0", "-1", "NaN", "Infinity", "1e2", "1.001", "1,000", "1000000000000", 12.3, True, " 12.00"]:
            with self.subTest(amount=amount):
                self.assertEqual(self.post("transaction-notification", payload(amount=amount)).status_code, 400)
        self.assertEqual(Inflow.objects.count(), 0)

    def test_missing_required_fields_and_wrong_types_are_rejected(self):
        for key in ["craccount", "originatoraccountnumber", "originatorname", "bankcode", "bankname",
                    "paymentreference", "sessionid", "craccountname", "amount", "created_at"]:
            with self.subTest(key=key):
                data = payload()
                data.pop(key)
                self.assertEqual(self.post("transaction-notification", data).status_code, 400)
                self.assertEqual(self.post("transaction-notification", payload(**{key: []})).status_code, 400)

    def test_invalid_and_future_dates_rejected(self):
        for value in ["invalid", "2026-99-99T99:99:99", (timezone.now() + timedelta(days=1)).isoformat()]:
            with self.subTest(value=value):
                self.assertEqual(self.post("transaction-notification", payload(created_at=value)).status_code, 400)

    def test_account_name_mismatch_rejected(self):
        self.assertEqual(self.post("transaction-notification", payload(craccountname="Wrong Customer")).status_code, 400)
        self.assertEqual(Inflow.objects.count(), 0)

    def test_unknown_notification_account_not_acknowledged(self):
        result = self.post("transaction-notification", payload(craccount="7119999999"))
        self.assertEqual(result.json()["status"], "07")
        self.assertEqual(Inflow.objects.count(), 0)

    def test_block_is_idempotent_and_retains_existing_statement_and_kyc(self):
        self.post("transaction-notification", payload())
        result = self.post("block-account", {"accountnumber": NUMBER, "blockreason": "Synthetic fraud test"})
        self.assertEqual(result.json(), {"message": "Account Restricted Successfully"})
        account = VirtualAccount.objects.get(number=NUMBER)
        self.post("block-account", {"accountnumber": NUMBER, "blockreason": "Retry"})
        account.refresh_from_db()
        self.assertEqual(account.block_reason, "Synthetic fraud test")
        self.assertEqual(self.post("account-lookup").json(), {"status": "07", "status_desc": "Inactive Account"})
        self.assertEqual(self.post("kyc-details").json()["status_desc"], "Inactive")
        self.assertEqual(self.post("kyc-details").json()["walletbalance"], "3100.05")
        self.assertEqual(len(self.post("mini-statement").json()["transactions"]), 1)

    def test_already_applied_replay_after_block_still_returns_00(self):
        first = self.post("transaction-notification", payload()).json()
        self.post("block-account", {"accountnumber": NUMBER, "blockreason": "Test"})
        self.assertEqual(self.post("transaction-notification", payload()).json(), first)

    def test_new_notification_after_block_is_held_without_value_or_success_ack(self):
        self.post("block-account", {"accountnumber": NUMBER, "blockreason": "Test"})
        for _ in range(2):
            result = self.post("transaction-notification", payload())
            self.assertEqual(result.status_code, 409)
            self.assertEqual(result.json()["status"], "07")
        self.assertEqual(Inflow.objects.filter(held=True).count(), 1)
        self.assertEqual(self.post("kyc-details").json()["walletbalance"], "0.00")
        self.assertEqual(self.post("mini-statement").json(), {"transactions": []})

    def test_block_reason_limits(self):
        for reason in ["", " ", "x" * 201, None, 1]:
            with self.subTest(reason=reason):
                self.assertEqual(self.post("block-account", {"accountnumber": NUMBER, "blockreason": reason}).status_code, 400)
        self.assertEqual(self.post("block-account", {"accountnumber": NUMBER, "blockreason": "x" * 200}).status_code, 200)

    def test_statement_window_anchored_to_last_transaction_not_today(self):
        for index, date in enumerate(["2026-01-10T23:59:59", "2026-01-11T00:00:00", "2026-01-20T16:00:00"]):
            self.post("transaction-notification", payload(sessionid=f"SIM-{index}", paymentreference=f"SIM-P-{index}", created_at=date))
        self.assertEqual(len(self.post("mini-statement").json()["transactions"]), 2)

    def test_statement_does_not_mix_accounts(self):
        self.post("transaction-notification", payload())
        self.assertEqual(self.post("mini-statement", {"accountnumber": "7110000002"}).json(), {"transactions": []})

    def test_database_failure_never_acknowledges_and_rolls_back(self):
        with patch("django.db.models.query.QuerySet.update", side_effect=OperationalError("synthetic")):
            self.assertEqual(self.post("transaction-notification", payload()).status_code, 503)
        self.assertEqual(Inflow.objects.count(), 0)
        self.assertEqual(VirtualAccount.objects.get(number=NUMBER).simulated_balance, Decimal("0"))
        self.assertEqual(self.post("transaction-notification", payload()).json()["status"], "00")

    def test_balance_overflow_rolls_back_receipt(self):
        VirtualAccount.objects.filter(number=NUMBER).update(simulated_balance=Decimal("999999999999.99"))
        self.assertEqual(self.post("transaction-notification", payload()).status_code, 400)
        self.assertEqual(Inflow.objects.count(), 0)

    def test_seed_does_not_reset_balance_or_unblock(self):
        self.post("transaction-notification", payload())
        self.post("block-account", {"accountnumber": NUMBER, "blockreason": "Test"})
        seed_customers()
        account = VirtualAccount.objects.get(number=NUMBER)
        self.assertFalse(account.active)
        self.assertEqual(account.simulated_balance, Decimal("3100.05"))

    def test_inflow_model_rejects_mutation(self):
        self.post("transaction-notification", payload())
        row = Inflow.objects.get()
        row.amount = Decimal("1.00")
        with self.assertRaises(ValidationError):
            row.save()

    def test_no_production_tables_exist(self):
        tables = set(connection.introspection.table_names())
        self.assertEqual(tables, {"django_migrations", "vas_harness_inflow", "vas_harness_virtualaccount"})

    def snapshot(self, **updates):
        row = {"sessionid": "SIM-SESSION-001", "craccount": NUMBER, "amount": "3100.05",
               "nibssresponse": "00", "sendresponse": "00"}
        row.update(updates)
        return {"status": "00", "transactions": [row]}

    def test_shadow_reconciliation_matches_without_writing(self):
        self.post("transaction-notification", payload())
        result = reconcile_snapshot(self.snapshot())[0]
        self.assertEqual(result["local_comparison"], "matched")
        self.assertEqual(Inflow.objects.count(), 1)
        self.assertEqual(VirtualAccount.objects.get(number=NUMBER).simulated_balance, Decimal("3100.05"))

    def test_shadow_missing_receipt_does_not_auto_credit(self):
        result = reconcile_snapshot(self.snapshot(sendresponse=""))[0]
        self.assertEqual(result["local_comparison"], "missing_receipt_request_bank_repush")
        self.assertEqual(Inflow.objects.count(), 0)
        self.assertEqual(VirtualAccount.objects.get(number=NUMBER).simulated_balance, Decimal("0"))

    def test_shadow_mismatch_does_not_correct_balance(self):
        self.post("transaction-notification", payload())
        for changes in [{"amount": "100.00"}, {"craccount": "7110000002"}]:
            self.assertEqual(reconcile_snapshot(self.snapshot(**changes))[0]["local_comparison"], "conflict_manual_review")
        self.assertEqual(VirtualAccount.objects.get(number=NUMBER).simulated_balance, Decimal("3100.05"))

    def test_shadow_unknown_status_does_not_refund(self):
        self.post("transaction-notification", payload())
        self.assertEqual(reconcile_snapshot(self.snapshot(nibssresponse="99"))[0]["local_comparison"], "unresolved_no_balance_change")
        self.assertEqual(VirtualAccount.objects.get(number=NUMBER).simulated_balance, Decimal("3100.05"))

    def test_shadow_does_not_release_held_receipt(self):
        self.post("block-account", {"accountnumber": NUMBER, "blockreason": "Test"})
        self.post("transaction-notification", payload())
        self.assertEqual(reconcile_snapshot(self.snapshot())[0]["local_comparison"], "held_manual_review")
        self.assertTrue(Inflow.objects.get().held)
        self.assertEqual(VirtualAccount.objects.get(number=NUMBER).simulated_balance, Decimal("0"))

    def test_shadow_rejects_non_fixture_account(self):
        with self.assertRaises(InvalidPayload):
            reconcile_snapshot(self.snapshot(craccount="9990000001"))


@override_settings(VAS_ENABLED=True, VAS_TOKEN=TOKEN)
class AuthenticationTests(APIHelpers, SimpleTestCase):
    def test_disabled_flag_hides_all_routes_before_database_access(self):
        with override_settings(VAS_ENABLED=False):
            for route in ENDPOINTS:
                self.assertEqual(self.post(route).status_code, 404)

    def test_missing_or_wrong_bearer_rejected_on_every_endpoint(self):
        for route in ENDPOINTS:
            for header in ["", "Bearer wrong", "Basic " + TOKEN, "Bearer", "Bearer " + TOKEN + " extra", "Bearer ünicode"]:
                with self.subTest(route=route, header=header):
                    result = self.client.post("/vas/" + route, {}, content_type="application/json", HTTP_AUTHORIZATION=header)
                    self.assertEqual(result.status_code, 401)

    def test_empty_or_short_token_fails_closed(self):
        for token in ["", "short"]:
            with override_settings(VAS_TOKEN=token):
                self.assertEqual(self.post("account-lookup").status_code, 503)

    def test_get_does_not_execute_any_endpoint(self):
        for route in ENDPOINTS:
            self.assertEqual(self.client.get("/vas/" + route, HTTP_AUTHORIZATION="Bearer " + TOKEN).status_code, 405)

    def test_non_json_rejected(self):
        self.assertEqual(self.client.post("/vas/account-lookup", "{}", content_type="text/plain", HTTP_AUTHORIZATION="Bearer " + TOKEN).status_code, 415)

    def test_remote_peer_and_forged_forwarded_header_rejected(self):
        for ip in ["203.0.113.1", "", "unknown"]:
            self.assertEqual(self.post("account-lookup", REMOTE_ADDR=ip, HTTP_X_FORWARDED_FOR="127.0.0.1").status_code, 403)

    def test_non_synthetic_mode_fails_closed(self):
        with override_settings(VAS_MODE="live"):
            self.assertEqual(self.post("account-lookup").status_code, 503)

    def test_incorrect_database_engine_fails_closed(self):
        with patch.dict(settings.DATABASES["default"], ENGINE="django.db.backends.postgresql"):
            self.assertEqual(self.post("account-lookup").status_code, 503)

    def test_oversized_payload_rejected(self):
        self.assertEqual(self.post("account-lookup", {"accountnumber": "x" * 17000}).status_code, 413)

    def test_malformed_array_or_duplicate_json_keys_rejected(self):
        for value in ["[1]", "null", "broken", '{"accountnumber":"7110000001","accountnumber":"7110000002"}']:
            self.assertEqual(self.client.post("/vas/account-lookup", value, content_type="application/json", HTTP_AUTHORIZATION="Bearer " + TOKEN).status_code, 400)

    def test_sensitive_responses_are_not_cacheable(self):
        with override_settings(VAS_ENABLED=False):
            result = self.post("kyc-details")
            self.assertEqual(result["Cache-Control"], "no-store")
            self.assertEqual(result["X-Zitch-VAS-Mode"], "synthetic-only")

    def test_outbound_routes_do_not_exist(self):
        for path in ["outbound", "transfer", "payout", "outward-tsq"]:
            self.assertEqual(self.post(path).status_code, 404)


class SearchTests(SimpleTestCase):
    def test_request_requires_one_selector(self):
        self.assertEqual(search_request(session_id="SIM-1"), {"sessionid": "SIM-1"})
        self.assertEqual(search_request(account=NUMBER), {"craccount": NUMBER})
        for kwargs in [{}, {"account": NUMBER, "session_id": "SIM-1"}, {"account": "invalid"}]:
            with self.assertRaises(InvalidPayload):
                search_request(**kwargs)

    def test_bank_statuses_are_read_only_findings_not_money_movements(self):
        for nibss, send, expected in [("00", "00", "acknowledged"), ("00", "", "notification_repush_required"),
                                      ("00", "07", "notification_repush_required"), ("99", "00", "uncertain_contact_bank")]:
            result = search_findings({"status": "00", "transactions": [{"sessionid": "SIM-1", "craccount": NUMBER,
                                                                         "nibssresponse": nibss, "sendresponse": send}]})
            self.assertEqual(result[0]["outcome"], expected)

    def test_invalid_search_response_is_not_success(self):
        for body in [{"status": "07"}, {"status": "00", "transactions": {}}, {"status": "00", "transactions": [{}]}, []]:
            with self.assertRaises(InvalidPayload):
                search_findings(body)


class IsolationTests(SimpleTestCase):
    def test_no_network_is_possible_under_test_runner(self):
        with self.assertRaises(AssertionError):
            socket.create_connection(("example.invalid", 443))

    def test_prod_settings_do_not_import_harness(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[3]
        for name in ["backend/zitch_api/settings.py", "backend/zitch_api/urls.py", "render.yaml"]:
            self.assertNotIn("vas_harness", (root / name).read_text(encoding="utf-8-sig"))

    def test_settings_refuse_render_or_external_database(self):
        for name in ["DATABASE_URL", "RENDER", "RENDER_EXTERNAL_HOSTNAME"]:
            with self.subTest(name=name), patch.dict(os.environ, {name: "forbidden"}):
                spec = importlib.util.find_spec("vas_harness.settings")
                module = importlib.util.module_from_spec(spec)
                with self.assertRaises(ImproperlyConfigured):
                    spec.loader.exec_module(module)


@override_settings(VAS_ENABLED=True, VAS_TOKEN=TOKEN)
class ConcurrentRetryTests(APIHelpers, TransactionTestCase):
    def test_concurrent_duplicates_either_ack_once_or_request_retry(self):
        seed_customers()

        def attempt(_):
            connections.close_all()
            try:
                result = Client().post("/vas/transaction-notification", payload(), content_type="application/json",
                                       HTTP_AUTHORIZATION="Bearer " + TOKEN)
                return result.status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=4) as pool:
            statuses = list(pool.map(attempt, range(8)))
        self.assertTrue(all(status in {200, 503} for status in statuses), statuses)
        # SQLite may make every overlapping writer retry; one sequential replay
        # must then succeed, with exactly one durable record and one credit.
        self.assertEqual(self.post("transaction-notification", payload()).json()["status"], "00")
        self.assertEqual(Inflow.objects.count(), 1)
        self.assertEqual(VirtualAccount.objects.get(number=NUMBER).simulated_balance, Decimal("3100.05"))
