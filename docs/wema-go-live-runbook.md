# Wema / ALAT go-live runbook

The exact, ordered procedure to take Zitch from **sandbox/mock** to **live money**
on the Wema/ALAT rail. It is deliberately mechanical: the code is already
fail-closed, so going live is a config + verification exercise, not a code change.

> **One-line status check any time:** `python manage.py wema_preflight`
> (add `--strict` to also gate on the non-money rails). It exits non-zero until
> every hard gate passes. `/healthz` shows the same gate as booleans over HTTP.
>
> **No shell?** Every step below has an HTTP or Django-admin equivalent, because the
> deploys that most need a go-live gate are the ones without a shell (Render's free
> tier has none). The preflight itself is `GET /preflight` with a diagnostic bearer
> token — the same command, same wording, `200` when ready and `503` when not
> (`?strict=1` for `--strict`).

---

## 0. What "go-live" actually gates on

Three things must be true before real money can move. Two are hard gates the
preflight enforces; the third is operational.

1. **Wema live keys present** — channel id + wallet key + per-product keys.
2. **Pointed at the live host** — `WEMA_BASE_URL` is the live ALAT host, not
   `apiplayground.alat.ng`.
3. **A funded / provisionable path** — either a working BVN/NIN provisioning path
   on the live host, or a funded `WEMA_SOURCE_ACCOUNT`, so the
   account → lift-PND → fund → transfer loop can actually be exercised.

Get these from Wema (see `docs/wema-migration.md` → "To go live"). Until then,
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
| `WEMA_CARD_KEY` | Subscription key for the **Virtual Naira Card** product. Its own subscription — no wallet fallback, so the card rail stays disabled until this is set. | Wema |
| `WEMA_AIRTIME_KEY` | **Optional — leave blank.** No separate Airtime & Data product exists; the Wallet Services key authenticates VAS. Set only if Wema issues a dedicated subscription. | Wema |
| `WEMA_BILLS_KEY` | **Optional — leave blank.** Same as above: bills ride the Wallet Services key. | Wema |
| `WEMA_KYC_KEY` | **Optional — leave blank.** Partnership Account KYC is bundled into Wallet Services. | Wema |
| `WEMA_SOURCE_ACCOUNT` | Pool NUBAN that funds pool-sourced payouts | Wema |
| `WEMA_SECURITY_INFO` | A value **we** choose; the bank echoes it back to our Authentication Callback (Wema confirmed 2026-07-27 — nothing is issued). Any long random string; enables `WEMA_AUTH_REQUIRE_SECURITY_INFO`. | us |
| `WEMA_BASE_URL` | Live ALAT host (differs from `apiplayground.alat.ng`) | Wema |
| `WEMA_SIMULATION` | **Deploy-wide simulation switch.** `true` puts the WHOLE stack (Wema, VTU airtime/data/bills, cards, FX, Mono, KYC) into mock mode so every feature can be walked end-to-end with no real money. **Must be unset/blank for live** — `wema_preflight` hard-fails while it is on. | — |

### Supporting

| Var | Why |
|---|---|
| `SENTRY_DSN` | So the reconcile crons can page on drift/outage (they call `utility.alerts.alert`). Set it on the **web service and every cron**. |
| `WEMA_DIAG_TOKEN` | Enables the `/wema-diagnose` remote self-test using an `Authorization: Bearer …` header. |
| `DIAG_TOKEN` | Enables `/preflight`, `/vtu-diagnose`, `/sms-diagnose`, and `/wema-callbacks-diagnose` using bearer auth (either diagnostic token opens the callback probe). Never put either token in a URL. |
| `COMPLIANCE_EXPORT_EMAIL` | Where an NDPR data-subject export is delivered (Django admin → Users). Unset means that action **refuses** — the export is never shown in a browser, so with no destination there is nowhere safe for it to go. Set it before you need it: the NDPR clock is 30 days and it is not a good day to discover the setting. |
| `VTUNG_API_KEY` **or** `VTUNG_USERNAME`+`VTUNG_PASSWORD` | Airtime/data/bills rail (VTU.ng). |
| `RESEND_API_KEY` | Transactional email (`RESEND_FROM_EMAIL` is already `no-reply@send.zitch.ng`). |
| `TERMII_API_KEY` | The SMS / OTP-by-SMS rail — the only one. **Blank = mock mode: nothing is sent**, so no user receives a code. |
| `TERMII_SENDER_ID` | Sender ID (default `Zitch`) — **must be approved AND whitelisted for DND**, or messages are accepted by the API and never reach the handset. |
| `TERMII_BASE_URL` | Termii's regional host for *your* account (see the Termii dashboard). |

> ⚠️ **Test-only switches — pre-launch testing ONLY, all must be UNSET for go-live.**
> `wema_preflight` **hard-fails** while any of these is set, so readiness cannot pass
> until you remove them.
>
> | Var | What it enables (test only) |
> |---|---|
> | `TEST_OTP_PHONE` / `TEST_OTP_CODE` | That one number accepts a fixed OTP code, so you can sign in before the SMS sender ID is approved. |
> | `ALLOW_PRODUCTION_TEST_OTP` | Required alongside the two above whenever `DEBUG` is off — without it the app **refuses to boot** (`production_checks.py`), because a fixed code on a live host is an account takeover if the pair leaks. Setting it does **not** make the deploy launch-ready: `wema_preflight` still hard-fails on `TEST_OTP`. |
> | `SIMULATE_DEPOSIT_TOKEN` | With `WEMA_SIMULATION=true`, gates two dev endpoints (both **404 whenever `WEMA_SIMULATION` is off**, so neither can touch a live deploy): `POST /api/dev/simulate-deposit/ {token, phone, amount}` credits mock money (the "money-in" step), and `POST /api/dev/simulate-kyc/ {token, phone, tier?}` marks a user KYC-verified to `tier` (1–3, default 3) and provisions a mock NUBAN — so tiers / virtual-account / limit-gated features work without real identity data. |
>
> **End-to-end simulation walk:** with `WEMA_SIMULATION=true` the whole stack is mocked,
> so **every** feature works — transfers, airtime/data/bills, virtual cards, FX, loans,
> savings, statements. Set the test vars, sign up with `TEST_OTP_PHONE`, then `curl`
> **simulate-kyc** (get verified + an account number) and **simulate-deposit** (load a
> balance), and walk the app. Delete all of them before launch (`wema_preflight`
> enforces this).

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
- Run: `python manage.py wema_preflight` (no shell: `GET /preflight`)
  - Expect: `Wema live keys` → **PASS**, `Live host` → **FAIL** (still sandbox —
    correct at this step). `securityInfo` and the callback token are hard gates;
    configure strong development values before profiling or transaction testing.
- Or hit `/healthz` and confirm `funding_wema: true`, `wema_sandbox: true`.
  (`funding_wema_security_info` reports whether the required callback value is set.)

### Step 3 — live host
- Set `WEMA_BASE_URL` to the **live** ALAT host on the web service and all crons.
- Redeploy so every process picks up the new env.

### Step 4 — preflight must now say GO
- Run: `python manage.py wema_preflight` (no shell: `GET /preflight`, expect `200`)
  - Expect: **`RESULT: GO`** (every hard gate PASS). If not, stop and fix.
- `/healthz` should now show `wema_sandbox: false`.

### Step 5 — live connectivity probes (no money moved)
- `POST /wema-diagnose` with `Authorization: Bearer <WEMA_DIAG_TOKEN>` and JSON
  `{"account":"<10-digit>","bank":"<code>"}` — proves auth plus a real
  name-enquiry against the live gateway. BVN/NIN/OTP test inputs also belong in JSON,
  never a URL.
- `python manage.py wema_banks_sync` — compares our payout `bank_code`s against the
  rail's own `GetAllBanks` list. Ours were seeded from a NIBSS/Paystack mirror, and
  the rail resolves recipients in ITS code space: a bank whose code differs fails
  name enquiry, and the gateway reports that as *"account enquiry failed, confirm
  that the account number is valid"* — it never says the bank code is wrong. Exits 1
  while anything differs; `--apply` takes the rail's codes (ambiguous name matches
  are reported for a human, never auto-applied). **No shell:** the read-only half is
  the `bank_codes` block of `POST /wema-diagnose`, and the fix is Django admin →
  Banks → *"Sync bank codes from the payout rail"*.
- `GET /vtu-diagnose` with `Authorization: Bearer <DIAG_TOKEN>` — proves the VTU.ng wallet authenticates
  and shows its balance (VAS buys fail on an empty provider wallet).
- `GET /wema-callbacks-diagnose` with a diagnostic bearer token — prints the
  four callback URL **templates** and confirms each
  resolves and that a wrong secret is refused. Read
  `ready_to_send_to_the_bank: true` before handing anything to the bank; the
  `blockers` list says what to fix otherwise. The response never contains the
  callback secret; substitute it from the deployment secret store when profiling.

  Cheapest possible check without any tooling: open a callback URL in a browser.
  The method check runs before the secret check, so a live route answers **405**
  (POST-only). A **404** means the route isn't deployed; a **502/503** means the
  service isn't up.

### Step 5b — profile the callbacks for PRODUCTION
- The dev/sandbox callbacks were profiled on 2026-07-28; production profiling was
  deliberately deferred to this point by agreement with Wema. Combine the four templates
  from `/wema-callbacks-diagnose` with the rotated secret in the deployment secret store,
  send the resulting URLs to the bank through the agreed secure channel, and wait for confirmation — the rails
  do not work until the bank has them.
- Before sending, rotate `WEMA_CALLBACK_TOKEN` to a value that has NOT been used for dev
  (keep the old one in `WEMA_CALLBACK_TOKEN_PREV` for the overlap). The dev token was shared
  in a Slack channel in Wema's workspace, so reusing it for production carries that exposure
  across. See `docs/wema-callback-profiling.md` §2.
- Once a production callback has arrived, read the observed source IP out of
  `whatsapp.WebhookEvent` and then set `WEMA_CALLBACK_ENFORCE_IPS=true`.

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
