"""Zitch MCP server — let an AI assistant operate a Zitch wallet.

This is a thin, typed client over the existing Zitch REST API. It does NOT touch
the database or re-implement any money logic: every tool calls a public endpoint
with the user's access token, so all server-side controls (auth + expiry, PIN
verification & lockout, tier/daily limits, idempotency, fraud/name checks) apply
unchanged — there is no second path to money.

Auth & config (environment):
  ZITCH_API_URL        Base URL, e.g. https://api.zitch.ng (default) or a dev host.
  ZITCH_ACCESS_TOKEN   A normal user access token (the same one the app uses).
  ZITCH_MCP_READONLY   "1"/"true" to expose ONLY read tools (no money movement).

Transfer tools require the user's transaction PIN as an argument; it is forwarded
to the endpoint that verifies it and is never stored. Dedicated-account funding is
a read-only instruction tool; linked-bank DirectPay is a money tool. Run with
--read-only (or the env flag) to disable all money-moving tools.

Transport: stdio (works with Claude Desktop/Code and any MCP client). See README.
"""
import argparse
import hashlib
import os
import re

import httpx
from mcp.server.fastmcp import FastMCP

API_URL = os.environ.get("ZITCH_API_URL", "https://api.zitch.ng").rstrip("/")
TOKEN = os.environ.get("ZITCH_ACCESS_TOKEN", "")
READ_ONLY = os.environ.get("ZITCH_MCP_READONLY", "").strip().lower() in ("1", "true", "yes", "on")
TIMEOUT = 30.0

mcp = FastMCP("zitch")


_TRANSIENT_HTTP_STATUSES = frozenset({408, 425, 429})
_PRE_EXECUTION_FAILURE_CODES = frozenset({"pin_locked", "velocity", "rate_limited"})
_SAME_REQUEST_ID_INSTRUCTION = (
    "The payment outcome is not confirmed. Do not create a new request_id. "
    "Check transaction history first; if you retry this intended payment, reuse "
    "the exact same request_id and unchanged payment fields."
)


def _http_metadata(status: int) -> dict:
    return {"http_status": status, "http_ok": 200 <= status < 300}


def _unknown_money_response(message: str, *, status: int | None = None,
                            data: dict | None = None, transport_error: bool = False) -> dict:
    """A delivery/result ambiguity that must retain the caller's request identity."""
    payload = dict(data or {})
    payload.update({
        # Never let a contradictory/stale success flag on a gateway response become
        # a terminal receipt in an MCP host.
        "success": False,
        "unknown": True,
        "outcome": "unknown",
        "retry_with_same_request_id": True,
        "retry_instruction": _SAME_REQUEST_ID_INSTRUCTION,
        "message": message,
    })
    if status is not None:
        payload.update(_http_metadata(status))
    if transport_error:
        payload["transport_error"] = True
    return payload


def _money_response(data: dict, status: int) -> dict:
    """Attach one conservative outcome to a parsed money-endpoint response."""
    payload = {**data, **_http_metadata(status)}
    if data.get("pending") is True:
        payload.pop("unknown", None)
        payload["success"] = False
        payload["outcome"] = "pending"
        payload["retry_with_same_request_id"] = True
        payload["retry_instruction"] = _SAME_REQUEST_ID_INSTRUCTION
        return payload

    code = str(data.get("code") or "")
    if status == 429 and code in _PRE_EXECUTION_FAILURE_CODES:
        payload.pop("unknown", None)
        payload.pop("retry_with_same_request_id", None)
        payload.pop("retry_instruction", None)
        payload["success"] = False
        payload["outcome"] = "failed"
        return payload

    ambiguous_status = status in _TRANSIENT_HTTP_STATUSES or status >= 500
    if ambiguous_status:
        return _unknown_money_response(
            str(data.get("message") or f"Zitch returned an uncertain HTTP {status} response."),
            status=status,
            data=data,
        )

    if 400 <= status < 500:
        payload.pop("unknown", None)
        payload.pop("retry_with_same_request_id", None)
        payload.pop("retry_instruction", None)
        payload["success"] = False
        payload["outcome"] = "failed"
        return payload
    if not 200 <= status < 300:
        return _unknown_money_response(
            str(data.get("message") or f"Zitch returned an uncertain HTTP {status} response."),
            status=status,
            data=data,
        )
    if data.get("success") is True:
        payload.pop("unknown", None)
        payload.pop("retry_with_same_request_id", None)
        payload.pop("retry_instruction", None)
        payload["outcome"] = "success"
        return payload

    # A 2xx transport only proves that an HTTP response arrived. Without an
    # explicit success/pending outcome it is not proof the payment settled.
    return _unknown_money_response(
        str(data.get("message") or "Zitch returned no confirmed payment outcome."),
        status=status,
        data=data,
    )


def _call(path: str, body: dict | None = None, *, money: bool = False) -> dict:
    """POST to Zitch and preserve the HTTP/delivery outcome for MCP callers."""
    payload = dict(body or {})
    headers = {"Content-Type": "application/json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.post(f"{API_URL}{path}", json=payload, headers=headers)
    except httpx.HTTPError as exc:
        if money:
            return _unknown_money_response(
                "Zitch API could not be reached and the payment outcome is unknown.",
                transport_error=True,
                data={"error_type": type(exc).__name__},
            )
        return {
            "success": False,
            "transport_error": True,
            "error_type": type(exc).__name__,
            "message": "Zitch API is temporarily unreachable.",
        }

    status = resp.status_code
    if status == 401:
        return {
            "success": False,
            "outcome": "failed" if money else "unavailable",
            "code": "unauthorized",
            "message": "Unauthorized — ZITCH_ACCESS_TOKEN is missing or expired.",
            **_http_metadata(status),
        }
    try:
        data = resp.json()
    except ValueError:
        if money:
            return _unknown_money_response(
                f"Zitch returned an unreadable HTTP {status} response; the payment outcome is unknown.",
                status=status,
                transport_error=True,
            )
        return {
            "success": False,
            "transport_error": True,
            "code": "invalid_response",
            "message": f"Unexpected response (HTTP {status}).",
            **_http_metadata(status),
        }
    if not isinstance(data, dict):
        if money:
            return _unknown_money_response(
                f"Zitch returned an invalid HTTP {status} response; the payment outcome is unknown.",
                status=status,
                transport_error=True,
            )
        return {
            "success": False,
            "transport_error": True,
            "code": "invalid_response",
            "message": f"Unexpected response (HTTP {status}).",
            **_http_metadata(status),
        }
    if money:
        return _money_response(data, status)
    return {**data, **_http_metadata(status)}


_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}\Z")
_MONEY_OPERATIONS = frozenset({"bank", "p2p", "linked-fund"})


def _idempotency_key(operation: str, request_id: str) -> str:
    """Derive the API idempotency key for one logical MCP money request.

    The caller, rather than this server, owns ``request_id`` so an MCP transport
    retry or a retry after an uncertain response reuses the same key.  The
    operation is included in the digest so the same request ID can never collide
    across different tools.  Do not include amount/recipient in the digest: if a
    caller accidentally changes those fields while reusing a request ID, the API
    must see the same key and reject the changed request as a conflict.
    """
    if operation not in _MONEY_OPERATIONS:  # pragma: no cover - programmer error
        raise ValueError("Unsupported money operation")
    if not isinstance(request_id, str):
        raise ValueError("request_id must be a string")
    request_id = request_id.strip()
    if not _REQUEST_ID_RE.fullmatch(request_id):
        raise ValueError(
            "request_id must be 8-128 characters using letters, numbers, '.', '_', ':' or '-'. "
            "Generate it once for an intended payment and reuse it for every retry."
        )
    digest = hashlib.sha256(f"{operation}\0{request_id}".encode("utf-8")).hexdigest()
    return f"mcp-{operation}-{digest}"


# --------------------------- read tools (always on) ---------------------------
@mcp.tool()
def get_balance() -> dict:
    """Get the user's Zitch wallet balance and dedicated account details."""
    return _call("/api/wallet_balance/")


@mcp.tool()
def list_transactions() -> dict:
    """List the user's recent wallet transactions (most recent first)."""
    return _call("/api/user-transaction-history/")


@mcp.tool()
def list_banks() -> dict:
    """List supported Nigerian banks (code + name) for transfers."""
    return _call("/api/transfers/banks/")


@mcp.tool()
def resolve_bank_account(account_number: str, bank: str = "") -> dict:
    """Name-enquiry: resolve the account holder name for a 10-digit account number.

    Pass `bank` (the bank's slug code from list_banks) to resolve at one bank, or
    omit it to auto-detect the bank.
    """
    return _call("/api/transfers/resolve/", {"account_number": account_number, "bank": bank})


@mcp.tool()
def list_linked_banks() -> dict:
    """List external bank accounts the user has linked via Mono open banking."""
    return _call("/api/banklink/list/")


@mcp.tool()
def fund_wallet() -> dict:
    """Get the dedicated bank account used to fund this Zitch wallet.

    This does not initiate a card charge or hosted checkout. Transfer money from
    any bank to the returned account number; Zitch credits the wallet after the
    partner bank reports the inbound transfer. If no account number is present,
    finish funding-account setup in the Zitch app before sending money.
    """
    result = _call("/api/wallet/account/")
    if result.get("success") is not True:
        return result
    if result.get("account_number"):
        return {
            **result,
            "funding_method": "bank_transfer",
            "message": (
                "Transfer the intended amount from any bank to this dedicated account. "
                "The wallet is credited automatically after the bank confirms receipt."
            ),
        }
    return {
        **result,
        "funding_method": "bank_transfer",
        "setup_required": True,
        "message": (
            "No dedicated funding account is ready yet. Finish account setup in the "
            "Zitch app before attempting a bank transfer."
        ),
    }


# --------------------------- money tools (PIN-gated) ---------------------------
def send_to_bank(account_number: str, bank: str, amount: str,
                 transaction_pin: str, request_id: str, note: str = "") -> dict:
    """Send money to an external bank account. Requires the user's transaction PIN.

    `bank` is the bank slug code (from list_banks). `amount` is in naira. Always
    confirm the resolved recipient name (resolve_bank_account) with the user
    before sending. `request_id` identifies this one intended transfer: generate
    it once and reuse the exact value for every retry. Never reuse it for a new
    transfer. The server verifies the PIN, tier/daily limits and idempotency.
    """
    return _call("/api/transfers/send/", {
        "account_number": account_number, "bank": bank, "amount": amount,
        "transaction_pin": transaction_pin, "note": note,
        "idempotency_key": _idempotency_key("bank", request_id),
    }, money=True)


def send_to_zitch_user(identifier: str, amount: str,
                       transaction_pin: str, request_id: str, note: str = "") -> dict:
    """Send money to another Zitch user (by phone/username/email). Requires the PIN.

    `amount` is in naira. `request_id` identifies this one intended transfer:
    generate it once and reuse the exact value for every retry; use a new value
    for a new transfer. The server verifies the PIN, limits and idempotency.
    """
    return _call("/api/transfer/send/", {
        "identifier": identifier, "amount": amount,
        "transaction_pin": transaction_pin, "note": note,
        "idempotency_key": _idempotency_key("p2p", request_id),
    }, money=True)


def fund_from_linked_bank(linked_id: int, amount: str, request_id: str) -> dict:
    """Fund the wallet from a linked bank (Mono DirectPay); returns an authorization_url.

    `linked_id` comes from list_linked_banks. `amount` is in naira. Generate
    `request_id` once for this intended debit and reuse it on every retry; use a
    new value only for a genuinely new debit.
    """
    return _call("/api/banklink/fund/", {
        "linked_id": linked_id,
        "amount": amount,
        "idempotency_key": _idempotency_key("linked-fund", request_id),
    }, money=True)


def _register_money_tools() -> None:
    for fn in (send_to_bank, send_to_zitch_user, fund_from_linked_bank):
        mcp.tool()(fn)


def main() -> None:
    parser = argparse.ArgumentParser(description="Zitch MCP server")
    parser.add_argument("--read-only", action="store_true",
                        help="Expose only read tools; disable all money movement.")
    args = parser.parse_args()
    if not (READ_ONLY or args.read_only):
        _register_money_tools()
    mcp.run()


if __name__ == "__main__":
    main()

