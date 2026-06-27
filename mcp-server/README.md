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

**Money (disabled with `--read-only`; require the transaction PIN):**
- `send_to_bank(account_number, bank, amount, transaction_pin, note?)`
- `send_to_zitch_user(identifier, amount, transaction_pin, note?)`
- `fund_wallet(amount)` — returns a checkout `authorization_url`
- `fund_from_linked_bank(linked_id, amount)` — Mono DirectPay; returns a pay URL

The PIN is passed per call and forwarded to the server for verification — it is
never stored. Always confirm a resolved recipient name with the user before a
transfer.

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
- Money tools still require the per-transaction PIN; a wrong/missing PIN is
  rejected by the server (with lockout after repeated failures).
- Tier limits, daily caps, idempotency and the large-transfer face-verification
  gate are all enforced server-side.
