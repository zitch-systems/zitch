# Wema Partnership restoration — 8 November 2026

**Status: staging candidate only — DO NOT DEPLOY OR MERGE.**

Requested target: remove 9PSB from *active* Zitch Android, Django API,
WhatsApp, KYC, transfers and bill-payment paths and restore **Wema
Partnership**. Do **not** reintroduce the separately retired Wema
virtual-account (VAS) integration. The Wema airtime/data/biller product
is distinct from Wema VAS virtual-account enrollment.

## Source and recovery

- Prior Wema Partnership source: archive branch
  `archive/wema-partnership-before-9psb-20261028`, commit
  `30a365b3c248b0831e79f197541939b6181fb5c4`.
- Current 9PSB-era snapshot: `archive/9psb-before-wema-20261108`,
  commit `9c5127696bc78fe454164df05870720017bafa90`.
- This review branch was created from current `main`, then restored to the
  archived Wema Partnership source tree. It is a code recovery **candidate**;
  this action does not migrate database contents, update Render, publish a
  WhatsApp Flow, distribute an APK, or activate a Wema product.

## Hard go-live blockers

1. **Forward database compatibility.** Current deployment has applied newer
   9PSB-era Django migrations including `banking.0001-0009` and
   `accounts.0034-0035`. This restored tree predates them. Produce tested
   *forward-only* compatibility migrations and run `migrate --check` and
   the full database test suite on a scrubbed PostgreSQL clone of the
   current production schema. Do not roll the DB back to October.
2. **Financial integrity and provider ownership.** Inventory live 9PSB
   bank accounts, unresolved transfers, holds, opening balances, provider
   receipts and inbound credits. Keep all original bank references and
   immutable transaction/audit history. No record deletion or arbitrary
   provider relabeling. Bank-side closure needs bank confirmation and
   outstanding liabilities require reconciliation.
3. **Wema account restoration.** Inspect historical Partnership account
   attachments and archive references. For every previously issued Wema
   NUBAN call the authorized Wema Partnership account-details/recovery
   service to prove ownership; do not simply copy account numbers,
   issue duplicate accounts, or unlock held money. Confirm the exact
   approved production Partnership base URL, product credentials and
   callbacks with Wema.
4. **Route safety.** Restore only Partnership wallet/account creation,
   funding, outbound debit-wallet payouts, Transaction History V2,
   authorized notifications, tier upgrades, and bank-backed bill payments.
   Keep VAS virtual-account enrollment retired. Ensure both WhatsApp
   and mobile share the same KYC and account provisioning state.
5. **Channel and configuration UAT.** Check `WEMA_BASE_URL`,
   `WEMA_CHANNEL_ID`, `WEMA_WALLET_KEY`,
   `WEMA_ACCOUNT_CREATION_KEY`, `WEMA_UPGRADE_BASE_URL` and
   `WEMA_UPGRADE_KEY` as appropriate in the Render environment (not in
   Git or chat), with bank-provided production values. Validate Wema
   callback authenticity, retry/idempotency, SMS/face verification,
   account number, historical funding, payouts and status recovery.
6. **Deployment sequencing.** API owns migrations; worker/cron only
   start after a successful DB gate. Existing Render services track
   `main` and auto-deploy after checks; do not merge until a controlled
   cutover window with rollback plan. Update the Wema reconciliation job,
   maintenance commands, Android build and published WhatsApp Flow only
   after UAT. Retain a safe route to investigate late 9PSB callbacks.

## Acceptance evidence required

- PostgreSQL migration, ledger immutability and all backend regressions pass.
- Android/TypeScript and WhatsApp flow contract suites pass.
- Owned non-production Wema Partnership KYC, account opening, inbound credit,
  outbound transfer, bill purchase and settlement tests are documented.
- Financial opening balances and unresolved items are reviewed and signed
  off, with no customer funds lost, doubled or silently relabeled.
- All active 9PSB client calls, config references, displayed provider names,
  schedules, and third-party Flow dependencies are absent **after**
  migration; historical evidence remains retainable.
- Render deployment and app/WhatsApp cutover observed healthy end-to-end.

This file is intended as an explicit safeguard against accidentally using the
restored archive as an already validated production release.
