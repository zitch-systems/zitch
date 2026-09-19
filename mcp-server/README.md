# Zitch MCP server

Let an AI assistant (Claude Desktop/Code, or any MCP client) operate a Zitch
wallet — check balance and history, resolve accounts, and (optionally) move money.

It is a **thin client over the Zitch REST API**: it does not touch the database
or re-implement money logic. Every tool calls a public endpoint with the user's
access token, so all server-side controls apply unchanged — token auth + expiry,
**transaction-PIN verification & lockout**, tier/daily limits, idempotency, and
name/fraud checks. There is no second path to money.

## Tools

**Read (always available):**
- `get_balance` — wallet balance + dedicated account
- `list_transactions` — recent activity
- `list_banks` — supported banks (code + name)
- `resolve_bank_account(account_number, bank?)` — name enquiry
- `list_linked_banks` — external banks linked via Mono
- `fund_wallet()` — return the wallet's dedicated funding account and bank-transfer instructions

**Money (disabled with `--read-only`; transfers require the transaction PIN):**
- `send_to_bank(account_number, bank, amount, transaction_pin, request_id, note?)`
- `send_to_zitch_user(identifier, amount, transaction_pin, request_id, note?)`
- `fund_from_linked_bank(linked_id, amount, request_id)` — Mono DirectPay; returns a pay URL

For transfers, the PIN is passed per call and forwarded to the server for
verification — it is never stored. Always confirm a resolved recipient name with
the user before a transfer.

`fund_wallet()` does **not** initiate a charge or hosted checkout. The current
wallet rail is funded by ordinary bank transfer to the dedicated account returned
by the tool; Zitch credits the wallet after the partner bank confirms the inbound
transfer. If `setup_required` is true, the user must finish funding-account setup
in the Zitch app before transferring money. Linked-bank DirectPay remains a
separate money-moving tool.

Every money tool requires a caller-generated `request_id` (a UUID is
recommended). Generate it **once for one intended payment** and retain it before
the first call. If the MCP connection drops, the response is uncertain, or the
call is otherwise retried, send the exact same request ID and the exact same
payment fields. Use a new request ID only when the user intends a new payment.
The MCP server deterministically converts it into the API idempotency key; it no
longer creates a fresh key during each invocation. Reusing a request ID with a
different recipient or amount is rejected by the API rather than treated as a
new payment.

### Money response outcomes

Money tools return an explicit `outcome`:

- `success` — the API explicitly confirmed success.
- `pending` — the request exists but its financial result is not final.
- `failed` — a definitive rejection occurred before or after safe reversal.
- `unknown` — delivery or the response could not be trusted, such as a timeout,
  unreadable response, HTTP 408/425/429, or 5xx response.

For `pending` or `unknown`, do not claim success or failure and do not create a
new `request_id`. Check transaction history first. Any retry of the same intended
payment must reuse the exact original `request_id` and unchanged payment fields;
the response includes `retry_with_same_request_id` and `retry_instruction` to
make that requirement visible to the MCP host. Responses also preserve
`http_status`/`http_ok` when an HTTP response was received. A structured
pre-execution `pin_locked`, `velocity`, or `rate_limited` 429 remains a definitive
`failed` outcome because the money handler did not run.

## Setup

```bash
cd mcp-server
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env   # set ZITCH_API_URL + ZITCH_ACCESS_TOKEN
```

Run it (stdio):
```bash
zitch-mcp            # or:  python server.py
zitch-mcp --read-only   # read tools only, no money movement
```

## Client config (Claude Desktop / Code)

Add to your MCP client's `mcpServers`:
```json
{
  "mcpServers": {
    "zitch": {
      "command": "zitch-mcp",
      "env": {
        "ZITCH_API_URL": "https://api.zitch.ng",
        "ZITCH_ACCESS_TOKEN": "<your access token>"
      }
    }
  }
}
```
Add `"ZITCH_MCP_READONLY": "1"` to that `env` block for a read-only connection.

## Security

- The access token grants wallet access — store it like a credential; prefer a
  short-lived token and `--read-only` unless money movement is required.
- Transfer tools still require the per-transaction PIN; a wrong/missing PIN is
  rejected by the server (with lockout after repeated failures).
- A money-moving tool's `request_id` must be 8-128 characters and contain only letters,
  numbers, `.`, `_`, `:` or `-`. Persist it until the final outcome is known.
- Tier limits, daily caps, idempotency and the large-transfer face-verification
  gate are all enforced server-side.
