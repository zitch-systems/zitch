# Zitch — Wema Partnership and retired-9PSB integration audit

**Review date:** 9 November 2026 (Africa/Lagos)
**Scope:** Django banking, Wema callbacks/ledger, Android/Expo, WhatsApp, GitHub Actions, Frankfurt Render.
**Source head reviewed:** `a0bdbe4f43f7c0ade48cec5708f5bf6559ab9c50`.
**Running API/WhatsApp worker release observed:** `2623dfc82709155aa5d405c593ed0d45270ad5ef`.
**Audit fixes:** branch `audit/wema-partnership-20261109` (PR linked when created).

## Executive conclusion

**Code-level restoration of Wema Partnership is substantially complete. Full banking production readiness is NOT established.**

Wema Partnership is the active *source-selected* account adapter. The 9PSB
account application, authentication client and customer API routes are absent
from the active source tree. Search of current GitHub source found no embedded
9PSB identity host `102.216.128.75`, WaaS integration or old
`/identity/api/v1/authenticate` call. No app/mobile 9PSB provider integration
was found. These are source checks, not a complete search of secrets or records
held in the production database, bank, or external systems.

The Wema service has **not** been certified by real-bank acceptance. On 9
November the settlement command explicitly logged
`Wema not live (simulation/mock)`, and did not have bank-backed balances
against which to reconcile customer liabilities. A code deploy cannot make
Wema live without verified credentials, the explicit activation gate, provider
entitlements and bank-side account evidence.

## Source and deployment observations

| Control | Observation | Decision |
|---|---|---|
| Wema adapter | `utility.providers.bank_account_provider()` selects `partnership`; Wema API module, callbacks, reconciliation and NUBAN code present | Source-implemented, no live bank UAT |
| Retired 9PSB endpoints | No `backend/banking` app in current tree or routing; no older provider host/auth source found | No active 9PSB adapter found |
| Historical financial records | Forward migration and read-only audits preserve `banking_bankaccount`, `banking_previousbankarchive`, old instructions and notifications | **Must retain until independent reconciliation** |
| Mobile | Main CI's Expo test suite succeeds | Tests alone do not validate a signed physical device |
| WhatsApp | Meta token/WABA reads succeed; Partnership Flow `1601841594748416` is published with 24 screens and no validation errors or routing gaps | Channel ready at metadata level, not transaction-proven |
| Meta webhook | WABA reports Zitch Banking subscribed, but subscribed-field metadata was not supplied | Inbound delivery and endpoint decryption need observed UAT |
| Frankfurt API and worker | Both live on the 9 November release; API `/readyz` returns 200 | Infrastructure healthy; bank transactions not certified |
| Reconciliation cron | Several latest 10-minute runs exited successfully, each with 0 accounts and 0 credits/transfers processed | **Zero work does not prove provider success** |
| Settlement report | 9 November 06:16 UTC run failed closed: historical funding unverified; `Wema not live (simulation/mock)` | Financial go-live blocked |
| GitHub CI | Main `42633762449` passed Django, PostgreSQL, legacy-to-Wema migration, Expo and Meta suites | Source regression evidence |
| CodeQL | Main `42633762591` ran analysis but failed uploading SARIF because GitHub reports code scanning is disabled for this repository | Enable code scanning; not a confirmed code vulnerability |

The deployed API and worker track `main`, but the last deployed revision
lagged the current main commit during review because checks/deployment had
not promoted the newest revision. Verify actual deployed SHA each release.

## Current financial exposure (aggregate-only)

The daily 9 November settlement log reported: ledger liability **₦153,076.00**;
stored wallet balances **₦152,976.00**; archived liability **₦100.00** included
in the larger amount but excluded from spendable wallet balances. It reported
one historical funding account requiring bank confirmation and no verified
historical bank balance. These are reported accounting positions, **not**
evidence of missing funds or a reconciled surplus/shortfall. Do not adjust
figures merely to make the reconciliation report green.

The live database blocks external IP connections, preventing the connected
read-only Render database tool from independently checking customer records.
Do not weaken the IP allowlist to accommodate this tool. A secure internal
maintenance run or bank-authorized read-only path is required.

## 9PSB reference inventory and retirement disposition

1. **Active integration code:** not found in the current backend routes,
   mobile source or WhatsApp provider adapter. Permanent regression tests
   assert that it stays absent.
2. **Recipient bank picker:** the separate external bank named `9PSB` was
   still being seeded as active. This was not an account-partner API, but it
   conflicts with the explicit instruction to remove customer-facing 9PSB.
   The audit branch marks it inactive on seed and excludes it from new
   app/WhatsApp bank selection, name enquiry and payout. Existing beneficiary
   and transaction records are left intact.
3. **Migration and archival tooling:** `accounts/0034`–`0036`,
   `wallet/partnership.py`, `wema_cutover_audit` and
   `export_previous_bank_accounts` intentionally mention the old bank. They
   MUST NOT be deleted while records or liabilities remain unresolved. They
   cannot initiate an old-bank API call.
4. **Historical Git history and documents:** retained for rollback, evidence
   and auditability. Removal from active code does not erase independent
   external bank-side accounts, existing balances or historic NUBANs.

## New code changes in this audit

- WhatsApp Add Money now refuses to display empty or stale deposit
  instructions whenever Wema Partnership is unavailable, under old-provider
  review or has no usable funding NUBAN.
- App and WhatsApp exclude 9PSB from **new** recipient-bank lists and bank
  name/PIN-resolution paths even when a stale database row is marked active.
  The next `seed_plans` run marks its existing bank row inactive.
- Tests exercise app transfer/picker rejection, WhatsApp picker rejection,
  customer funding messaging and absence of active 9PSB adapter/URLs/client
  references.
- No production customer data, bank account, balance, transaction, secret,
  Wema activation flag or external bank mapping has been changed.

## Required release gates and Wema UAT

1. **Account ownership and unresolved funding.** Export old account-to-wallet
   mappings into a private, access-controlled destination *outside the
   repository*; obtain statements for retired and Wema accounts, bank-confirm
   ownership and unresolved receipts, and reconcile to the immutable Zitch
   ledger before reattaching or changing any customer NUBAN.
2. **Wema credentials and entitlements.** In Render's secure Environment pages,
   independently confirm the approved production base URL, channel ID, wallet,
   account-creation, upgrade, and product keys plus bank-registered callback.
   Never send keys through chat or commit them. The existence of an env variable
   cannot prove the bank has enabled the product.
3. **Activation gate.** Keep `WEMA_CUTOVER_APPROVED=false` and real-money
   instructions blocked until (1) and (2), financial preflight and signed bank
   UAT pass. `WEMA_SIMULATION` must be off for live trials only with approved
   controlled test accounts and a completed rollback/incident plan.
4. **Bank-end acceptance.** Authorized test customer: BVN/NIN challenge,
   accepted OTP/face result bound to the session, issued or recovered NUBAN,
   status/PND confirmation, external inflow and bank/ledger requery, transfer
   with duplicate/delayed response, refund and status recovery, and
   airtime/data/bill success/failure/refund with provider-confirmed reference.
5. **Higher-tier KYC.** Full Tier 2 still needs a server-verified interactive
   Prembly session with liveness/identity binding; Tier 3 must use
   request-bound Wema address verification completion. Do not fake a
   provider-success result from a photo, account-level status, or client
   callback alone.
6. **WhatsApp and Android.** UAT direct app versus WhatsApp behavior on the
   same consenting user; verify published Flow, webhook signature,
   idempotency, PIN, late callbacks, state parity and real push-delivery on
   a signed device.
7. **Release controls.** All Django/PostgreSQL/mobile/Meta tests and
   source/URL retirement guard must pass. Enable GitHub code scanning to
   accept SARIF output, verify the deployed commit SHA and review fresh
   Render reconciliation and settlement reports. Preserve incident rollback
   and signed financial release approval.

**Release decision: Wema-source audit improvements may be tested and reviewed;
full live money movement is NOT authorized by this report.** The current
bank-state facts prevent certifying that Wema is "working perfectly."
