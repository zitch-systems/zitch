# Mono API — Postman collection

Importable Postman docs for the Mono open-banking endpoints Zitch uses
(`backend/utility/mono.py` + the `banklink` app).

## Import
1. Postman → **Import** → drop both files:
   - `mono.postman_collection.json` (the requests)
   - `mono.postman_environment.json` (the variables)
2. Select the **Mono — Test** environment (top-right), then edit it and set
   `sec_key` (and `pub_key`) to your Mono **test** keys. Both are SECRET-typed —
   no key is ever stored in a request. `baseurl` defaults to
   `https://api.withmono.com`.

## Auth
The collection sends `mono-sec-key: {{sec_key}}` on every request (collection-level
API-key auth). Switch the environment to your live keys only when you're ready.

## What's covered
- **Accounts:** exchange auth code → account id, get account / balance /
  transactions / identity, unlink.
- **DirectPay (fund wallet):** initiate payment (amount in **kobo**), verify.
- **Misc:** list institutions.

## How it maps to Zitch
| Postman request | Zitch endpoint / function |
|---|---|
| Exchange auth code | `POST /api/banklink/connect/` → `mono.exchange_token` |
| Get balance / account | `POST /api/banklink/refresh/` → `mono.get_balance` |
| Initiate payment (DirectPay) | `POST /api/banklink/fund/` → `mono.initiate_directpay` |
| (webhook) | `POST /api/banklink/webhook/` → `mono.verify_webhook` + `settle_funding` |

> Paths/fields follow Mono's published docs (https://docs.mono.co) and are
> **VERIFY-BEFORE-LIVE** — confirm against your dashboard before relying on them.
> Amounts are in **kobo**; balances come back in kobo and Zitch converts to naira.
