# Removing 9PSB

Decision: remove the 9PSB integration entirely. No replacement rail is being built
in this work; Zitch returns to "no bank partner configured" until one is.

Authorised on 2026-11-08. Only the owner's own test account is on the rail, so the
~₦150k the bank holds, the pending ₦200 airtime row and the open Tier 2 upgrade
(`ZU8FAD092A22138B39E212`) are accepted as stranded.

## What is actually there

| | |
|---|---|
| `backend/banking` | 56 files — 4,572 lines production, 5,121 lines tests |
| 9 migrations | `BankAccount`, `Instruction`, `Notification`, `AccountUpgrade`, `IdentityChallenge`, `AddressVerification`, `BillerValidation`, `PreviousBankArchive` |
| 11 management commands | sandbox tooling, reconciliation, directory sync, maintenance |
| Files mentioning 9PSB | 147 across backend, app, lib, components and both Render blueprints |
| Non-banking importers | `wallet`, `transfers`, `utility`, `whatsapp`, `cards`, `accounts`, `compliance`, `admin_api`, `zitch_api` — ~60 import lines over 17 banking modules |

`banking` is not a provider-agnostic layer with 9PSB behind it. It *is* 9PSB. So the
boundary the other apps call has to be rewritten, not merely re-pointed.

## Why staged

One pass would take most of the product down with it and leave nothing reviewable.
Each stage below lands green and committed on its own.

### Stage 1 — sever the wire
Delete the protocol, the credentials and the operator tooling, so no 9PSB call can be
made and no credential exists:

- the HTTP client, auth and token cache; WaaS and VAS wire layers; the UAT gate
- inbound webhooks and their URL routes; the bank notification ingest
- every `bank_*` / `reconcile_9psb` / `sync_bank_directory` management command
- the `NINEPSB` settings block and every `NINEPSB_*` variable in both Render
  blueprints and `.env.example`
- the tests covering exactly those layers

The platform already degrades correctly with no bank: `account_for` returns `None`,
`account_payload` reports `banking_unavailable`, `customer_enabled()` is false, and
`spending_account` refuses every debit. That existing path is the target state, which
is why this stage is safe to do first.

### Stage 2 — the app surfaces
The 10 `app/`, 6 `lib/` and 2 `components/` files carrying funding, NUBAN display,
bank transfers and the KYC upgrade flows.

### Stage 3 — models and tables
Drop the banking tables with the same care `wallet/0031_drop_retired_account_schema`
took: refuse to run on non-empty data, keep the immutability triggers, and preserve
`PreviousBankArchive` / `RetiredFundingArchive` / `UnsettledFunding` so the Wema-era
accounting evidence survives.

### Stage 4 — call sites and the package
Unpick the ~60 import lines, delete `backend/banking`, delete its 5,121 lines of tests.

## Not in scope

- Building a Wema integration. Wema was deleted before this work: no app directory,
  no client, no settings block, and `wallet/0031` dropped its tables. A new partner is
  a fresh build, not a flag.
- Moving money out of 9PSB. Explicitly accepted as stranded (see above).
