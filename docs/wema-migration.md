# Wema / ALAT integration — the SOLE money + KYC rail (2026-07)

**Status: live rail.** As of #188 (rail cutover) + #189 (VAS + cards), **Wema/ALAT is
the only money-movement rail** (wallet funding via OTP-provisioned NUBANs, bank payout +
recipient name enquiry + balance), **the Nigeria-KYC rail** (BVN/NIN identity is verified
by the name-matched NUBAN account-creation flow — ALAT has no standalone lookup, see KYC
below), a **VAS rail** (airtime always; data/cable once their catalogue is synced), and a
**virtual-card backend** (ALAT Card-Management). Monnify and Korapay/Baxi were deleted
entirely. **VTU.ng** remains the fallback VAS rail (electricity/betting/exams, and
data/cable until mapped); **Mono** is a secondary open-banking link/fund path;
face/liveness + address + ID-document OCR stay on **Prembly**.

The `*_PROVIDER` selectors default to `wema` when blank (kept only so a caller/diagnostic
that reads them keeps working) — you don't need to set them. The client (`utility/wema.py`)
runs **mock-first**: blank keys ⇒ mock in dev/tests, **fails closed in production**
(`mock_disabled_in_prod`) so a misconfigured deploy never fabricates an account / credit /
identity / card. `WEMA_SIMULATION=true` serves the mock *money* flow even in prod (identity
never mock-passes, even under simulation).

**Verified against Wema test keys** (via `/wema-diagnose`): auth model (per-product
`Ocp-Apim-Subscription-Key` + universal channel id) ✅, bank list ✅, recipient name
enquiry ✅.

**Reconciled against the ALAT OpenAPI bundle ("Wema API" set).** The money rails
(ProcessClientTransfer / FundWallet / name-enquiry / GetAllBanks / transhistoryV2 /
GetAccountV2), the BVN/NIN wallet-creation + OTP paths, and the airtime/data/bills
purchase paths **match the specs**. The following were corrected or newly wired from the
specs — see "What the specs resolved" below: transaction-status legend, data/bills
catalogue nesting, VAS status-requery codes, the real card-management endpoints, the KYC
model (no standalone lookup), and the Post-No-Debit (PND) lift a new NUBAN needs.

**⚠️ Still blocked on Wema before real money can move (VERIFY-BEFORE-LIVE):**
1. **`securityInfo` (the one true blocker)** — the encrypted field required on every
   money-movement call (transfer / credit_wallet / VAS) is **still not in any spec**.
   `utility.wema._security_info` returns `WEMA_SECURITY_INFO` (a static prebuilt value) or
   `""`; until it's set, a **live payout fails at the gateway → the debit auto-refunds** (an
   outage, not a leak). Sandbox does not enforce it.
2. **Sandbox provisioning + PND** — `apiplayground.alat.ng` returns a canned "download ALAT"
   response for BVN/NIN wallet-creation, so the account → lift-PND → fund → transfer loop
   can't be exercised end-to-end against sandbox. Needs a working provisioning path or a
   funded test source account. (The PND lift itself is now wired — see below.)
3. **VAS status-requery legend** — `PartnerPayment/CheckTransactionStatus` returns an
   INTEGER `transactionStatus` (enum 1..11) whose meaning ALAT doesn't publish; the client
   reads it but leaves such a purchase **PENDING** (never auto-settle/refund on an
   un-decodable code). Confirm the integer legend to enable auto-settlement of timed-out
   VAS buys. The bank-transfer `confirm_transfer_status` string legend is still matched
   defensively (`_SETTLED`/`_REVERSED`, now incl. ALAT's `SUCCESSFULL` spelling).
4. **Opaque card fields** — the card-management `virtualCard`/`virtual-card-details`
   responses carry an opaque `data` field (masked PAN / expiry / CVV shape not in the spec)
   and the request needs a `cardKey` (card product id, `WEMA_CARD_PRODUCT_KEY`) Wema must
   supply. Reveal/last4 depend on that shape. Confirm before relying on Wema cards.
5. **Live host** — set `WEMA_BASE_URL` to the live host (differs from sandbox).

**To go live:** get from Wema (a) the `securityInfo` spec, (b) a working provisioning path /
funded source account, (c) the VAS integer status legend + the `cardKey`, (d) the live host
+ keys — then set the `WEMA_*` env vars (already declared `sync:false` on the web service
**and** the `zitch-reconcile-wema` cron in `render.yaml`, schedule `*/10 * * * *`), and run
`manage.py seed_wema_plans` to map the data/cable catalogue. No `*_PROVIDER` flip is needed;
wema is already the default.

## What the specs resolved (this pass)

The ALAT OpenAPI bundle let us fix code that had been built on guessed shapes:

- **Transaction-status legend** — `transhistoryV2` documents `status ∈ {Default,
  Successfull, Failed, Pending}`. `normalize_transaction` now surfaces `status`/`settled`
  and the funding sweep (`apply_wema_credit`) **only credits a settled row** — a Failed row
  never credits and a Pending one waits for a later sweep (idempotent on `referenceId`), so
  we never book a deposit that hasn't landed.
- **Data/bills catalogue nesting** — data is `result[].dataPackages[]` (code = the package
  `id`) and bills are `result[] categories → billers[] → packages[]` (code = the package
  `id`). `get_data_plans`/`get_bills` now flatten to normalised rows; `seed_wema_plans`
  maps those onto `wema_code` (cable scoped to the provider's billers to avoid mis-maps).
- **VAS status requery** — `CheckTransactionStatus` takes an integer `transactionType`
  (1 = airtime, 2 = data) and returns an integer `transactionStatus`; the client sends the
  int and treats the un-decodable status code as PENDING (see blocker 3).
- **Virtual cards** — the real product is `/card-management /api/Partner/partnerCard/*`,
  **keyed by the NUBAN** (not a card token). issue (`virtualCard`) / reveal
  (`virtual-card-details/{accountNo}`) / block (`hotlistCard`) are wired; ALAT offers no
  reversible freeze or incremental top-up, so those report unsupported (the generic
  `CARD_ISSUER`, still the default card backend, supports both).
- **KYC identity** — ALAT has **no standalone BVN/NIN/vNIN lookup**; its "Full KYC" product
  opens/upgrades accounts and returns a name only after the OTP round-trip. So identity is
  verified by the NUBAN account-creation flow, and the holder name ALAT returns is
  name-matched (`holder_name_mismatch`) against the user's registered name before the tier
  is lifted (`wallet.views.wema_wallet_verify_otp`). The `verify_bvn/nin/vnin` entry points
  no longer call a non-existent endpoint — in prod they route the user to account setup.
- **Post-No-Debit (PND) lift** — a new Tier-1 NUBAN is provisioned under a PND hold (can
  receive, can't be debited). `lift_debit_restriction` (`PartnerDebitRestrictionManagement`)
  is now called right after provisioning so the per-user-balance payout/VAS debit works.
- **Available but not wired** — `get_kyc_status` (`/account-upgrade` tier/PND/address read),
  and the Remita, BNPL, account-upgrade (tier2/tier3) and NIP-charges products exist in the
  bundle but aren't consumed yet.

---

## What's wired

| Capability | Status |
|-----------|--------|
| Recipient name enquiry | **live-capable** (no securityInfo needed) |
| Bank payout (transfer out) | **wired**; needs `securityInfo` to settle live (else refunds) |
| Payout settlement (no webhook) | polled by `reconcile_wema` (Phase 2) |
| Wallet funding account (NUBAN) | **wired** — BVN→OTP→NUBAN, app drives it in `addmoney.tsx` |
| Inbound deposit crediting (no webhook) | polled by `reconcile_wema` (Phase 1); **only settled (Successfull) credit rows** |
| New-NUBAN PND lift | **wired** — `lift_debit_restriction` after provisioning (so the account can be debited) |
| KYC — BVN / NIN | **wired via provisioning** — the account-creation OTP flow verifies + name-matches; no standalone lookup |
| VAS — **airtime** | **wired** (auto-selects Wema once VAS keys are set; debits user NUBAN) |
| VAS — **data / cable** | **wired, gated per-plan** on a synced `wema_code` (else VTU.ng) |
| VAS — electricity / betting / exams | stays on VTU.ng (billers not mapped) |
| Virtual cards | **wired to real card-management** (NUBAN-keyed issue/reveal/block); no reversible freeze/top-up |

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
`GetAllBills` onto `wema_code`. The client now flattens ALAT's nested catalogues
(`get_data_plans` → `result[].dataPackages[]`, code = package `id`; `get_bills` →
`result[] categories → billers[] → packages[]`, code = package `id`), and the seeder matches
best-effort by price then normalised name (cable scoped to the provider's own billers;
`--dry-run` available; preserves `wema_code` on re-seed). VERIFY-BEFORE-LIVE: the airtime
network code (name vs code) and the `clientId` field value.

## Virtual cards (ALAT Card-Management)

The real product is `/card-management /api/Partner/partnerCard/*`, **keyed by the customer's
NUBAN** — so the `card_token` the `cards` app stores IS the account number. Three ops map to
real endpoints: **issue** (`virtualCard`, funded at creation), **reveal**
(`virtual-card-details/{accountNo}`) and a **permanent block** (`hotlistCard`). ALAT's
virtual-card product exposes **no reversible freeze and no incremental top-up**, so
`card_set_status(active=True)` and `card_fund` report unsupported rather than faking success —
the generic `CARD_ISSUER` (still the default when `WEMA_CARD_KEY` is unset) supports both.
Mock-first; fails closed in prod when unkeyed. VERIFY-BEFORE-LIVE: the opaque `data` field
shape (masked PAN / expiry / CVV) and the `cardKey` product id (`WEMA_CARD_PRODUCT_KEY`).

## Environment variables

Set these in the host (never in source). Boolean-only status is visible at `/healthz`.

- `WEMA_CHANNEL_ID` — the single channel id (sent as `x-api-key`, or `access` on the
  credit/debit-wallet/VAS products). **Same value for all products.**
- `WEMA_WALLET_KEY` — Wallet-Services subscription key (`Ocp-Apim-Subscription-Key`);
  covers wallet-creation, account-maintenance, credit and debit.
- `WEMA_KYC_KEY` — reserved. BVN/NIN identity is now verified through the wallet-creation
  (provisioning) products, which use `WEMA_WALLET_KEY`; ALAT has no standalone KYC-lookup
  product to key, so this is unused today (kept for a future Full-KYC account product).
- `WEMA_AIRTIME_KEY` / `WEMA_BILLS_KEY` — VAS subscription keys (airtime+data / bills).
- `WEMA_CARD_KEY` — Card-Management subscription key (enables the Wema virtual-card backend).
- `WEMA_CARD_PRODUCT_KEY` — the `cardKey` (card product id) ALAT's `virtualCard` request
  needs; distinct from the subscription key above. Supplied by Wema; blank until then.
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
3. **Transaction-status legend — partially RESOLVED.** `transhistoryV2` now documents
   `status ∈ {Default, Successfull, Failed, Pending}`, and the funding sweep gates on it
   (only settled credits apply). Two legends remain undocumented: the `confirm_transfer_status`
   status STRING for bank payouts (matched defensively via `_SETTLED`/`_REVERSED`, incl. the
   `SUCCESSFULL` spelling), and the integer `transactionStatus` (1..11) the VAS
   `CheckTransactionStatus` returns (left PENDING until the legend is confirmed). Confirm both.
4. **Wallet-creation OTP response shape — STILL OPEN.** The spec confirms the create endpoints
   return `{message, status, code, statusCode, errors}` with **no** tracking id, yet the OTP
   validate step requires `trackingId`. `create_wallet_request` looks for it at the top level
   and under `data`/`result` to tolerate the live (undocumented) shape. Confirm where the live
   gateway returns `trackingId`/`otpTrackingID`.
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
