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

Money tools require the user's transaction PIN as an argument; it is forwarded to
the endpoint that verifies it and is never stored. Run with --read-only (or the
env flag) to disable money tools entirely.

Transport: stdio (works with Claude Desktop/Code and any MCP client). See README.
"""
import argparse
import os
import uuid

import httpx
from mcp.server.fastmcp import FastMCP

API_URL = os.environ.get("ZITCH_API_URL", "https://api.zitch.ng").rstrip("/")
TOKEN = os.environ.get("ZITCH_ACCESS_TOKEN", "")
READ_ONLY = os.environ.get("ZITCH_MCP_READONLY", "").strip().lower() in ("1", "true", "yes", "on")
TIMEOUT = 30.0

mcp = FastMCP("zitch")


def _call(path: str, body: dict | None = None) -> dict:
    """POST to a Zitch endpoint with the configured token; return parsed JSON."""
    payload = dict(body or {})
    headers = {"Content-Type": "application/json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
        payload.setdefault("access_token", TOKEN)  # body fallback the API also accepts
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.post(f"{API_URL}{path}", json=payload, headers=headers)
    except httpx.HTTPError as exc:
        return {"success": False, "message": f"Zitch API unreachable: {exc}"}
    if resp.status_code == 401:
        return {"success": False, "message": "Unauthorized — ZITCH_ACCESS_TOKEN is missing or expired."}
    try:
        return resp.json()
    except ValueError:
        return {"success": False, "message": f"Unexpected response (HTTP {resp.status_code})."}


def _idem() -> str:
    return f"mcp-{uuid.uuid4().hex}"


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


# --------------------------- money tools (PIN-gated) ---------------------------
def send_to_bank(account_number: str, bank: str, amount: str,
                 transaction_pin: str, note: str = "") -> dict:
    """Send money to an external bank account. Requires the user's transaction PIN.

    `bank` is the bank slug code (from list_banks). `amount` is in naira. Always
    confirm the resolved recipient name (resolve_bank_account) with the user
    before sending. The server verifies the PIN, tier/daily limits and idempotency.
    """
    return _call("/api/transfers/send/", {
        "account_number": account_number, "bank": bank, "amount": amount,
        "transaction_pin": transaction_pin, "note": note, "idempotency_key": _idem(),
    })


def send_to_zitch_user(identifier: str, amount: str,
                       transaction_pin: str, note: str = "") -> dict:
    """Send money to another Zitch user (by phone/username/email). Requires the PIN.

    `amount` is in naira. The server verifies the PIN and limits.
    """
    return _call("/api/transfer/send/", {
        "identifier": identifier, "amount": amount,
        "transaction_pin": transaction_pin, "note": note, "idempotency_key": _idem(),
    })


def fund_wallet(amount: str) -> dict:
    """Start a wallet top-up; returns an authorization_url for the user to pay.

    `amount` is in naira. No PIN needed (the user completes payment on the rail).
    """
    return _call("/api/fund/initialize/", {"amount": amount})


def fund_from_linked_bank(linked_id: int, amount: str) -> dict:
    """Fund the wallet from a linked bank (Mono DirectPay); returns an authorization_url.

    `linked_id` comes from list_linked_banks. `amount` is in naira.
    """
    return _call("/api/banklink/fund/", {"linked_id": linked_id, "amount": amount})


def _register_money_tools() -> None:
    for fn in (send_to_bank, send_to_zitch_user, fund_wallet, fund_from_linked_bank):
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
