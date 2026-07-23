# Wema / ALAT integration — the SOLE money + KYC rail (2026-07)

**Status: live rail.** As of #188 (rail cutover) + #189 (VAS + cards), **Wema/ALAT is
the only money-movement rail** (wallet funding via OTP-provisioned NUBANs, bank payout +
recipient name enquiry + balance), **the sole Nigeria-KYC rail** (BVN/NIN/vNIN via the
Full KYC product), a **VAS rail** (airtime always; data/cable once their catalogue is
synced), and a **virtual-card backend** (Wema Virtual Naira Card). Monnify and
Korapay/Baxi were deleted entirely. **VTU.ng** remains the fallback VAS rail
(electricity/betting/exams, and data/cable until mapped); **Mono** is a secondary
open-banking link/fund path; face/liveness + address + ID-document OCR stay on **Prembly**.

The `*_PROVIDER` selectors default to `wema` when blank (kept only so a caller/diagnostic
that reads them keeps working) — you don't need to set them. The client (`utility/wema.py`)
runs **mock-first**: blank keys ⇒ mock in dev/tests, **fails closed in production**
(`mock_disabled_in_prod`) so a misconfigured deploy never fabricates an account / credit /
identity / card. `WEMA_SIMULATION=true` serves the mock *money* flow even in prod (identity
never mock-passes, even under simulation).

**Verified against Wema test keys** (via `/wema-diagnose`): auth model (per-product
`Ocp-Apim-Subscription-Key` + universal channel id) ✅, bank list ✅, recipient name
enquiry ✅.

**⚠️ Still blocked on Wema before real money can move (VERIFY-BEFORE-LIVE):**
1. **`securityInfo`** — the encrypted field required on every money-movement call
   (transfer / credit_wallet / VAS / card fund) is undocumented. `utility.wema._security_info`
   returns `WEMA_SECURITY_INFO` (a static prebuilt value) or `""`; until it's set, a **live
   payout fails at the gateway → the debit auto-refunds** (an outage, not a leak). Sandbox
   does not enforce it.
2. **Sandbox provisioning** — `apiplayground.alat.ng` returns a canned "download ALAT"
   response for BVN/NIN wallet-creation (no `trackingId`, no OTP), so the account → fund →
   transfer loop can't be exercised end-to-end against sandbox. Needs a working provisioning
   path or a funded test source account.
3. **Transaction-status legend** — the `confirm_transfer_status` value set is undocumented;
   `reconcile_wema` matches SUCCESS/FAILED families defensively (`_SETTLED`/`_REVERSED`) and
   leaves anything else PENDING.
4. **Catalogue + endpoint field names** — the data/cable/card/KYC endpoint paths and fields
   follow the ALAT pattern but weren't exercised live; confirm against Wema's integration
   guide (and whether cards/KYC need `securityInfo`) before go-live.
5. **Live host** — set `WEMA_BASE_URL` to the live host (differs from sandbox).

**To go live:** get from Wema (a) the `securityInfo` spec, (b) a working provisioning path /
funded source account, (c) the status legend, (d) the live host + keys — then set the `WEMA_*`
env vars (already declared `sync:false` on the web service **and** the `zitch-reconcile-wema`
cron in `render.yaml`, schedule `*/10 * * * *`), and run `manage.py seed_wema_plans` to map the
data/cable catalogue. No `*_PROVIDER` flip is needed; wema is already the default.

---

## What's wired

| Capability | Status |
|-----------|--------|
| Recipient name enquiry | **live-capable** (no securityInfo needed) |
| Bank payout (transfer out) | **wired**; needs `securityInfo` to settle live (else refunds) |
| Payout settlement (no webhook) | polled by `reconcile_wema` (Phase 2) |
| Wallet funding account (NUBAN) | **wired** — BVN→OTP→NUBAN, app drives it in `addmoney.tsx` |
| Inbound deposit crediting (no webhook) | polled by `reconcile_wema` (Phase 1) |
| KYC — BVN / NIN / vNIN | **wired** to tier (all three name-matched); fails closed in prod |
| VAS — **airtime** | **wired** (auto-selects Wema once VAS keys are set; debits user NUBAN) |
| VAS — **data / cable** | **wired, gated per-plan** on a synced `wema_code` (else VTU.ng) |
| VAS — electricity / betting / exams | stays on VTU.ng (billers not mapped) |
| Virtual cards | **wired** (Wema Virtual Naira Card; `card_provider()` auto-selects when `WEMA_CARD_KEY` set) |

## VAS (airtime / data / cable) — #189

`vas_provider()` auto-selects Wema once its VAS keys are configured (else VTU.ng), so a
deploy without Wema VAS keys never breaks airtime/data/bills. When Wema is selected, routing
is **per-service** (`utility.providers._wema_vas_route`):

- **Airtime** → Wema immediately (network + amount, no catalogue), debiting the sender's own
  NUBAN (`accountNumber`) — per-user model.
- **Data / cable** → Wema only once the plan's `wema_code` (new on `DataPlan`/`CablePlan`,
  migration `utility/0002`) is populated; a blank code keeps that plan on VTU.ng, so the
  cutover is incremental and can't break a purchase whose Wema code isn't mapped.
- **Electricity / betting / exams** → VTU.ng until their Wema billers are mapped.

The fulfilling rail is stamped on the ledger row (`vas_rail`/`vas_type`) so a PENDING purchase
is requeried against the SAME rail (`reconcile_vtu` / `vtu_requery` won't mis-route a Wema
purchase to VTU.ng or vice versa).

**Sync the catalogue:** `manage.py seed_wema_plans` maps Wema's live `GetDataPlans` /
`GetAllBills` onto `wema_code` (best-effort by price then normalised name; `--dry-run`
available; preserves `wema_code` on re-seed). VERIFY-BEFORE-LIVE: the ALAT catalogue field
names, the airtime network code (name vs code), and the `clientId` field value.

## Virtual cards (Wema Virtual Naira Card) — #189

`card_provider()` auto-selects `"wema"` once `WEMA_CARD_KEY` is set (else the generic
`CARD_ISSUER`). `utility.wema.card_issue` / `card_set_status` / `card_fund` / `card_reveal`
return the same shapes the `cards` app + `providers.card_*` wrappers expect. Mock-first with a
deterministic fake card; fails closed in prod when unkeyed. VERIFY-BEFORE-LIVE: the ALAT
virtual-card endpoint paths/fields and whether they need `securityInfo`.

## Environment variables

Set these in the host (never in source). Boolean-only status is visible at `/healthz`.

- `WEMA_CHANNEL_ID` — the single channel id (sent as `x-api-key`, or `access` on the
  credit/debit-wallet/VAS products). **Same value for all products.**
- `WEMA_WALLET_KEY` — Wallet-Services subscription key (`Ocp-Apim-Subscription-Key`);
  covers wallet-creation, account-maintenance, credit and debit.
- `WEMA_KYC_KEY` — Full KYC subscription key. **Without it, KYC fails closed in prod**, so no
  user can verify a BVN/NIN, get a tier, or provision a NUBAN — set it before go-live.
- `WEMA_AIRTIME_KEY` / `WEMA_BILLS_KEY` — VAS subscription keys (airtime+data / bills).
- `WEMA_CARD_KEY` — Virtual Naira Card subscription key (enables the Wema card backend).
- `WEMA_SOURCE_ACCOUNT` — our pool NUBAN that funds outbound transfers (see money-flow note).
- `WEMA_SECURITY_INFO` — the encrypted `securityInfo` for money-movement calls. **Not
  enforced in sandbox**; required before live.
- `WEMA_BASE_URL` — `https://apiplayground.alat.ng` (sandbox). Set the live host for go-live.
- `WEMA_SIMULATION=true` — serve the mock flow in a real build without live keys (no money moves).
- `PAYOUT_PROVIDER` / `PAYMENT_PROVIDER` / `VAS_PROVIDER` / `KYC_PROVIDER` / `CARD_PROVIDER` —
  optional selectors; blank already auto-resolves (wema for money/KYC; wema-when-keyed for
  VAS/card). You don't need to set them.

To test payout in sandbox: set `WEMA_CHANNEL_ID` + `WEMA_WALLET_KEY` + `WEMA_SOURCE_ACCOUNT`
(leave `WEMA_SECURITY_INFO` blank), redeploy, run a name-enquiry + a small transfer. `/healthz`
should show `payout_provider: "wema"`.

## ⚠️ Open decisions — confirm with Wema before go-live

1. **Money-flow model — RESOLVED: per-user balances.** Each user's NUBAN holds its own
   balance. `payout_send` debits the **sender's own NUBAN** (`execute_payout` passes the
   sender's `wallet.account_number` as `source_account`); the shared `WEMA_SOURCE_ACCOUNT`
   pool is only a fallback for a sender who has no Wema NUBAN yet. A live payout with neither
   fails closed (refundable). To pay out via Wema a user must have a Wema NUBAN with balance,
   or `WEMA_SOURCE_ACCOUNT` must be funded to cover pool-sourced payouts.
2. **`securityInfo` construction.** The encryption scheme (algorithm / what is signed / key
   material) is not in the OpenAPI. Implement in `utility.wema._security_info` once Wema
   supplies it. Sandbox does not enforce it.
3. **Transaction-status legend.** `confirm_transfer_status` returns a status string; the value
   set is undocumented. `reconcile_wema` matches SUCCESS/FAILED families defensively
   (`_SETTLED` / `_REVERSED`) and leaves anything else PENDING. Confirm the real values.
4. **Wallet-creation OTP response shape.** Per the OpenAPI the create endpoints return
   `ResponseModel` with no tracking id; `create_wallet_request` looks for the tracking id in
   several places to tolerate the live (undocumented) shape. Confirm where the live gateway
   returns `trackingId`/`otpTrackingID`.
5. **Inbound-credit detection — reversal double-credit now GUARDED (was audit OPEN, High).**
   The funding sweep (Phase 1) credits `creditType == "Credit"` history rows, while the payout
   phase (Phase 2) independently reverses a FAILED payout via `reverse_transfer`. A
   reversed/bounced payout landing back in the sender's own NUBAN was previously counted
   **twice** — once per phase — leaking float. The sweep now excludes self-reversals:
   `apply_wema_credit` matches each credit row (whole raw row, any field, so it doesn't depend
   on the undocumented reversal shape) against the wallet owner's own outbound payout
   references and routes a hit through `reverse_transfer` — which refunds at most once, ever,
   across both phases — instead of crediting it as funding. Third-party deposits (including
   another user's payout arriving) still credit normally. **Remaining before go-live:** confirm
   the `transhistoryV2` date format and that a genuine deposit is distinguishable from any
   credit we push ourselves via `FundWallet` (`credit_wallet` has no production caller today),
   and add a ledger-vs-polled-NUBAN reconciliation invariant that alarms on divergence — that
   part still needs Wema's live reversal history shape.

## No-webhook reconciliation

ALAT exposes no webhooks, so `python manage.py reconcile_wema` (render cron, every 10 min):
- **Funding:** sweeps each Wema-provisioned wallet's history and credits inbound deposits,
  idempotent on Wema's `referenceId` stored as `WEMA-CR-<referenceId>` (namespaced so it can
  never collide with a `ZTRF`/`ZPAY`/`ZFND` ledger reference).
- **Payouts:** settles/reverses PENDING bank payouts by polling `confirm_transfer_status`
  (only when `payout_provider() == "wema"`, which is the default).
