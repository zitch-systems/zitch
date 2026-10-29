# 9PSB integration and previous partnership retirement

**Draft implementation; not a production-ready or fully integrated release.**
Customer banking, VAS and sandbox enrollment remain disabled. Do not merge or
deploy this branch until the release gates below are satisfied.

## Archive

The previous implementation is preserved in Git branch
`archive/wema-partnership-before-9psb-20261028` at
`30a365b3c248b0831e79f197541939b6181fb5c4`.
New account and bill instructions were disabled on all nine Django services on
28 October 2026. Historical transactions and outstanding liabilities are evidence,
not funds that can be relabelled as a new provider's balance.

## Source contracts

Implementation uses the supplied WaaS API v3.1 (22 July 2026), VAS API PDF,
Postman collections, and the signed-service scope supplied by the project owner.
The VAS contract review and implementation follow-up on 29 October is recorded
in [9psb-vas-contract.md](9psb-vas-contract.md), including source hashes, all
documented routes, dynamic fields, duplicate handling and receipt recovery.
The older December 2024 collection does not define the newer identity handshake;
the July PDF takes precedence. Agreements, credentials and identity data are not
committed to this public repository.

## Delivery controls

- Separate explicit VAS API/identity URLs, credentials and cached tokens from
  WaaS; bounded network timeouts. The environment example uses the supplied
  VAS sandbox collection URLs; see [routing review](9psb-vas-auth-routing.md).
- Persist transaction references before money instructions. Never replay an
  uncertain write or refund merely because a request timed out.
- Verify inflow notifications with the bank before ledger credit; acknowledge
  duplicate authenticated deliveries without crediting twice.
- Bind BVN/NIN verification to a customer, identifier hash and expiring reference.
  Accept the bank's identity result, not a client assertion or a generic HTTP 200.
- 9PSB performs identity and face verification. Prembly is limited to Tier 3
  address verification, whose evidence must be included in the bank upgrade.
- Bank-reported PND, tier and limits are authoritative. An upgrade request is not
  an approved upgrade. Keep pending status until the bank confirms it.
- Keep sandbox funds and identities isolated from production. HTTP sandbox
  endpoints cannot be used for real customer data. Production requires HTTPS,
  production credentials and the bank's account activation/UAT confirmation.

## Confirmations still required from the bank

Tier 1 single deposit is NGN 50,000 and maximum balance NGN 300,000.
Tier 2 maximum balance is NGN 500,000; the correspondence says `N200,00` for
single deposit and needs correction. Tier 3 says `5M` without specifying single,
daily, inbound or outbound. Daily limits, minimum balance and single outbound
limits were not supplied. Do not manufacture values for these fields.

The agreements also mention virtual accounts and card acquiring. Supplied
technical documents cover WaaS, identity, QR and VAS; a host name alone is not
an API contract for the additional products. Keep undocumented products closed
until their request, authentication, callback and reconciliation contracts exist.

## Render

The API and worker use the smallest paid compute plan, PostgreSQL the smallest
paid database plan, and the read-only Meta connector a free plan. API memory
observed across 21–28 October was approximately 103–185 MiB of 512 MiB.
Do not move the banking API onto sleeping compute or discard database backups.
Consolidate scheduled maintenance only with durable execution tracking, isolated
failures, bounded batches and alerts. Preserve the existing financial review
findings rather than suppressing them to make health reports appear green.

Live resources have different names from the old Blueprint. Blueprint auto-sync
must not create duplicate resources during this migration.

The exact resource adoption and retirement order is in
[render-maintenance-cutover.md](render-maintenance-cutover.md). No cron savings
have yet been realized.

## Release evidence and remaining work

| Area | Current state | Required before production |
|---|---|---|
| WaaS authentication and directory | Sandbox authentication and bank listing succeeded | Production HTTPS credentials, UAT/ICAD approval |
| Identity and wallet opening | Bound OTP/facial handshake, recovery and upgrade code implemented; mocked contract tests | Approved synthetic BVN/NIN/NIN-user-ID fixtures and account-opening UAT |
| Payments | Durable once-only dispatch, fees bound to confirmation, uncertain outcomes retained | Confirm total customer debit including fees; execute funded-wallet UAT |
| Status | Separate WaaS and VAS parsing; WaaS success sample verified against supplied Postman collection | Negative/reversal status examples and live test evidence |
| Inflows | Authenticated durable inbox, bank requery before credit, duplicate protection | Owned test wallet; statement/history schema, outage backfill and reversal correlation |
| VAS | Dynamic catalog, validation, purchase and status code implemented | The documented identity URL, separate VAS credential pair and noauth login were verified in a fresh 13:20 WAT request on 29 October; authentication still returned HTTP 400 / Bad credentials |
| Prembly Tier 3 | Correlated request/status adapter prepared; new paid requests disabled | Terminal approval enum and retrievable report contract, then tests and activation |
| App and WhatsApp | Shared app checkout, dynamic WhatsApp VAS flow, transfer fee review and secure identity forms implemented | Live channel UAT and broader backend regression migration |
| QR and corporate WaaS | Documented by bank | Product implementation and acceptance tests remain |
| Virtual accounts and card acquiring | Commercial scope only | Technical API/callback/reconciliation contracts are missing |
| Render | Blueprint validated; bounded durable scheduler and idle polling implemented | Authenticated resource cutover and full daily-cycle evidence |

Local validation at this checkpoint: 89 banking and Render checks (87 passed;
two PostgreSQL trigger checks skipped on SQLite), all 68 app suites / 706 tests and one snapshot
passed, and TypeScript checking passed. Banking tests cover archive immutability,
fees, server-owned bill prices, renewed-validation replay, identity ownership,
web PIN/CSRF binding, WhatsApp authorization replay and ambiguous bank routing.
The `c161ee1` checkpoint also passed migration consistency and all 78 banking
tests on PostgreSQL, including both immutability trigger checks. Its app CI run
had one cold native-component initialization timeout in an existing savings
test (705 passed). That native test boundary was isolated and all 15 tests in
that suite passed without a transform cache; production behavior is unchanged.
The subsequent `d349423` app CI passed, and its PostgreSQL banking checks passed.
Both Render files now describe the same proposed existing-resource cutover.
Maintenance holds no Meta credentials; the worker retries receipts but leaves
bank/account sweeps to the cron. Static checks enforce shared credentials,
single migration ownership, checks-before-deploy and all seven retained controls.

The obsolete per-screen bill and old-provider identity tests were replaced with
shared checkout, bank identity and route-binding coverage. Their original
implementation and tests remain in the archive branch. The active mobile source
contains no retired-provider API wrappers or hosted face screens. Prembly is no
longer advertised for Tier 2.

The first draft CI run at `bfd2160` passed migration consistency and all 47 then
present banking tests on PostgreSQL. Its complete PostgreSQL regression run
executed 3,508 tests with **667 failures and 1,045 errors**. Many use retired
provider contracts; this does not establish that every failure is obsolete.
The full backend regression suite remains an unresolved release blocker and has
not been disabled or removed. New PostgreSQL checks run separately before that
suite in CI. Focused green tests are not evidence of production readiness.

Keep all provider agreements, credentials and identity attachments out of this
public Git repository. Sandbox keys were installed only in server environment
configuration, with every new-customer activation switch left off.

## Workspace recovery and archive safety follow-up

Workspace access was restored on 28 October. The two credential files actually
present are byte-identical to the earlier set. Rechecking these files returned
WaaS authentication HTTP 200 and a successful bank directory, but VAS
authentication HTTP 400 / bad credentials. The separately referenced replacement
attachment was not present at that checkpoint. It was retrieved on 29 October
and found identical; the fresh VAS request still failed with HTTP 400. No activation
or Render cost cutover was performed in this follow-up.

Historical card refunds now create immutable archive adjustments in the same
transaction as the refund. Operator restoration of a retired payout cannot use
new-bank funds and reduces the historical hold without double-posting the ledger.
Bank accounts and identity challenges have immutable environment bindings;
background queues and direct recovery paths reject records from another
environment before any network request or money transition. Migration 0006 leaves
pre-existing challenge origins blank and blocked rather than guessing their
origin. New challenges record their environment at creation.

The combined banking, Render and shared-control regression run completed 222
checks: 218 passed and four PostgreSQL-only checks were skipped locally.
Coverage includes refund replay, rollback, insufficient historical funds and
environment isolation across account, identity, payment and notification recovery. Updated email,
device-risk, receipt and operator fixtures exercise the current bank evidence
without opening retired provider routes. These focused results do not replace the
full regression run: the then-current full PostgreSQL checkpoint at `3d46c79`
had 680 failures and 1,046 errors across 3,541 tests; SQLite had 680 failures,
1,042 errors and 10 skips. The complete suite remains enabled and unresolved.

## Executed sandbox checks

The follow-up UAT run confirmed WaaS authentication, directory, the supplied test
wallet's NUBAN and its bank-bound name enquiry. It uncovered and fixed internal
wallet-number/NUBAN confusion and trailing newlines in bank routing codes. VAS
returned HTTP 400 / response code 400 at the documented identity URL. A fresh
13:20 WAT VAS-only check verified the Postman URL and VAS credential mapping;
the rejection persisted. Its underlying cause remains unverified.
See [9psb-uat-evidence.md](9psb-uat-evidence.md) for exact results, regression evidence
and the remaining activation gates. No test identity or settlement is implied by
successful account reads, and no activation flag was changed.


## WaaS deployment preparation — 29 October 2026

The current release work migrates shared authorization and ledger fixtures to
explicit 9PSB accounts and bank-owned identity evidence. Retired ALAT/Prembly
wire and UI contracts are recorded by original Git commit, source hash and
replacement suites in retired-provider-test-inventory.json; the full default
Django and PostgreSQL test discovery remains enabled. Historical reversal,
refund, liability, browser authorization and shared financial-control checks
remain active.

This checkpoint fixes lost-response identity retries, concurrent browser KYC
submissions, shared identity request budgets, retained-statement guards,
historical balance visibility and bank recovery error isolation. Scheduled
balance and settlement findings alert operators without including customer
account numbers. A stuck instruction remains pending and alerts after two
hours; recovery never guesses a refund.

Full local regression and PostgreSQL release validation are still in progress.
This checkpoint is not a deployed or bank-certified result. The requested
activation is WaaS sandbox only, with VAS disabled and production UAT approval
false. New identity, OTP and test NUBAN acceptance still require approved bank
test fixtures and a permitted tester. Existing cron schedules can continue
through the reconciliation alias; consolidation is separate from this release.
