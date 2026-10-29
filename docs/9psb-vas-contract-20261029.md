# 9PSB VAS integration review - 29 October 2026

This change completes the supplied VAS request shapes and receipt handling in
the shared backend, mobile checkout and WhatsApp checkout. It remains part of
draft PR #561. It is not deployed or accepted for live customer purchases.

## Authentication: observed request, not an inferred endpoint

At **10:50:44 WAT on 29 October 2026**, a fresh VAS-only request was traced from
`Client("vas")` using the separately supplied VAS credentials. Credential values
and bearer tokens were not logged.

| Field | Observed value |
| --- | --- |
| Method | POST |
| URL | `http://102.216.128.75:9090/identity/api/v1/authenticate` |
| Body keys | `username`, `password` |
| Credential set | VAS |
| HTTP status | 400 |
| Provider response code | 400 |
| Provider message | Bad credentials |
| Match to attached Postman authentication URL | Exact |

The VAS authentication mapping was already the identity endpoint; this change
adds a guard against accidental cross-product calls. A WaaS client now refuses
VAS operations before it authenticates, and a VAS client refuses WaaS operations.
WaaS separately uses `/waas/api/v1/authenticate`; its successful authentication
does not establish VAS access. Token and catalog caches also include environment.

A later bank reply disputes this URL. The response alone does not prove that the
credentials/profile are wrong. Both newly supplied attachments still name the
same URL; obtain the exact VAS identity URL before another authenticated request.
See [the routing correction](9psb-vas-auth-routing.md). No test-environment IP whitelist is required according
to the bank's reply. No test purchase, real identity submission or activation
switch was performed in this review.

## Implemented contract and customer behavior

- All 12 documented authentication, network, data-plan, top-up, catalog, biller
  validation, purchase and status routes have explicit transport tests.
- Biller `validation` and `isSelectData` flags determine requested inputs.
  No-plan billers omit `itemId`; optional names are collected only when required.
  Unpriced PREPAID/POSTPAID selections take the customer's amount. Fixed prices
  remain bound to the bank catalog and are rechecked before a debit.
- Customer validation is bound to user, environment, customer, biller, item,
  amount and expiry. Migration 0007 leaves older unknown-environment validation
  records unusable. Client-supplied names or debit accounts cannot replace the
  validated customer or account owned by the wallet.
- The Postman example uses response code 300 for a duplicate slip. That response
  and an inconclusive 404 now remain pending; neither causes an automatic refund
  or another purchase. Status queries use the persisted 18-character reference.
- Conflicting echoed references, recipients, amounts, source accounts, networks
  or selected products cannot settle an instruction.
- Electricity tokens and units are retained for successful immediate and delayed
  outcomes. Units are parsed from the documented `otherField`/`description`
  labels; arbitrary provider text is not exposed. A pending payment never exposes
  a token as delivered value.
- Mobile checkout opens the specific payment's status with its durable retry
  identity. Confirmed settlement clears that attempt. A failed wallet refresh
  cannot replace a confirmed receipt with an interrupted-payment message.
- WhatsApp supports `receipt <reference>` for confirmed VAS receipts, including
  recovered electricity tokens. It requires the active owner's linked channel,
  the existing idle reauthentication check, the current environment and a
  confirmed ledger/instruction outcome. It makes no new bank purchase request.

## Validation

- Shared banking/account/transfer/archive/receipt/Render run: **326 tests, 321
  passed and five PostgreSQL-only skips on SQLite**. The final VAS-only run
  passed **all 67 checks**, including the cross-product guard and isolated
  catalog fixtures.
- Full mobile suite: **68 suites, 724 tests and one snapshot passed**. The two
  touched screens also passed their focused checks after hook cleanup.
- TypeScript, lint on touched frontend files, Django system checks and migration
  consistency passed. No test-discovery exclusion was added.
- Wider SQLite backend run: **3,211 tests, 487 failures, 561 errors, 12 skips**.
  The earlier migration already had failing full backend CI. Failures span
  several shared apps and retired-provider assumptions; they have not all been
  established as obsolete. These focused passes do not replace that release gate.

All local payment and identity responses are synthetic. They are not evidence of
bank settlement, delivered airtime/data, meter acceptance or live WhatsApp delivery.

The newer PR head `087249e` was incorporated before the final focused checks.
Its missing-token recovery state and migration 0008 are preserved: a delivered
bill can recover its token without reopening or refunding the payment.

## Deployment and remaining external acceptance

Render still serves main commit `30a365b3c248b0831e79f197541939b6181fb5c4` on
`zitch-api-ry6y`; draft PR #561 is not deployed. The new callback receiver cannot
be represented to the bank as live until deployed and checked. Preserve the
documented maintenance cutover order and customer activation gates.

Before release: resolve the full backend regressions; obtain working VAS
authentication; complete bank-approved, funded sandbox purchase/status/receipt
tests; verify the deployed Basic Auth callback and bank delivery; and complete
the wider WaaS acceptance gates recorded in `9psb-uat-evidence.md`. Production
credentials and acceptance remain separate from sandbox access.

## Supplied source identity

Private attachments are not committed. SHA-256 hashes identify the reviewed copies:

| Attachment | SHA-256 |
| --- | --- |
| VAS API DOCUMENTATION (updated) (1)(2).pdf | `aeda07f7d42b35c9670477fee340cf091700f2ed61f1e64ce9abf5b07f5c35b1` |
| VAS API COLLECTION EXTERNAL.postman_collection(4).json | `b2f02f0b74e47f7c9e2a60ba26110e7b574b445db337b26c2fa1e47b3b1fcadc` |
