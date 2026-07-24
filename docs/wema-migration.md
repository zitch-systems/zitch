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
deterministic fake card; fails closed in prod when unkeyed. **FIX-BEFORE-LIVE:** the current
`/api/VirtualCard/*` endpoints are wrong — see *Spec reconciliation → Card rail* for the real
`/card-management` contract (NUBAN-keyed, `x-api-key`, no `securityInfo`, block-only freeze).

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

### Card rail — FIX before enabling cards (see code header in `wema.py`)

The current `/api/VirtualCard/*` paths are fabricated. The real **Card Management API**:
- APIM suffix **`/card-management`** (not `/virtual-card`); auth header **`x-api-key`** (not `access`); **no `securityInfo`**.
- Keyed by the customer **NUBAN (`accountNo`)**, not a card token:
  - issue → `POST /api/Partner/partnerCard/virtualCard` (`{accountNo, emailaddress, phoneNumber, amount, customerAddress, cardKey, currency:'NGN'}`)
  - reveal → `GET /api/Partner/partnerCard/retrieveCard/{accountNo}` (full PAN/CVV) or `.../virtual-card-details/{accountNo}`
  - freeze → `POST /api/Partner/partnerCard/hotlistCard?maskedPan=&accountNumber=` (**block-only — no unfreeze endpoint exists**)
  - fund → none; funding is only the optional `amount` at creation
- Migration needs the cardholder's NUBAN/email/phone threaded into these calls (a card-model
  change) + a live-key smoke test, so it is intentionally deferred, not done blind.

### KYC rail — FIX (no standalone lookup on Wema)

Wema exposes **no** BVN/NIN lookup endpoint — the `/api/Kyc/Verify*` paths are fabricated and
will 404, and there is **no vNIN endpoint anywhere** on the Wema rail. BVN/NIN are validated
only as a byproduct of account creation (`tier1-bvn/nin-withoutOtp-v2`) or upgrade
(`partner-account-upgrade-tier2`); current tier reads from
`GET /api/partnership/partner-account-kyc-status`. None returns a holder name for a bare-lookup
name-match. **Resolution (product decision, needs live keys):** either (a) route BVN/NIN/vNIN
identity lookups to **Prembly/IdentityPass** (which do expose them), or (b) fold BVN/NIN
validation into the account-creation/upgrade flow. `accounts/views.py` calls
`verify_bvn`/`verify_nin` today, so this must be resolved before KYC-gated tiers work live.

### Transaction-status legends — still needed from Wema

- **Transfer/credit:** `result.status` / `result.data.status` are plain strings; the spec
  examples are all `"string"`. `reconcile_wema` matches SUCCESS/FAILED families defensively.
- **VAS airtime/data requery:** `CheckTransactionStatus` → `result.transactionStatus` is an
  **integer enum (1–11)**; `transactionType` on the request is an **int enum {1,2}** (not the
  string `'airtime'`/`'data'`). Requery cannot interpret the code until Wema supplies the map.
- **Bills requery:** `checktransactionstatus` → `result.transactionStatus` **integer enum (1–9)**.
- **History:** `TransactionStatus {Default, Successfull, Failed, Pending}` — now honored.

### New portal products (available, NOT launch-blocking)

`Remita Payment`, `Pay with Bank Account (ALAT Authenticator)`, `Buy-Now-Pay-Later`,
`Direct Debit / Scheduled Payments`, and `Get Statement` are provisioned in the portal but not
wired. They are new capabilities (Remita maps to the `remita.tsx` stub; BNPL to `bnpl.tsx`),
each a product decision. BNPL uses a different auth scheme (`x-merchant-id` +
`x-merchant-authorization-key`). Wire post-launch as features, not go-live blockers.

## ⚠️ Open decisions — confirm with Wema before go-live

1. **Money-flow model — RESOLVED: per-user balances.** Each user's NUBAN holds its own
   balance. `payout_send` debits the **sender's own NUBAN** (`execute_payout` passes the
   sender's `wallet.account_number` as `source_account`); the shared `WEMA_SOURCE_ACCOUNT`
   pool is only a fallback for a sender who has no Wema NUBAN yet. A live payout with neither
   fails closed (refundable). To pay out via Wema a user must have a Wema NUBAN with balance,
   or `WEMA_SOURCE_ACCOUNT` must be funded to cover pool-sourced payouts.
2. **`securityInfo` construction.** Not in the OpenAPI — see *Spec reconciliation →
   `securityInfo`* above for the confirmed shape (AES-CBC + PBKDF2 signature), the full list of
   endpoints that carry it, and the exact 9-question list to send Wema. Implement in
   `utility.wema._security_info` once Wema answers. Sandbox does not enforce it.
3. **Transaction-status legends.** See *Spec reconciliation → status legends* above. The
   transfer status is an undocumented string; the **VAS/bills requery status is an integer
   enum** (airtime/data 1–11, bills 1–9) that the code cannot yet interpret. History status is
   now honored. Confirm the code→meaning maps with Wema.
4. **Wallet-creation OTP response shape.** Create endpoints return `ResponseModel` (no tracking
   id per the spec); the live envelope carries it at `data.otpTrackingID`, which
   `create_wallet_request` already hunts for. `ResendOtp` is a **200 No-Content** endpoint —
   now handled (a bare `.json()` on the empty body used to raise on a genuine success).
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
