"""Focused safety tests for the MCP money-tool request contract.

The production dependencies are intentionally stubbed here so this small test
suite can run in a backend checkout without installing the MCP SDK or httpx.
"""
from __future__ import annotations

import importlib
import sys
import types
import unittest
from unittest.mock import patch


class _FastMCP:
    def __init__(self, _name):
        pass

    def tool(self):
        return lambda fn: fn

    def run(self):  # pragma: no cover - CLI behavior is outside these unit tests
        pass


def _load_server():
    httpx = types.ModuleType("httpx")
    httpx.HTTPError = Exception
    fastmcp = types.ModuleType("mcp.server.fastmcp")
    fastmcp.FastMCP = _FastMCP
    server_package = types.ModuleType("mcp.server")
    mcp_package = types.ModuleType("mcp")
    with patch.dict(sys.modules, {
        "httpx": httpx,
        "mcp": mcp_package,
        "mcp.server": server_package,
        "mcp.server.fastmcp": fastmcp,
    }):
        sys.modules.pop("server", None)
        return importlib.import_module("server")


server = _load_server()


class _Response:
    def __init__(self, status_code, payload=None, *, invalid_json=False):
        self.status_code = status_code
        self.payload = payload
        self.invalid_json = invalid_json

    def json(self):
        if self.invalid_json:
            raise ValueError("not json")
        return self.payload


class _Client:
    def __init__(self, *, response=None, error=None):
        self.response = response
        self.error = error

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def post(self, *_args, **_kwargs):
        if self.error is not None:
            raise self.error
        return self.response


class CallOutcomeTests(unittest.TestCase):
    def call(self, response=None, *, error=None, money=True):
        client = _Client(response=response, error=error)
        with patch.object(server.httpx, "Client", return_value=client, create=True):
            return server._call("/api/test-money/", {"amount": "100"}, money=money)

    def test_transport_error_keeps_the_original_request_identity(self):
        result = self.call(error=server.httpx.HTTPError("timed out"))

        self.assertFalse(result["success"])
        self.assertTrue(result["unknown"])
        self.assertEqual(result["outcome"], "unknown")
        self.assertTrue(result["transport_error"])
        self.assertTrue(result["retry_with_same_request_id"])
        self.assertIn("Do not create a new request_id", result["retry_instruction"])

    def test_transient_http_status_overrides_a_success_body(self):
        for status in (408, 425, 429, 500, 502, 503):
            with self.subTest(status=status):
                result = self.call(_Response(status, {"success": True, "reference": "REF-1"}))
                self.assertFalse(result["success"])
                self.assertTrue(result["unknown"])
                self.assertEqual(result["outcome"], "unknown")
                self.assertEqual(result["http_status"], status)
                self.assertTrue(result["retry_with_same_request_id"])

    def test_known_pre_execution_429_is_a_definitive_rejection(self):
        for code in ("pin_locked", "velocity", "rate_limited"):
            with self.subTest(code=code):
                result = self.call(_Response(429, {
                    "success": True,
                    "code": code,
                    "message": "Try later",
                }))
                self.assertFalse(result["success"])
                self.assertEqual(result["outcome"], "failed")
                self.assertNotIn("unknown", result)

    def test_transient_gateway_status_wins_over_stale_pre_execution_code(self):
        for status in (408, 425, 500, 502, 503):
            with self.subTest(status=status):
                result = self.call(_Response(status, {
                    "success": False,
                    "code": "pin_locked",
                    "message": "stale body",
                }))
                self.assertEqual(result["outcome"], "unknown")
                self.assertTrue(result["retry_with_same_request_id"])

    def test_invalid_money_response_is_unknown_not_a_safe_new_attempt(self):
        result = self.call(_Response(200, invalid_json=True))

        self.assertFalse(result["success"])
        self.assertTrue(result["unknown"])
        self.assertEqual(result["http_status"], 200)
        self.assertTrue(result["retry_with_same_request_id"])

    def test_definitive_4xx_and_explicit_2xx_outcomes_remain_distinct(self):
        failed = self.call(_Response(422, {"message": "Rejected"}))
        pending = self.call(_Response(200, {"pending": True, "reference": "REF-P"}))
        succeeded = self.call(_Response(200, {"success": True, "reference": "REF-S"}))

        self.assertEqual(failed["outcome"], "failed")
        self.assertEqual(pending["outcome"], "pending")
        self.assertFalse(pending["success"])
        self.assertTrue(pending["retry_with_same_request_id"])
        self.assertEqual(succeeded["outcome"], "success")

    def test_unauthorized_money_call_is_definitive_and_preserves_status(self):
        result = self.call(_Response(401, invalid_json=True))

        self.assertFalse(result["success"])
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["code"], "unauthorized")
        self.assertEqual(result["http_status"], 401)
        self.assertNotIn("retry_with_same_request_id", result)

    def test_http_status_is_preserved_for_read_calls_too(self):
        result = self.call(_Response(200, {"success": True}), money=False)
        self.assertTrue(result["success"])
        self.assertEqual(result["http_status"], 200)
        self.assertTrue(result["http_ok"])


class IdempotencyKeyTests(unittest.TestCase):
    def test_key_is_stable_bounded_and_scoped_to_operation(self):
        request_id = "f0ea56ce-a227-4ad8-b2af-ded7ac61f34e"
        first = server._idempotency_key("bank", request_id)
        self.assertEqual(first, server._idempotency_key("bank", request_id))
        self.assertNotEqual(first, server._idempotency_key("p2p", request_id))
        self.assertLessEqual(len(first), 80)

    def test_outer_whitespace_is_normalized(self):
        self.assertEqual(
            server._idempotency_key("bank", " request-123 "),
            server._idempotency_key("bank", "request-123"),
        )

    def test_invalid_request_ids_are_rejected(self):
        for value in ("", "short", "contains spaces", "x" * 129, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                server._idempotency_key("bank", value)


class MoneyToolTests(unittest.TestCase):
    def setUp(self):
        self.request_id = "request-20260918-0001"

    def test_bank_retry_forwards_the_same_key(self):
        with patch.object(server, "_call", return_value={"success": True}) as call:
            for _ in range(2):
                server.send_to_bank("0123456789", "bank-code", "5000", "1234", self.request_id)

        first_body = call.call_args_list[0].args[1]
        second_body = call.call_args_list[1].args[1]
        self.assertEqual(first_body, second_body)
        self.assertEqual(
            first_body["idempotency_key"],
            server._idempotency_key("bank", self.request_id),
        )

    def test_every_money_tool_forwards_a_derived_key(self):
        cases = (
            (server.send_to_zitch_user, ("08012345678", "100", "1234", self.request_id), "p2p"),
            (server.fund_from_linked_bank, (7, "100", self.request_id), "linked-fund"),
        )
        for fn, args, operation in cases:
            with self.subTest(tool=fn.__name__), patch.object(
                server, "_call", return_value={"success": True}
            ) as call:
                fn(*args)
                self.assertEqual(
                    call.call_args.args[1]["idempotency_key"],
                    server._idempotency_key(operation, self.request_id),
                )
                self.assertTrue(call.call_args.kwargs["money"])


class DedicatedFundingAccountTests(unittest.TestCase):
    def test_fund_wallet_returns_bank_transfer_instructions_without_starting_checkout(self):
        account = {
            "success": True,
            "account_number": "0123456789",
            "account_name": "ADA EZE",
            "bank_name": "Partner Bank",
        }
        with patch.object(server, "_call", return_value=account) as call:
            result = server.fund_wallet()

        call.assert_called_once_with("/api/wallet/account/")
        self.assertEqual(result["funding_method"], "bank_transfer")
        self.assertEqual(result["account_number"], "0123456789")
        self.assertNotIn("authorization_url", result)
        self.assertIn("credited automatically", result["message"])

    def test_fund_wallet_requires_account_setup_when_no_nuban_exists(self):
        with patch.object(server, "_call", return_value={
            "success": True,
            "account_number": "",
            "account_setup_state": "not_started",
        }):
            result = server.fund_wallet()

        self.assertTrue(result["setup_required"])
        self.assertEqual(result["funding_method"], "bank_transfer")
        self.assertIn("Finish account setup", result["message"])


if __name__ == "__main__":
    unittest.main()
