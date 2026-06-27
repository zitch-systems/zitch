# Zitch API (Django) — build & run

Backend for the Zitch mobile app. Implements the exact endpoints the Expo app
calls. Runs in **MOCK mode** until you add aggregator/payment/SMS keys, so the
whole flow is testable with zero external accounts.

## Stack
- Django 5.1 (plain JSON views, no DRF)
- PostgreSQL in prod (SQLite locally)
- Token auth: opaque token via `Authorization: Bearer <token>` (or `access_token`
  in the body for older app builds — `require_user` accepts both)
- Render for hosting

## Run locally
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # tweak if needed; SQLite works out of the box
python manage.py migrate
python manage.py seed_plans     # populate data/cable plans
python manage.py createsuperuser
python manage.py runserver      # http://127.0.0.1:8000  (admin at /admin/)
```

## Point the app at it
In the mobile app, `components/configFiles/apiConfig.tsx`:
```ts
const baseUrl = "http://10.0.2.2:8000";   // Android emulator -> host machine
// device on same wifi: "http://<your-LAN-ip>:8000"
// production:          "https://api.zitch.ng"
```

## Smoke test (mock mode)
```bash
# register -> sends OTP (printed to server log in mock mode via send_sms)
curl -X POST localhost:8000/api/phone_verification/ -H 'Content-Type: application/json' -d '{"phone":"08012345678","email":"a@b.com"}'
# check the OTP in Django admin (OTP table) or server output, then:
curl -X POST localhost:8000/api/verify_otp/ -H 'Content-Type: application/json' -d '{"phone":"08012345678","otp":"<code>"}'
```

## Deploy to Render
1. Push this repo to GitHub (already done).
2. Render dashboard -> **New + -> Blueprint** -> select this repo.
   `render.yaml` creates the web service (rootDir `backend`) + Postgres.
3. After first deploy, set the service env vars (Kora / VTU.ng / Sendchamp / Prembly keys).
4. Create an admin: Render shell -> `python manage.py createsuperuser`.
5. Set the app's `baseUrl` to the Render URL.

> Free tier sleeps after ~15 min and free Postgres expires — fine for testing,
> upgrade both to paid **before** handling real money (webhooks need always-on).

## Endpoints (all POST, JSON)
Auth: `/api/sigin/` · `/api/phone_verification/` · `/api/verify_otp/` ·
`/api/resend_verify_otp/` · `/api/set-password/` · `/api/set-transaction-pin/` ·
`/api/update_info/`
KYC: `/api/kyc/status/` · `/api/kyc/bvn/` · `/api/kyc/nin/` · `/api/kyc/face/`
Wallet: `/api/wallet_balance/` · `/api/user-transaction-history/`
Funding (Kora): `/api/fund/initialize/` · `/api/fund/verify/` · `/api/fund/webhook/`
Transfer (Zitch→Zitch): `/api/transfer/resolve/` · `/api/transfer/send/`
Utility: `/api/utility/{buyairtime,get_data_plans,get_data_plans_price,buydata,
get_cable_plans,get_cable_plans_price,validate_iuc,buycable,validate_meter,
buyelectricity}/`
Exams: `/api/exams/list/` · `/api/exams/buy/`
Loans: `/api/loans/status/` · `/api/loans/quote/` · `/api/loans/request/` · `/api/loans/repay/`
Fixed Save: `/api/savings/rates/` · `/api/savings/quote/` · `/api/savings/create/` · `/api/savings/list/`
Betting: `/api/betting/list/` · `/api/betting/fund/`
Zitch transfer: `/api/transfer/resolve/` · `/api/transfer/send/`
Bank transfer: `/api/transfers/banks/` · `/api/transfers/beneficiaries/` · `/api/transfers/resolve/` · `/api/transfers/send/` · `/api/transfers/webhook/`
Cards: `/api/cards/list/` · `/api/cards/create/` · `/api/cards/freeze/` · `/api/cards/details/` · `/api/cards/fund/`

## Fixed Save maturities
Matured plans are paid out (principal + interest credited to the wallet) two ways:
- **On access:** opening `/api/savings/list/` settles the caller's matured plans
  (`settle_user_maturities`), so payouts happen with no cron — even on Render's
  free tier.
- **Sweep:** `python manage.py run_maturities` pays out *every* due plan (for
  users who never open the app). Enable the `zitch-maturities` cron in
  `render.yaml` once the service is on a paid Render plan (cron has no free tier).

Payout is idempotent per plan, so overlapping runs never double-pay.

## Wallet funding flow (Kora)
1. App calls `/api/fund/initialize/` `{access_token, amount}` -> `{reference,
   authorization_url}`. A `FundingIntent` row is created (pending).
2. App opens `authorization_url` (Kora hosted checkout) in a browser.
3. Wallet is credited **once**, by whichever arrives first:
   - `/api/fund/verify/` `{access_token, reference}` (app calls on return), and/or
   - `/api/fund/webhook/` (Kora `charge.success`, `x-korapay-signature` HMAC-SHA256
     over the payload `data` object, verified).
   `settle_funding()` locks the intent row and guards on `credited`, so
   duplicate verify/webhook calls never double-credit. A bank transfer into a
   dedicated virtual account (no `FundingIntent`) is credited by account mapping
   (`credit_kora_virtual_account_funding`), keyed on Kora's reference.
4. In MOCK mode (no `KORA_SECRET_KEY`) verify/webhook succeed automatically so the
   flow is testable offline; in production a missing key fails closed.

Set the Kora dashboard webhooks to:
- pay-in:  `https://<your-render-host>/api/fund/webhook/`
- payout:  `https://<your-render-host>/api/transfers/webhook/`

### Provider layout

Kora is the sole money-movement rail (funding, virtual accounts, payouts) and the
KYC backend (BVN/NIN/vNIN). The views/services call provider-agnostic wrappers
(`utility.providers.funding_*` / `payout_*` / `verify_*`) that delegate to the
Kora client (`utility/kora.py`). Prembly handles the selfie/liveness step only
(Kora has no liveness check). Cards default to the generic `CARD_ISSUER` but can
run on Kora via `CARD_PROVIDER=kora`; Kora has no PAN-reveal endpoint, so
`/api/cards/details/` returns "not available on this card provider" there. Kora
endpoint shapes are marked VERIFY-BEFORE-LIVE in `utility/kora.py`.

## WhatsApp channel (deterministic; AI layer comes later)
A WhatsApp banking channel where a **linked** user checks balance and sends money
from chat. Built deterministic-first so money never depends on the AI being up.

- **Webhook:** `GET/POST /webhooks/whatsapp` — GET verifies against
  `WHATSAPP_VERIFY_TOKEN`; POST takes inbound messages (HMAC-verified via
  `WHATSAPP_APP_SECRET`, deduped on Meta's message id, acked 200, processed
  inline). Blank `WHATSAPP_TOKEN` ⇒ MOCK mode (outbound logged, inbound unsigned
  accepted) so the flow is testable with no Meta app.
- **Linking:** app calls `POST /api/whatsapp/link/start/` → a one-time code; the
  user sends `LINK <code>` from WhatsApp and the number binds to their account
  (`link/status/`, `link/unlink/` manage it). Unknown numbers get only the link flow.
- **Router (`whatsapp/router.py`):** keyword + numbered-menu + slot-filling that
  drives the SAME money services the app uses:
  - **Balance** and **NGN bank transfer** (name-enquiry → confirm → PIN →
    idempotent payout via `transfers.services.execute_payout`, shared with
    `/api/transfers/send/`).
  - **Airtime / data** (network → plan → phone → confirm → PIN) and **bills —
    electricity / cable** (meter/smartcard **validation** → confirm with the
    validated customer name → PIN), all via the shared `run_provider_purchase`
    (VTU.ng). Prepaid electricity returns the token in the receipt.
  - PINs are masked in the message log; every flow cancels after one wrong PIN.
- **AI intent layer (`whatsapp/ai.py`):** when `LLM_API_KEY` is set and the AI
  is enabled (global `SystemSetting.ai_enabled_global` AND per-user
  `WhatsAppLink.ai_enabled`), free-form text is mapped by an LLM (Claude, tool
  calling, temperature 0) to ONE structured intent, which is dispatched to the
  same deterministic flows above. The model only proposes — validation, confirm,
  and PIN still gate every movement. Explicit keywords/menu/paste always run
  deterministically first, and with the AI off (no key, or either toggle) the
  channel is fully menu-driven. The parsed intent is stored on the inbound log.
- **Multi-currency + FX (`wallet/forex.py`, Fincra):** NGN lives in `Wallet`;
  USD/GBP/CAD in `CurrencyWallet`. `balance` shows every funded currency;
  `convert` quotes a rate (margin from `SystemSetting.fx_margin_bps`), shows a
  time-boxed confirm, and on PIN-within-TTL settles atomically (debit source,
  credit target, ledger pair tagged with `currency`). The quote is single-use
  and expiry-checked, so a stale rate is never settled. Corridor-aware: CNY is
  quote/display-only (blocked from settlement, §13). Blank `FINCRA_SECRET_KEY`
  => MOCK rates so it's testable offline.
- **Operator backend (Django admin + staff endpoints):**
  - **Conversation monitor + handover (§10):** `ConversationState` per number;
    `POST /api/whatsapp/ops/handover/` pauses the bot (and the conversation's AI
    scope), `ops/return-to-bot/` re-enables it, `ops/reply/` sends an agent
    message. While a conversation is `human`, the bot stays silent. The message
    log (with the parsed AI intent) is the inbox, browsable in Django admin.
  - **Broadcasts (§9):** `Broadcast` + `BroadcastRecipient`; `ops/broadcast/`
    sends a template to a segment — **marketing only reaches opted-in users**,
    utility reaches all linked. `STOP`/`UNSUBSCRIBE` inbound flips
    `marketing_opt_in` off. Delivery callbacks update per-recipient status +
    roll-up counts. A provider block (e.g. Meta 131049) is recorded, not retried.
  - **Audit (§hard-rule #10):** `AuditLog` records handovers, agent replies, and
    broadcasts (actor + before/after).
  - **RBAC (§11):** every `/api/ops/*` endpoint is staff-gated and role-checked
    server-side (`portal/roles.py`): superuser ⇒ `super_admin`; otherwise the
    `super_admin` / `finance` / `support` Django **group** sets the role, and a
    staff user in none of them is `read_only`. Caps are returned at login and
    mirrored in the portal UI, but the server check is the gate.

## Web surfaces (landing + operator portal)
- **Landing page** at `/` and the **interactive prototype** at `/prototype/` —
  the design-handoff references served as templates + static (responsive,
  theme-aware). The health probe moved to `/healthz` (render.yaml points there).
- **Operator portal** at `/portal/`: the admin design wired to live data via 25
  staff endpoints under `/api/ops/` (login, overview KPIs, users + freeze/PIN
  unlock, KYC queue + approve/reject, transactions + requery, FX margin +
  corridor pauses, products incl. card freeze + maturity sweep, WhatsApp inbox
  riding the ops handover/reply endpoints, broadcasts, AI kill switch, webhook/
  recon log, audit, settings + team). Every mutation is audit-logged with
  before/after. Sign in with a **staff** account (`createsuperuser`, or staff +
  one of the role groups).
- Corridor pauses are enforced in `wallet/forex.py` (`fx_corridor_<ccy>_enabled`,
  default on; CNY stays settlement-blocked in code regardless).

Set the webhook URL + `WHATSAPP_VERIFY_TOKEN` in the Meta app dashboard and fill
the `WHATSAPP_*` env vars (see `.env.example`).

## Before go-live (TODO)
- **HTTPS hardening is automatic.** With `DJANGO_DEBUG=false` (set in
  `render.yaml`), Django enforces the HTTPS redirect, secure session/CSRF
  cookies, and 1-year HSTS behind Render's TLS proxy, so `manage.py check
  --deploy` is clean. HSTS **preload** stays opt-in (`DJANGO_HSTS_PRELOAD=true`)
  because it's hard to reverse, and a deploy still running on the dev
  `SECRET_KEY` now fails fast instead of booting insecure.
- **Upgrade off the free tier.** Flip the web service and Postgres in
  `render.yaml` from `plan: free` to a paid plan before real money: free web
  sleeps (webhooks need always-on) and free Postgres expires. The two crons
  (`zitch-maturities`, `zitch-reconcile-vtu`) already require a paid plan.
- VTU.ng (v2) is the VTU provider, in `utility/vtung.py` (called via the
  `utility/providers.py` `vtu_*` wrappers). Confirm the tv/electricity/betting
  request field names, the customer-verify endpoint, the 9mobile `service_id`,
  and that the seeded data/cable `variation_id` codes match VTU.ng's catalogue —
  these couldn't be fetched from CI.
- Kora request/response shapes are VERIFY-BEFORE-LIVE: set `KORA_SECRET_KEY`
  (sk_test_ first), run `python manage.py kora_check`, and confirm the funding /
  virtual-account / payout / identity field names against your Kora dashboard
  before flipping off mock. Configure the dashboard webhooks (URLs above).
- Set `SENDCHAMP_API_KEY`, `PREMBLY_API_KEY` / `PREMBLY_APP_ID` (liveness only),
  and (when a card issuer is chosen) `CARD_ISSUER_*` / `CARD_PROVIDER` — confirm
  the request/response mapping in `utility/providers.py` / `utility/kora.py`.
- Auth accepts `Authorization: Bearer <token>` (preferred) or body `access_token`.
  The app's `lib/api.ts` `apiPost`/`apiJson` helpers send the Bearer header and
  the core money screens use them; remaining screens can adopt incrementally —
  the body token still works, so nothing breaks mid-migration.
- Replace seeded plans with the live aggregator catalogue.
- Bank transfers use Kora payouts (drawn from your Kora payout balance, which you
  pre-fund). Point the Kora **transfer** webhook at
  `https://<your-render-host>/api/transfers/webhook/` — `transfer.success` settles
  a PENDING payout and `transfer.failed`/`reversed` refunds the wallet (payouts
  that come back `processing` stay PENDING until the webhook confirms).
