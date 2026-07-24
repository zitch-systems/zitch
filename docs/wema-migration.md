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
| VAS — **Remita RRR** | **wired** — `validate_rrr` + `payremita` debit the user NUBAN (pending stays for manual recon) |
| Virtual cards | **wired to real card-management** (NUBAN-keyed issue/reveal/block); no reversible freeze/top-up |
| NIP transfer charges | **wired** — `payout_charge` + `/api/transfers/charge/` (informational; debit unchanged) |
| Account tier upgrade | **tier 3 synced** on address verify (bank-side limits); tier 2 needs BVN/NIN we don't retain |
| BNPL | **offers wired** (read-only `/api/loans/bnpl/offers/`); consent→disburse gated on product sign-off |
| Fund from ALAT account | **wired** — Pay-with-Bank direct debit (`/api/wallet/alat/fund/` + `/verify/`, credits once) |
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
| **Virtual cards** | ❌ **paths wrong** | `/api/VirtualCard/*` do not exist — see Card rail below. Rail is gated off, so this blocks cards only, not core launch. |
| **KYC (BVN/NIN/vNIN)** | ❌ **endpoints don't exist** | Wema has no standalone identity lookup — see KYC rail below. |

**Funding-correctness fix (landed):** `normalize_transaction` now treats an inbound
`creditType=='Credit'` row as fundable only when its `status` is settled. The ALAT
`TransactionStatus` enum is `{Default, Successfull(sic), Failed, Pending}`; a **Pending**
(in-flight) or **Failed** (bounced) credit row is skipped, so a deposit is never credited
before it settles. Unknown/blank still counts (a live gateway that omits the field can't
strand real money); a Pending row credits on a later sweep once it settles.

### `securityInfo` — the crux (still needs Wema)

The specs reveal the **shape** but not the **algorithm**. `EncryptionCredentials`
`{encryptionPassword, encryptionIV, encryptionSalt, encryptionIdentifier}` (all `readOnly`,
wrapped in the standard result envelope) is **issued to the partner** — no endpoint in any of
the 19 specs returns or accepts it, so credentials are provisioned out-of-band. The quartet
(password + **salt** + IV + identifier) is the classic **AES-CBC + PBKDF2** signature
(salt ⇒ derived, not pre-shared key; IV ⇒ chaining mode; identifier ⇒ which credential set
Wema decrypts with). Confidence ~MEDIUM on that shape, LOW on the runnable parameters.

`securityInfo` is carried by: `ProcessClientTransfer`, `FundWallet`, `PurchaseAirtime`/`Data`
(Client **and** pool — **required, minLength 1** on the pool variants), `PayBill`
(+ pool), `ProcessRemitaPayment`. It is **absent** from account-creation, balance/history,
name-enquiry, **cards, and KYC** — so those rails are fully buildable without it.

**Send Wema exactly these questions to close it:**
1. How are our production `EncryptionCredentials` (password/IV/salt/identifier) issued? (They appear in no endpoint.)
2. What plaintext is encrypted into `securityInfo` — a fixed credential string, or a per-transaction canonical string? If the latter, which fields and in what order (e.g. `reference|amount|sourceAccount|timestamp`)?
3. Is `securityInfo` static-per-channel (cacheable) or per-transaction?
4. Cipher: AES-CBC or AES-GCM? Key size (128/192/256)? Padding (PKCS7)? If GCM, where/how long is the auth tag?
5. KDF: PBKDF2-HMAC over password+salt? Which hash (SHA1/SHA256), how many iterations, what derived key length? Or is `encryptionPassword` already the raw key?
6. Encodings: are IV/salt/password delivered Base64/hex/raw-UTF-8, and is the `securityInfo` output Base64 or hex?
7. Is `encryptionIdentifier` sent in the request, or inferred by Wema from our subscription key / channel id?
8. Please provide **one fully worked example** (sample plaintext + credential set → resulting `securityInfo`) so we can match it byte-for-byte.
9. A C# reference snippet (the specs are .NET) would let us match iterations/padding exactly.

`_security_info()` is a fail-loud stub: it returns a static `WEMA_SECURITY_INFO` if set, else
`""` (which makes a live money call fail at the gateway rather than send an unsigned payload).
Slot the real construction into that one function; keep the static-value fast path; add a unit
test reproducing Wema's worked example before flipping `wema_live()`.

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

- **Pay with Bank Account (ALAT Authenticator)** — `pwba_fund_request` / `pwba_status`
  (`/pwba-authenticator`, no securityInfo, channel id in the body). A direct-debit **funding**
  rail: `POST /api/wallet/alat/fund/` starts a debit from the user's OWN ALAT account (they
  approve it in the ALAT app) and `POST /api/wallet/alat/fund/verify/` polls it and credits the
  wallet exactly once (FundingIntent + `settle_funding`).
- **Get Statement** — `POST /api/wallet/statement/` returns the user's Wema NUBAN bank statement
  (ALAT transhistoryV2, normalised) for a date range; distinct from the Zitch ledger history.

Still unwired: **Direct Debit / Scheduled Payments** and any recurring-mandate flow — the bundle
carries recurring *schemas* (SaveRecurringRequest/RecurringBillsResponse in bills-payment) but no
recurring *endpoints*, so there's nothing to call yet. Confirm the endpoints with Wema.

## ⚠️ Open decisions — confirm with Wema before go-live

1. **Money-flow model — RESOLVED: per-user balances.** Each user's NUBAN holds its own
   balance. `payout_send` debits the **sender's own NUBAN** (`execute_payout` passes the
   sender's `wallet.account_number` as `source_account`); the shared `WEMA_SOURCE_ACCOUNT`
   pool is only a fallback for a sender who has no Wema NUBAN yet. A live payout with neither
   fails closed (refundable). To pay out via Wema a user must have a Wema NUBAN with balance,
   or `WEMA_SOURCE_ACCOUNT` must be funded to cover pool-sourced payouts.
2. **`securityInfo` construction.** The encryption scheme (algorithm / what is signed / key
   material) is NOT in the OpenAPI. Implement in `utility.wema._security_info` once Wema
   supplies it; it carries on the transfer / credit_wallet / VAS / bills money-movement calls.
   Sandbox does not enforce it.
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

## No-webhook reconciliation

ALAT exposes no webhooks, so `python manage.py reconcile_wema` (render cron, every 10 min):
- **Funding:** sweeps each Wema-provisioned wallet's history and credits inbound deposits,
  idempotent on Wema's `referenceId` stored as `WEMA-CR-<referenceId>` (namespaced so it can
  never collide with a `ZTRF`/`ZPAY`/`ZFND` ledger reference).
- **Payouts:** settles/reverses PENDING bank payouts by polling `confirm_transfer_status`
  (only when `payout_provider() == "wema"`, which is the default).
