"""Bank query transport, no-financial-write and operator boundary regressions."""
import json
import os
import re
from io import StringIO
from unittest.mock import patch

import requests
from django.contrib.auth.models import AnonymousUser
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from wallet.models import Transaction, TransactionAlertDelivery, Wallet
from wema_vas.contracts import InvalidPayload
from wema_vas.models import Receipt
from wema_vas.operator_queries import query_page
from wema_vas.services import process_notification
from wema_vas.tests import LIVE, account_fixture, payload
from wema_vas.transaction_query import (ENDPOINTS, MAX_RESPONSE_BYTES, NoBankCredentials, QueryUnavailable,
                                       fetch_query, query_and_reconcile, query_configuration)

VALUES = {**LIVE, **{key: url for key, url in ENDPOINTS.values()}}


def mock_transport(test, body=None, raw=None, status=200, content_type="application/json; charset=utf-8"):
    context = patch("wema_vas.transaction_query.requests.Session")
    factory = context.start()
    test.addCleanup(context.stop)
    client = factory.return_value.__enter__.return_value
    response = client.post.return_value.__enter__.return_value
    response.status_code = status
    response.headers = {"Content-Type": content_type}
    response.iter_content.return_value = [raw if raw is not None else json.dumps(body).encode()]
    return client, response


@override_settings(WEMA_VAS=VALUES)
class QueryTransportTests(SimpleTestCase):
    def test_both_rails_send_exact_body_without_callback_or_operator_credentials(self):
        client, _ = mock_transport(self, {"status": "02", "status_desc": " No data found.", "transactions": None})
        for rail, (_key, url) in ENDPOINTS.items():
            for args, body in (({"account": "9081059048"}, {"craccount": "9081059048"}),
                               ({"session_id": "S-1"}, {"sessionid": "S-1"})):
                self.assertEqual(fetch_query(rail, **args), {"status": "00", "transactions": []})
                client.post.assert_called_with(url, json=body, headers={"Accept": "application/json"},
                                               timeout=(4, 10), allow_redirects=False, stream=True)
                self.assertIsInstance(client.auth, NoBankCredentials)

    def test_only_exact_bank_endpoints_are_accepted_before_network(self):
        client, _ = mock_transport(self, {})
        for url in ("http://apps3.wemabank.com/FintechTransQuery/api/v1/Trans/TransQuery",
                    ENDPOINTS["nip"][1] + "?token=secret", ENDPOINTS["etranzact"][1],
                    "https://evil.example/query", "http://169.254.169.254/", ""):
            with override_settings(WEMA_VAS={**VALUES, "NIP_QUERY_URL": url}):
                with self.assertRaises(QueryUnavailable):
                    fetch_query("nip", session_id="S")
        client.post.assert_not_called()

    def test_invalid_scope_rail_and_response_are_rejected(self):
        client, response = mock_transport(self, {})
        for args in ({}, {"account": "bad"}, {"account": "9081059048", "session_id": "S"}):
            with self.assertRaises(InvalidPayload):
                fetch_query("nip", **args)
        with self.assertRaises(InvalidPayload):
            fetch_query("outward", session_id="S")
        client.post.assert_not_called()
        for raw in (b'<html>login</html>', b'{"status":"00","status":"07"}', b'[]',
                    b'{"status":"02","status_desc":"Error","transactions":null}',
                    b'{"status":"02","status_desc":"No data found.","transactions":[{}]}',
                    b'x' * (MAX_RESPONSE_BYTES + 1)):
            response.iter_content.return_value = [raw]
            with self.assertRaises(QueryUnavailable):
                fetch_query("nip", session_id="S")

    def test_redirect_http_error_content_type_and_timeout_are_not_success(self):
        client, response = mock_transport(self, {})
        for code in (301, 302, 401, 403, 429, 500):
            response.status_code = code
            with self.assertRaises(QueryUnavailable):
                fetch_query("nip", session_id="S")
        response.status_code = 200
        response.headers = {"Content-Type": "text/html"}
        with self.assertRaises(QueryUnavailable):
            fetch_query("nip", session_id="S")
        client.post.side_effect = requests.Timeout("private-token-and-bank-body")
        with self.assertRaises(QueryUnavailable) as exc:
            fetch_query("nip", session_id="S")
        self.assertNotIn("private-token", str(exc.exception))

    def test_configuration_report_contains_booleans_only(self):
        self.assertEqual(query_configuration(VALUES), {"nip_query_configured": True, "etranzact_query_configured": True})
        self.assertEqual(query_configuration({}), {"nip_query_configured": False, "etranzact_query_configured": False})


@override_settings(WEMA_VAS=VALUES, TESTING=True, TXN_ALERTS={})
class QueryReconciliationTests(TestCase):
    def setUp(self):
        self.user, self.account = account_fixture()
        row = payload(self.account)
        row.pop("created_at")
        row.update(requestdate="2026-01-20", nibssresponse="00", sendresponse="00")
        self.body = {"status": "00", "transactions": [row]}

    def test_successful_nip_rows_require_receipt_and_never_write_money(self):
        mock_transport(self, self.body)
        def read_only(execute, sql, params, many, context):
            self.assertIsNone(re.match(r"\s*(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP)\b", sql, re.I))
            return execute(sql, params, many, context)
        with connection.execute_wrapper(read_only):
            report = query_and_reconcile("nip", session_id="SESSION-1")
        self.assertEqual(report["counts"], {"missing_receipt_request_bank_repush": 1})
        self.assertFalse(Transaction.objects.exists())
        self.assertFalse(Receipt.objects.exists())
        self.assertFalse(TransactionAlertDelivery.objects.exists())
        self.assertEqual(Wallet.objects.get(user=self.user).balance, 0)
        self.assertTrue(report["source_authenticated"])
        self.assertFalse(report["full_reconciliation_confirmed"])

    def test_matched_nip_is_not_collection_settlement_and_etz_is_not_assumed_nip(self):
        process_notification(payload(self.account))
        mock_transport(self, self.body)
        report = query_and_reconcile("nip", session_id="SESSION-1")
        self.assertEqual(report["status"], "returned_rows_match")
        self.assertFalse(report["scope_complete"])
        etz = query_and_reconcile("etranzact", session_id="SESSION-1")
        self.assertEqual(etz["status"], "review_required")
        self.assertEqual(etz["counts"], {"bank_status_uncertain_contact_support": 1})
        self.assertEqual(Transaction.objects.count(), 1)
        for value in (self.account.number, self.account.display_name, "SESSION-1", "PAYMENT-1"):
            self.assertNotIn(value, json.dumps(report))

    def test_unrelated_or_unrecognized_bank_rows_fail_safely(self):
        _client, response = mock_transport(self, self.body)
        with self.assertRaises(QueryUnavailable):
            query_and_reconcile("nip", session_id="UNRELATED")
        response.iter_content.return_value = [b'{"status":"00","transactions":[{"unknown":"bank-shape"}]}']
        with self.assertRaises(QueryUnavailable):
            query_and_reconcile("etranzact", session_id="SESSION-1")

    def test_no_data_never_proves_transaction_failed_or_successful(self):
        mock_transport(self, {"status": "02", "status_desc": " No data found.", "transactions": None})
        output = StringIO()
        with self.assertRaises(CommandError):
            call_command("vas_query_transactions", "--rail", "nip", "--session-id", "S", stdout=output)
        report = json.loads(output.getvalue())
        self.assertEqual(report["status"], "no_rows_returned")
        self.assertFalse(report["full_reconciliation_confirmed"])
        self.assertFalse(Transaction.objects.exists())


@override_settings(RATELIMIT_ENABLE=False)
class QueryHttpTests(SimpleTestCase):
    def setUp(self):
        context = patch.dict(os.environ, {"DIAG_TOKEN": "operator-secret", "WEMA_DIAG_TOKEN": ""})
        context.start()
        self.addCleanup(context.stop)

    @patch("wema_vas.operator_queries.query_and_reconcile")
    def test_auth_method_and_inputs_precede_bank_calls(self, query):
        self.assertEqual(self.client.get("/vas-transaction-query").status_code, 405)
        self.assertEqual(self.client.post("/vas-transaction-query?token=operator-secret", {}, content_type="application/json").status_code, 403)
        for body in ({"rail": "nip", "url": "https://evil.example"}, [], {"rail": []},
                     {"rail": "nip", "sessionid": "S", "craccount": ""},
                     {"rail": "nip", "sessionid": []}):
            self.assertEqual(self.client.post("/vas-transaction-query", body, content_type="application/json",
                HTTP_AUTHORIZATION="Bearer operator-secret").status_code, 400)
        query.assert_not_called()

    @patch("wema_vas.operator_queries.query_and_reconcile")
    def test_operator_query_is_post_only_no_store_and_no_financial_response(self, query):
        query.return_value = {"read_only": True, "status": "no_rows_returned", "scope_complete": False}
        response = self.client.post("/vas-transaction-query/", {"rail": "nip", "sessionid": "S"},
            content_type="application/json", HTTP_AUTHORIZATION="Bearer operator-secret")
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        query.assert_called_once_with("nip", session_id="S", account="")

    @patch("wema_vas.operator_queries.query_and_reconcile")
    def test_bank_unavailability_is_not_ok_and_admin_page_requires_permission(self, query):
        query.side_effect = QueryUnavailable("Bank query unavailable.")
        response = self.client.post("/vas-transaction-query", {"rail": "nip", "sessionid": "S"},
            content_type="application/json", HTTP_AUTHORIZATION="Bearer operator-secret")
        self.assertEqual(response.status_code, 503)
        request = RequestFactory().get("/admin/vas-transaction-query/")
        request.user = AnonymousUser()
        self.assertEqual(query_page(request).status_code, 403)
