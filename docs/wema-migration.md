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
1. **Production webhook profiling** — **BLOCKER FOR LIVE.** Wema confirmed the
   development callbacks were profiled on 2026-07-28, but dev is a simulator and proves
   routing only. Production profiling was deliberately deferred to cutover. Rotate the
   callback token, generate the four production URLs, have Wema profile them, and receive
   a real callback before disabling simulation. See `docs/wema-callback-profiling.md`.
2. ~~**`securityInfo`**~~ — **RESOLVED, not a blocker.** Wema confirmed (2026-07-27) it is
   "a private key best known to you" which the bank simply echoes back to our Authentication
   Callback. We choose the value; nothing is issued and nothing is owed. See the
   `securityInfo` section below.
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

**To go live:** Wema confirmed Compliance Approval on 2026-07-24 and asked their team to
"push the Partner live"; what that produced has not been reported back, so chase it as a
follow-up rather than a fresh request. Then get from Wema (a) a working provisioning path /
funded source account, (b) the VAS integer status legend + the `cardKey`, (c) the live host
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
- **Current optional-product coverage** — Remita validation/payment, read-only BNPL
  offers, NIP charge enquiry, and Tier-3 address-verified upgrade/status sync are wired
  behind dedicated keys. Tier-2 bank sync remains unavailable because Zitch does not
  retain the required BVN/NIN pair, and BNPL consent/disbursement stays gated pending
  product and compliance sign-off.

---

## What's wired

| Capability | Status |
|-----------|--------|
| Recipient name enquiry | **live-capable** |
| Bank payout (transfer out) | **wired**; production requires `securityInfo` to match the callback (see below) |
| Payout settlement (no webhook) | polled by `reconcile_wema` (Phase 2) |
| Wallet funding account (NUBAN) | **wired** — BVN→OTP→NUBAN, app drives it in `addmoney.tsx` |
| Inbound deposit crediting (no webhook) | polled by `reconcile_wema` (Phase 1); **only settled (Successfull) credit rows** |
| New-NUBAN PND lift | **wired** — `lift_debit_restriction` after provisioning (so the account can be debited) |
| KYC — BVN / NIN | **wired via provisioning** — the account-creation OTP flow verifies + name-matches; no standalone lookup |
| VAS — **airtime** | **wired** (auto-selects Wema once VAS keys are set; debits user NUBAN) |
| VAS — **data / cable** | **wired, gated per-plan** on a synced `wema_code` (else VTU.ng) |
| VAS — electricity / betting / exams | stays on VTU.ng (billers not mapped) |
| VAS — **Remita RRR** | **wired** — `validate_rrr` + `payremita` debit the user NUBAN (pending stays for manual recon) |
| Virtual cards | **wired to real card-management** (NUBAN-keyed issue/reveal/block); no reversible freeze/top-up |
| NIP transfer charges | **wired** — `payout_charge` + `/api/transfers/charge/` (informational; debit unchanged) |
| Account tier upgrade | **tier 3 synced** on address verify (bank-side limits); tier 2 needs BVN/NIN we don't retain |
| BNPL | **offers wired** (read-only `/api/loans/bnpl/offers/`); consent→disburse gated on product sign-off |
| Fund from partner-bank account | **removed** — Pay-with-Bank required the customer to bank with the provider and named it in-app |
| NUBAN bank statement | **wired** — `/api/wallet/statement/` over transhistoryV2 |

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

- `WEMA_CHANNEL_ID` — the channel id (sent as `x-api-key`, or `access` on
  the credit/debit-wallet/VAS products).
- `WEMA_WALLET_KEY` — **Wallet Services** subscription. The live product page lists
  six capabilities: wallet creation, credit wallet, debit wallet, bills payment,
  transaction notification and account management.
- `WEMA_CARD_KEY` — **Virtual Naira Card** subscription. Required for all card calls;
  Wallet Services is not a fallback. `WEMA_CARD_PRODUCT_KEY` is the separate `cardKey`
  product id required for issuance.
- `WEMA_AIRTIME_KEY` — **Airtime and Data API** subscription. Without it, AUTO routing
  stays on VTU.ng and an explicit `VAS_PROVIDER=wema` fails preflight.
- `WEMA_BILLS_KEY` — optional override; Bills Payment is covered by Wallet Services.
- `WEMA_UPGRADE_KEY` — **Account Upgrade API** subscription used for tier/status sync.
- `WEMA_REMITA_KEY` — **Remita Payment** subscription; no wallet-key fallback.
- `WEMA_BNPL_KEY` + `WEMA_BNPL_MERCHANT_ID` + `WEMA_BNPL_AUTH_KEY` — the separate
  BNPL product and merchant credentials. Simulation or any missing credential disables it.
- `WEMA_KYC_KEY` — reserved for the separate Partnership Account KYC API; the current
  BVN/NIN identity flow instead verifies through wallet provisioning.
- The subscribed **Address Verification**, **Scheduled Direct Debit**, **Get Statement**
  and **Pay with Bank Account** keys are not runtime secrets today: those products are
  not used by the core dedicated-NUBAN wallet flow. Do not store unused credentials.
- `WEMA_SOURCE_ACCOUNT` — our pool NUBAN that funds pool-sourced payouts.
- `WEMA_SECURITY_INFO` — a strong random seed chosen by Zitch. The seed never crosses
  the wire; a unique per-reference HMAC is sent and verified on the callback. Production
  preflight requires at least 32 characters.
- `WEMA_BASE_URL` — `https://apiplayground.alat.ng` (sandbox). Set the bank-supplied
  live host for go-live.
- `WEMA_SIMULATION=true` — serve mock flows in a real build without moving money or debt.
- `PAYOUT_PROVIDER` / `PAYMENT_PROVIDER` / `VAS_PROVIDER` / `KYC_PROVIDER` /
  `CARD_PROVIDER` — optional selectors; blank uses the safe AUTO rules.

To test payout in sandbox: set `WEMA_CHANNEL_ID` + `WEMA_WALLET_KEY` + `WEMA_SOURCE_ACCOUNT`
(`WEMA_SECURITY_INFO` may be left blank — the call then carries the `SECRET_KEY`-derived value,
never an empty one), redeploy, run a name-enquiry + a small transfer. `/healthz` should show
`payout_provider: "wema"`.

## Spec reconciliation — against the full ALAT OpenAPI set (2026-07)

Every rail in `utility/wema.py` was reconciled field-by-field against the official ALAT
OpenAPI specs (19 files: wallet-creation/BVN, credit/debit-wallet, account-maintenance,
airtime-data, bills, card-management, the partnership-account/KYC/upgrade family, plus the
new Remita / pay-with-bank / BNPL products). Summary:

| Rail | Verdict | Notes |
|------|---------|-------|
| Wallet creation (NIN + BVN + OTP) | ✅ paths/fields correct | `trackingId` lives at `data.otpTrackingID` on the live envelope; ResendOtp is **200 No-Content** (now handled). |
| Balance + transaction history | ✅ correct | `status` now honored — see funding guard below. |
| Debit wallet / transfer (payout) | ✅ correct | `ClientTransferRequestDto` is a perfect field match. `GetNIPCharges` is unused (optional). |
| Credit wallet / FundWallet | ✅ correct | Status poll is bound to the `debit` suffix; a credit-rail poll is optional. |
| Airtime & Data | ⚠️ mostly correct | On the right (Client/SingleAccount) endpoints. **Requery bug:** `CheckTransactionStatus` returns `result.transactionStatus` as an **integer** enum (1–11), not a string — legend needed (below). |
| Bills payment | ⚠️ mostly correct | Same integer-status requery gap (`checktransactionstatus`, enum 1–9). `packageId` is int32 in the spec. |
| **Virtual cards** | ⚠️ wired, live-shape check required | The real card-management issue/reveal/block paths are wired; Wema must supply `cardKey` and confirm the opaque response shape before production. |
| **KYC (BVN/NIN/vNIN)** | ✅ corrected model | Wema has no standalone identity lookup; Zitch verifies and name-matches identity through the wallet-provisioning OTP flow. |

**Funding-correctness fix (landed):** `normalize_transaction` now treats an inbound
`creditType=='Credit'` row as fundable only when its `status` is settled. The ALAT
`TransactionStatus` enum is `{Default, Successfull(sic), Failed, Pending}`; a **Pending**
(in-flight) or **Failed** (bounced) credit row is skipped, so a deposit is never credited
before it settles. Unknown/blank still counts (a live gateway that omits the field can't
strand real money); a Pending row credits on a later sweep once it settles.

### `securityInfo` — dynamic per transaction

Wema confirmed that Zitch chooses the private value and that the bank returns
`securityInfo` with the custom `transactionReference` to the Authentication Callback.
The current portal additionally requires it to be highly secure, dynamic and unique for
each transaction.

`WEMA_SECURITY_INFO` is therefore a private **seed**, not the value sent on the wire.
For every money request, Zitch sends:

```
HMAC-SHA256(seed, "zitch:wema:transaction:" + transactionReference)
```

The callback recomputes that value from the echoed reference using constant-time
comparison, then still requires a fresh PENDING bank-payout ledger row. A copied
`securityInfo` from one transaction cannot authorize another reference, and the seed
never leaves Zitch.

ALAT rejects a blank value. Development can derive a stable seed from `SECRET_KEY`, but
go-live preflight requires an explicit random `WEMA_SECURITY_INFO` of at least 32
characters so rotating Django's key cannot invalidate transactions in flight.

`securityInfo` is carried by: `ProcessClientTransfer`, `FundWallet`, `PurchaseAirtime`/`Data`
(Client **and** pool — **required, minLength 1** on the pool variants), `PayBill` (+ pool),
`ProcessRemitaPayment`. It is **absent** from account-creation, balance/history, name-enquiry,
cards and KYC.

### Card rail — DONE (re-pointed to the real Card Management API)

The old `/api/VirtualCard/*` paths were fabricated; the client now uses the real
**Card Management API** (`utility.wema.card_*`, threaded through `providers.card_*` + the
`cards` app):
- APIM suffix **`/card-management`** (not `/virtual-card`); auth header **`x-api-key`** (not `access`); **no `securityInfo`**.
- Keyed by the customer **NUBAN (`accountNo`)**, so the stored `card_token` IS the NUBAN:
  - issue → `POST /api/Partner/partnerCard/virtualCard` (`{accountNo, emailaddress, phoneNumber, amount, customerAddress, cardKey, currency:'NGN'}`)
  - reveal → `GET /api/Partner/partnerCard/virtual-card-details/{accountNo}` (or `retrieveCard/{accountNo}` for full PAN/CVV)
  - block → `POST /api/Partner/partnerCard/hotlistCard?maskedPan=&accountNumber=` (**block-only — no unfreeze endpoint exists**, so `card_set_status(active=True)` reports unsupported)
  - fund → none; funding is only the optional `amount` at creation, so `card_fund` reports unsupported
- VERIFY-BEFORE-LIVE: the opaque `data` shape (masked PAN/expiry/CVV) and the `cardKey`
  (`WEMA_CARD_PRODUCT_KEY`) need a live-key smoke test.

### KYC rail — DONE (verified via account creation, name-matched)

Wema exposes **no** BVN/NIN/vNIN lookup endpoint — the old `/api/Kyc/Verify*` paths were
fabricated and 404 live. BVN/NIN are validated only as a byproduct of account creation; current
tier reads from `GET /api/partnership/partner-account-kyc-status` (`get_kyc_status`).
**Resolution (chosen: fold into account creation):** identity is verified by the NUBAN
account-creation OTP flow, and the holder name ALAT returns is name-matched
(`holder_name_mismatch`) against the user's registered name before the tier lifts
(`wallet.views.wema_wallet_verify_otp`). `verify_bvn`/`verify_nin`/`verify_vnin` no longer hit a
non-existent endpoint — in production they route the caller to account setup; dev/tests keep the
mock. (Prembly stays the image/biometric KYC rail for face/address/ID.)

### Transaction-status legends — still needed from Wema

- **Transfer/credit:** `result.status` / `result.data.status` are plain strings; the spec
  examples are all `"string"`. `reconcile_wema` matches SUCCESS/FAILED families defensively.
- **VAS airtime/data requery:** `CheckTransactionStatus` → `result.transactionStatus` is an
  **integer enum (1–11)**; `transactionType` on the request is an **int enum {1,2}** (not the
  string `'airtime'`/`'data'`). Requery cannot interpret the code until Wema supplies the map.
- **Bills requery:** `checktransactionstatus` → `result.transactionStatus` **integer enum (1–9)**.
- **History:** `TransactionStatus {Default, Successfull, Failed, Pending}` — now honored.

### New portal products — now wired (client + endpoints)

The follow-up rails from the bundle are wired (mock-first, fail-closed):

- **NIP transfer charges** — `get_nip_charges` + `nip_fee_for` (`/debit-wallet/GetNIPCharges`);
  `providers.payout_charge` caches the schedule and `POST /api/transfers/charge/` returns the
  fee for an amount. **Informational** — the send flow debit is unchanged (whether to pass the
  NIP fee to users is a pricing decision, deliberately not made here).
- **Account tier upgrade** — `upgrade_tier2` ({accountNumber, nin, bvn, liveImageOfFace}) and
  `upgrade_tier3` ({residentialAddress, accountNumber}) on `/account-upgrade`. Tier 3 is
  best-effort synced when the user verifies their address (`accounts.views.kyc_address`), so the
  NUBAN's bank-side limits track the user's KYC. Tier 2 is **not** auto-synced: it needs the raw
  BVN/NIN, which Zitch doesn't retain post-provisioning (only an HMAC + last4).
- **Remita** — `validate_rrr` / `pay_remita` / `remita_receipt` (`/remita-payment`).
  `POST /api/utility/validate_rrr/` + `POST /api/utility/payremita/` validate then pay an RRR
  from the user's NUBAN via `run_provider_purchase` (debit → pay → settle/refund). A timed-out
  payment stays PENDING for manual reconciliation — ALAT has no Remita status endpoint, so
  `vas_status("…","remita")` never auto-settles/refunds or mis-routes to the airtime check.
- **BNPL** — `bnpl_offers` / `bnpl_consent` / `bnpl_accept_terms` / `bnpl_status` /
  `bnpl_liquidate` on `/alat-bnpl` with the merchant-auth headers (`x-merchant-id` +
  `x-merchant-authorization-key`, config `WEMA_BNPL_*`). Only the **read-only** offers endpoint
  (`POST /api/loans/bnpl/offers/`) is exposed; the consent→accept→disburse commitment flow is
  built at the client layer but intentionally NOT exposed as an end-user endpoint — it creates
  real external debt and needs product/compliance sign-off first.

- **Pay with Bank Account (ALAT Authenticator)** — **removed** (2026-07-27). It pulled funds
  from the customer's *own* account at the partner bank, which they approved in that bank's
  app, so it only worked for customers who already banked there and it named the provider in
  user-facing copy — both wrong for Zitch, whose customers are on a separate platform and must
  never see the upstream brand. The endpoints (`/api/wallet/alat/fund/` + `/verify/`), the
  `pwba_*` client and `WEMA_PWBA_KEY` are all gone; funding is by bank transfer to the
  dedicated NUBAN, reconciled by the poller. Recoverable from git history if ever needed.
- **Get Statement** — `POST /api/wallet/statement/` returns the user's Wema NUBAN bank statement
  (ALAT transhistoryV2, normalised) for a date range; distinct from the Zitch ledger history.

- **Direct Debit / Scheduled Payments** — intentionally not exposed in Zitch. The live
  portal now documents four merchant endpoints (setup mandate, consent status, run schedule,
  schedule lookup) under `/merchant-direct-debit`. Shipping this is a separate product, not
  an API-wrapper task: it needs mandate persistence, customer-consent UX, cancellation,
  retries, notices and compliance approval. Its subscription key should remain out of Render
  until that product is approved and implemented end to end.

## ⚠️ Open decisions — confirm with Wema before go-live

1. **Money-flow model — RESOLVED: per-user balances.** Each user's NUBAN holds its own
   balance. `payout_send` debits the **sender's own NUBAN** (`execute_payout` passes the
   sender's `wallet.account_number` as `source_account`); the shared `WEMA_SOURCE_ACCOUNT`
   pool is only a fallback for a sender who has no Wema NUBAN yet. A live payout with neither
   fails closed (refundable). To pay out via Wema a user must have a Wema NUBAN with balance,
   or `WEMA_SOURCE_ACCOUNT` must be funded to cover pool-sourced payouts.
2. ~~**`securityInfo` construction.**~~ **CLOSED 2026-07-27** — there is no construction. It
   is a value we pick that the bank echoes back to our Authentication Callback.
3. **Transaction-status legends.** `transhistoryV2` history status is now documented and
   honored (`{Default, Successfull, Failed, Pending}` — only settled credits fund). Two legends
   remain: the `confirm_transfer_status` bank-payout status STRING (matched defensively via
   `_SETTLED`/`_REVERSED`, incl. the `SUCCESSFULL` spelling), and the **integer**
   `transactionStatus` the VAS/bills `CheckTransactionStatus` returns (enum 1..11) — the code
   reads it but leaves such a purchase PENDING until the code→meaning map is confirmed. Confirm
   both with Wema.
4. **Wallet-creation OTP response shape.** The create endpoints return
   `{message, status, code, statusCode, errors}` with **no** tracking id per the spec, yet the
   OTP validate step requires `trackingId`; `create_wallet_request` hunts for it at the top
   level and under `data`/`result` (the live envelope is reported to carry it at
   `data.otpTrackingID`). `ResendOtp` is a **200 No-Content** endpoint — now handled (a bare
   `.json()` on the empty body used to raise on a genuine success). Confirm the live shape.
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

## Callbacks and reconciliation

ALAT's integration guide requires the partner to **profile callback URLs with the bank**, and
the rails do not work until it has them: account creation is refused without a profiled
Account Creation URL, and transactions fail authentication without the Authentication URL.
(An earlier version of this doc claimed ALAT exposes no webhooks and that we deliberately
registered none. That was wrong, and it is the reason sandbox wallet creation returned a
canned "use the bank's own app" message with no tracking id.)

### The four callbacks — `backend/wallet/wema_callbacks.py`

| Purpose | Route | Behaviour |
|---|---|---|
| Account Creation (`requestType 2`) | `/webhooks/wema/account/<token>` | provisions the NUBAN idempotently; does **not** lift KYC tier |
| Authentication | `/webhooks/wema/authorize/<token>` | the bank asks whether a payout may proceed; we answer `{transactionReference, authorized}` |
| Transaction (`requestType 3`) | `/webhooks/wema/transaction/<token>` | settles/refunds by **re-querying**, never from the payload |
| Transaction Notification (prod) | `/webhooks/wema/notification/<token>` | recorded only; payload undocumented |

**Security.** ALAT signs nothing, so the endpoints stack a secret in the URL path
(`WEMA_CALLBACK_TOKEN`) and a source-IP allowlist against the egress addresses below
(`WEMA_CALLBACK_ENFORCE_IPS`), both applied *before* the body is parsed.

There is deliberately **no per-IP rate limit**. `client_ip()` resolves through
`RATELIMIT_TRUSTED_PROXY_HOPS`, and on this deployment every bank callback currently
arrives bearing the same platform-internal address (observed `10.30.1.250`) — so a
per-IP bucket would be shared by all of the bank's traffic rather than isolating an
attacker, throttling real callbacks while bounding nobody. Add one once the hop count
is corrected and the bank's true source address is visible. Until then the abuse that
actually costs something — the transaction callback acting as a 1:1 amplifier into
ALAT's own API — is bounded per-reference by `REQUERY_COOLDOWN` instead.

On top of that neither money-moving handler trusts its payload:

- **`authorize`** answers `true` only when our own ledger holds a *fresh PENDING bank payout*
  under that exact reference (`meta["bank"]`, younger than `WEMA_AUTH_MAX_AGE`). Holding the
  URL is not sufficient. Every refusal returns an identical body, so it is not an oracle for
  "is a payout in flight right now". Any error fails closed to `authorized: false` — a wrong
  deny is an outage, a wrong approve is irreversible.
- **`transaction`** is a *trigger, not an oracle*: it re-queries `confirm_transfer_status` over
  the authenticated APIM channel and settles from that, so a forged, replayed or out-of-order
  callback is at worst an unnecessary requery. ALAT publishes no status legend, so this also
  avoids inventing one.
- **Nothing credits a wallet from a callback.** The `requestType 3` payload carries no amount
  and no account number; a credit path under a no-signature trust model would be a
  money-printing primitive.

`securityInfo` appears on the **authentication callback** — the bank sends it to us alongside
the transaction reference and expects an authorisation decision back. Matching it against
`WEMA_SECURITY_INFO` is opt-in (`WEMA_AUTH_REQUIRE_SECURITY_INFO`, default off) because that
value is blank in every environment, and requiring a match against a blank value would deny
every payout.

### Still polled

`python manage.py reconcile_wema` (render cron, every 10 min) remains the safety net, and is
still the **only** source of deposit credits — ALAT exposes no inbound-credit webhook:
- **Funding:** sweeps each Wema-provisioned wallet's history and credits inbound deposits,
  idempotent on Wema's `referenceId` stored as `WEMA-CR-<referenceId>` (namespaced so it can
  never collide with a `ZTRF`/`ZPAY`/`ZFND` ledger reference).
- **Payouts:** settles/reverses PENDING bank payouts by polling `confirm_transfer_status`.
  The transaction callback routes through the same settlement path, so whichever arrives
  first wins and the other is a no-op.

### Wema egress IPs

Wema confirmed (2026-07-27) that IP allowlisting is done on the partner's side — they do not
allowlist our outbound addresses. Their gateway's egress IPs, the addresses the callbacks
above arrive **from**, are:

| | Address |
|---|---|
| Egress IP 1 | `135.236.18.76` |
| Egress IP 2 | `74.178.162.156` |

These are enforced in production by default; debug and automated-test environments default
off so the trusted-proxy hop can be diagnosed safely. Keep `WEMA_CALLBACK_IPS`
environment-overridable — Azure egress addresses can change — and validate the resolved
source IP before enabling a development callback test. The allowlist is a second factor,
never the only one: the path secret, `securityInfo`, and ledger-state checks above are what
protect the money.

**That condition is now checkable rather than a matter of waiting.** Dev was profiled on
2026-07-28, so callbacks have arrived, and every inbound callback records its source in
`whatsapp.WebhookEvent.remote_ip` — including the ones we refused. So the observed source IP
can be read off the table before enforcement is switched on, instead of being guessed:

```sql
SELECT remote_ip, source, outcome, COUNT(*) FROM whatsapp_webhookevent
WHERE source LIKE 'wema.%' GROUP BY 1, 2, 3 ORDER BY 4 DESC;
```

If those rows show only the two addresses above, enforcement can go on. If they show a third,
add it to `WEMA_CALLBACK_IPS` **before** enabling — enforcement with an incomplete list
refuses real callbacks, and on the account-creation route that means customers silently never
get a NUBAN.

Our own outbound addresses (`209.97.130.65`, `68.183.254.113`) were shared with Wema on
2026-07-24, and their answer was *"Whitelisting IPs will be done from your end"* — i.e. calls
**to** their API are not source-IP filtered, and nothing on the bank side holds our
addresses. Auth is `Ocp-Apim-Subscription-Key` + channel id, as the code assumes. Do not
expect the shared addresses to have been allowlisted anywhere.
