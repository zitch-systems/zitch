"""Which host and which key each Wema product uses, printed without a secret.

On 14 November 2026 the statement endpoint alone was answered with a gateway's
403 page while transfers and airtime went through. Statement and balance reads
are the only calls that honour WEMA_ACCT_MGT_BASE_URL and WEMA_ACCT_MGT_KEY, so
the first question was whether those were set, and to what. Nothing logged it.
"""
import requests
from django.test import SimpleTestCase, override_settings

from utility import wema

BASE = {
    "BASE_URL": "https://gw.example/",
    "CHANNEL_ID": "channel-value-7731",
    "KEYS": {"wallet": "wallet-sub", "airtime": "airtime-sub"},
    "SOURCE_ACCOUNT": "0100000001",
}


def _with(**changes):
    config = {**BASE, **changes}
    return override_settings(WEMA=config)


class ConnectionReportTests(SimpleTestCase):
    @_with()
    def test_products_without_overrides_use_the_base_host_and_the_wallet_key(self):
        report = wema.connection_report()
        self.assertEqual(report["base_host"], "gw.example")
        statement = report["products"]["acct_mgt"]
        self.assertEqual(statement["host"], "gw.example")
        self.assertEqual(statement["key"], "wallet")
        self.assertEqual(statement["key_id"], report["wallet_key"])
        self.assertEqual(report["products"]["debit"]["key_id"], report["wallet_key"])

    @_with(BASE_URLS={"acct_mgt": "https://other-gw.example"},
           KEYS={"wallet": "wallet-sub", "acct_mgt": "statement-sub"})
    def test_an_override_shows_as_a_different_host_and_key(self):
        report = wema.connection_report()
        statement = report["products"]["acct_mgt"]
        self.assertEqual(statement["host"], "other-gw.example")
        self.assertEqual(statement["key"], "own")
        self.assertNotEqual(statement["key_id"], report["wallet_key"])
        # The rail that works is untouched by it.
        self.assertEqual(report["products"]["debit"]["host"], "gw.example")
        self.assertEqual(report["products"]["debit"]["key"], "wallet")

    @_with()
    def test_a_product_with_no_key_says_none(self):
        card = wema.connection_report()["products"]["card"]
        self.assertEqual(card["key"], "none")
        self.assertEqual(card["key_id"], "")

    @_with(BASE_URLS={"acct_mgt": "https://other-gw.example"},
           KEYS={"wallet": "wallet-sub", "acct_mgt": "statement-sub"})
    def test_no_secret_appears_in_the_report_or_the_summary(self):
        text = repr(wema.connection_report()) + wema.connection_summary()
        for secret in ("wallet-sub", "statement-sub", "channel-value-7731"):
            self.assertNotIn(secret, text)

    @_with(BASE_URLS={"acct_mgt": "https://other-gw.example"})
    def test_the_summary_names_each_products_host_and_key_source(self):
        line = wema.connection_summary()
        self.assertIn("base=gw.example", line)
        self.assertIn("acct_mgt=other-gw.example/wallet:", line)
        self.assertIn("debit=gw.example/wallet:", line)
        self.assertNotIn("bnpl=", line)

    @_with(BASE_URL="not a url")
    def test_an_unreadable_base_reports_an_empty_host_without_failing(self):
        self.assertEqual(wema.connection_report()["base_host"], "")


class RefusalNamesTheHostTests(SimpleTestCase):
    @_with(BASE_URLS={"acct_mgt": "https://other-gw.example"})
    def test_a_non_json_answer_logs_the_host_and_server(self):
        resp = requests.Response()
        resp.status_code = 403
        resp._content = b"<html>blocked</html>"
        resp.headers["Content-Type"] = "text/html"
        resp.headers["Server"] = "Microsoft-Azure-Application-Gateway/v2"
        with self.assertLogs("zitch", level="WARNING") as logs:
            wema._note_non_json(
                "acct_mgt", "/api/AccountMaintenance/CustomerAccount/transhistoryV2", resp)
        line = logs.output[0]
        self.assertIn("host=other-gw.example", line)
        self.assertIn("server=Microsoft-Azure-Application-Gateway/v2", line)
        self.assertIn("request_id=-", line)
        self.assertNotIn("blocked", line)

    @_with()
    def test_the_gateway_request_id_is_logged_for_the_bank_to_trace(self):
        resp = requests.Response()
        resp.status_code = 403
        resp._content = b"<html></html>"
        resp.headers["apim-request-id"] = "0f8fad5b-d9cb-469f-a165-70867728950e"
        with self.assertLogs("zitch", level="WARNING") as logs:
            wema._note_non_json("acct_mgt", "/api/AccountMaintenance/CustomerAccount/transhistoryV2", resp)
        self.assertIn("request_id=0f8fad5b-d9cb-469f-a165-70867728950e", logs.output[0])

    @_with()
    def test_a_malformed_request_id_is_not_logged(self):
        resp = requests.Response()
        resp.status_code = 403
        resp._content = b"<html></html>"
        resp.headers["apim-request-id"] = "x\nforged=1"
        with self.assertLogs("zitch", level="WARNING") as logs:
            wema._note_non_json("acct_mgt", "/api/AccountMaintenance/CustomerAccount/transhistoryV2", resp)
        self.assertIn("request_id=-", logs.output[0])
        self.assertNotIn("forged", logs.output[0])
