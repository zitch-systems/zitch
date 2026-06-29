# Mono bank-linking ("Connected accounts") — going live

The Connected-accounts feature (Wallet carousel + Home summary + Add-a-bank) is
in the app. It works **end-to-end in a simulated dev preview** out of the box;
this doc covers what to set so it links **real** banks and moves real money.

## 1. Frontend — Mono public key + a native build

Real bank linking runs in the native `@mono.co/connect-react-native` widget,
which is **not** available in Expo Go. You need a custom dev build or an EAS
build, plus the Mono **public** key.

```bash
# .env (local) and your EAS environment variables (builds)
EXPO_PUBLIC_MONO_PUBLIC_KEY=pk_live_xxx   # from https://app.mono.co (public key — safe to expose)
```

- Set `EXPO_PUBLIC_MONO_PUBLIC_KEY` as an **EAS environment variable**
  (`eas env:create` or the dashboard) so it's inlined at build time. Don't commit
  the value.
- Build a binary that includes the native module — any non-Expo-Go build works:
  ```bash
  eas build --profile preview      # internal APK/IPA
  # or, for a dev client:
  eas build --profile preview3     # developmentClient: true
  ```
  The SDK **autolinks** — no `app.json` config plugin is required.
- Until both the key and a native build are present, `lib/mono.tsx` falls back to
  a clearly-labeled **simulated** "Connecting…" sheet that returns a fake
  `MONO-SIM-…` code the backend rejects, so the UI is testable everywhere.

> `useMono().native` is `true` only when the SDK loaded **and** the public key is
> set; the Add-a-bank screen shows a "Dev preview" hint otherwise.

## 2. Backend — Mono secret + webhook

`backend/utility/mono.py` runs in **mock mode** until the secret key is set
(see `backend/zitch_api/settings.py` → `MONO`):

```bash
MONO_SECRET_KEY=sk_live_xxx        # mono-sec-key header; blank => mock mode
MONO_PUBLIC_KEY=pk_live_xxx        # informational on the backend
MONO_WEBHOOK_SECRET=whsec_xxx      # compared against the mono-webhook-secret header
```

In the Mono dashboard, point the webhook to:

```
https://<your-api-host>/api/banklink/webhook/
```

This settles **Fund Zitch** (DirectPay debit-in) credits idempotently and marks
accounts active/reauth on connect events.

## 3. Endpoints (all live)

| Endpoint | Purpose |
|---|---|
| `POST /api/banklink/connect/` `{code}` | Exchange the Mono auth code, link the account |
| `POST /api/banklink/list/` | List linked accounts (+ cached balances) |
| `POST /api/banklink/refresh/` `{linked_id}` | Re-pull one account's balance |
| `POST /api/banklink/unlink/` `{linked_id}` | Unlink |
| `POST /api/banklink/fund/` `{linked_id, amount, idempotency_key}` | **Fund Zitch** — Mono DirectPay debit-in (wallet credited via webhook) |
| `POST /api/banklink/payout/` `{linked_id, amount, pin, idempotency_key}` | **Fund bank** — wallet → linked bank, PIN-verified, via the transfers payout rail |
| `POST /api/banklink/webhook/` | Mono callback (signature-verified) |

`payout/` reuses the same `execute_payout` rail as normal bank transfers, so the
balance / send-limit / daily-limit / idempotency guards and the Kora settlement
webhook all apply. It routes by detecting the bank for the linked account number
(the linked record stores the bank name, not a routable code).
