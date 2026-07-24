# Wema / ALAT go-live runbook

The exact, ordered procedure to take Zitch from **sandbox/mock** to **live money**
on the Wema/ALAT rail. It is deliberately mechanical: the code is already
fail-closed, so going live is a config + verification exercise, not a code change.

> **One-line status check any time:** `python manage.py wema_preflight`
> (add `--strict` to also gate on the non-money rails). It exits non-zero until
> every hard gate passes. `/healthz` shows the same gate as booleans over HTTP.

---

## 0. What "go-live" actually gates on

Four things must be true before real money can move. Three are hard gates the
preflight enforces; the fourth is operational.

1. **Wema live keys present** — channel id + wallet key + per-product keys.
2. **`securityInfo` scheme configured** — the encrypted field on every
   money-movement call. **Without it, every live payout fails at the gateway and
   the debit auto-refunds** (an outage, not a leak). It is not in any spec; Wema
   provisions it out-of-band. See `utility.wema._security_info`.
3. **Pointed at the live host** — `WEMA_BASE_URL` is the live ALAT host, not
   `apiplayground.alat.ng`.
4. **A funded / provisionable path** — either a working BVN/NIN provisioning path
   on the live host, or a funded `WEMA_SOURCE_ACCOUNT`, so the
   account → lift-PND → fund → transfer loop can actually be exercised.

Get all four from Wema (see `docs/wema-migration.md` → "To go live"). Until then,
the preflight will correctly report **NOT READY** and no real money can move.

---

## 1. Environment variables

Set these in the **Render dashboard** (they are `sync: false` in `render.yaml`, so
they live only in the dashboard, never in git). **Crons are separate processes
from the web service** — the `WEMA_*` and `SENTRY_DSN` values must be set on the
crons too (`render.yaml` already declares the slots on each).

### Money rail (the hard gates)

| Var | What it is | Where it comes from |
|---|---|---|
| `WEMA_CHANNEL_ID` | Channel identifier (travels in the `access`/`x-api-key` header) | Wema |
| `WEMA_WALLET_KEY` | Subscription key for wallet/account/transfer (`Ocp-Apim-Subscription-Key`) | Wema |
| `WEMA_CARD_KEY` | Subscription key for card-management | Wema |
| `WEMA_AIRTIME_KEY` | Subscription key for VAS/airtime (if using Wema VAS vs VTU.ng) | Wema |
| `WEMA_BILLS_KEY` | Subscription key for bills | Wema |
| `WEMA_KYC_KEY` | Subscription key for Full KYC (account open/upgrade) | Wema |
| `WEMA_SOURCE_ACCOUNT` | Pool NUBAN that funds pool-sourced payouts | Wema |
| `WEMA_SECURITY_INFO` | The encrypted securityInfo value/scheme | Wema (out-of-band) |
| `WEMA_BASE_URL` | Live ALAT host (differs from `apiplayground.alat.ng`) | Wema |
| `WEMA_SIMULATION` | `true` exercises the flow without live keys; **unset/blank for live** | — |

### Supporting

| Var | Why |
|---|---|
| `SENTRY_DSN` | So the reconcile crons can page on drift/outage (they call `utility.alerts.alert`). Set it on the **web service and every cron**. |
| `WEMA_DIAG_TOKEN` | Enables the `/wema-diagnose?token=…` browser self-test. |
| `DIAG_TOKEN` | Enables `/vtu-diagnose?token=…`. |
| `VTUNG_API_KEY` **or** `VTUNG_USERNAME`+`VTUNG_PASSWORD` | Airtime/data/bills rail (VTU.ng). |
| `RESEND_API_KEY` | Transactional email (`RESEND_FROM_EMAIL` is already `no-reply@send.zitch.ng`). |
| `SENDCHAMP_API_KEY` | SMS / OTP-by-SMS. |
| `SENDCHAMP_SENDER_NAME` | Sender ID (default `Zitch`) — **must be an approved sender ID in Sendchamp** for the DND route to deliver. |

> ⚠️ **Test-only switches — pre-launch testing ONLY, all must be UNSET for go-live.**
> `wema_preflight` **hard-fails** while any of these is set, so readiness cannot pass
> until you remove them.
>
> | Var | What it enables (test only) |
> |---|---|
> | `TEST_OTP_PHONE` / `TEST_OTP_CODE` | That one number accepts a fixed OTP code, so you can sign in before the SMS sender ID is approved. |
> | `SIMULATE_DEPOSIT_TOKEN` | With `WEMA_SIMULATION=true`, `POST /api/dev/simulate-deposit/ {token, phone, amount}` credits mock money into a wallet — the missing "money-in" step so you can walk **fund → transfer → airtime** end to end. The endpoint **404s whenever `WEMA_SIMULATION` is off**, so it can never fabricate real money on a live deploy. |
>
> **End-to-end simulation walk:** set all four (plus `WEMA_SIMULATION=true`), sign up
> with `TEST_OTP_PHONE`, `curl` the simulate-deposit endpoint to load a balance, then
> transfer / buy airtime in the app. Delete all of them before launch.

No `*_PROVIDER` flip is needed — `wema` is already the default funding, payout and
KYC rail.

---

## 2. The ordered procedure

### Step 0 — infrastructure
- Upgrade the **web service** off `free` (no sleep) and the **Postgres** off `free`
  (the free tier expires). The crons already require `starter`.

### Step 1 — set the keys, still on sandbox
- Enter every `WEMA_*` key above **except** `WEMA_BASE_URL` (leave it on
  `apiplayground.alat.ng` for now), on the web service **and** the crons.
- Set `SENTRY_DSN` everywhere.

### Step 2 — confirm keys load (still sandbox)
- Run: `python manage.py wema_preflight`
  - Expect: `Wema live keys` → **PASS**, `securityInfo` → PASS if set,
    `Live host` → **FAIL** (still sandbox — correct at this step).
- Or hit `/healthz` and confirm `funding_wema: true`, `funding_wema_security_info: true`,
  `wema_sandbox: true`.

### Step 3 — live host
- Set `WEMA_BASE_URL` to the **live** ALAT host on the web service and all crons.
- Redeploy so every process picks up the new env.

### Step 4 — preflight must now say GO
- Run: `python manage.py wema_preflight`
  - Expect: **`RESULT: GO`** (all three hard gates PASS). If not, stop and fix.
- `/healthz` should now show `wema_sandbox: false` and
  `funding_wema_security_info: true`.

### Step 5 — live connectivity probes (no money moved)
- `GET /wema-diagnose?token=<WEMA_DIAG_TOKEN>&account=<10-digit>&bank=<code>` —
  proves auth + a real name-enquiry against the live gateway.
- `GET /vtu-diagnose?token=<DIAG_TOKEN>` — proves the VTU.ng wallet authenticates
  and shows its balance (VAS buys fail on an empty provider wallet).

### Step 6 — seed the catalogue
- `python manage.py seed_wema_plans` — maps the live data/cable catalogue onto
  `wema_code` (cable scoped per biller). Re-runnable.

### Step 7 — controlled smoke test (small real money)
1. Provision a NUBAN for a test user (BVN/NIN → OTP), confirm PND is lifted.
2. Fund it with a **small** real transfer; confirm `zitch-reconcile-wema` credits it
   (idempotent, every 10 min).
3. Send a **small** payout to a known account; confirm it settles via
   `confirm_transfer_status` on the next reconcile.
4. Buy the smallest airtime unit; confirm it delivers.

### Step 8 — confirm the safety nets are running
- `zitch-reconcile-wema` (*/10 min) — funding + payout settlement.
- `zitch-reconcile-balances` (every 6 h) — ledger vs the bank's NUBAN balance.
- `zitch-integrity-check` (daily) — ledger vs stored balance.
- All three now page via Sentry (Step 1 set `SENTRY_DSN`).

### Step 9 — monitor
- Watch `/healthz`, Sentry, and the `recon.*` AuditLog rows for the first days.

---

## 3. `/healthz` booleans by stage

| Boolean | Sandbox (now) | After Step 4 (GO) |
|---|---|---|
| `funding_wema` | `false` | `true` |
| `funding_wema_security_info` | `false` | `true` |
| `wema_sandbox` | `true` | **`false`** |
| `funding_wema_simulation` | maybe `true` | `false` |
| `payout_live` / `kyc_wema` | `false` | `true` |

Real money can only move when `funding_wema_security_info: true` **and**
`wema_sandbox: false`. Either being wrong means live calls are rejected and
auto-refunded — a safe outage.

---

## 4. Rollback

Going live is reversible in one step:
- Set `WEMA_BASE_URL` back to `https://apiplayground.alat.ng` (or set
  `WEMA_SIMULATION=true`, or blank the keys) and redeploy.
- The rail immediately fails closed: live money calls stop; nothing is stranded
  (a debit with no gateway confirmation auto-refunds on the next reconcile).

---

## 5. What the crons page on

| Cron | Pages (Sentry) when | Your move |
|---|---|---|
| `zitch-reconcile-wema` | the run crashes, or **every** gateway call in a run fails (systemic auth/outage) | Check `/wema-diagnose`; a transient blip clears next run. |
| `zitch-reconcile-balances` | **ledger > bank** for any wallet (possible float leak / double-credit) | Investigate that user's ledger vs NUBAN immediately. `bank > ledger` is benign (unswept deposit) — not paged. |
| `zitch-integrity-check` | stored balance ≠ ledger for any wallet | A money bug or tampering — investigate before it compounds. |

See `docs/wema-migration.md` for the rail internals and the remaining
verify-before-live items.
