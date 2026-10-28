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
The older December 2024 collection does not define the newer identity handshake;
the July PDF takes precedence. Agreements, credentials and identity data are not
committed to this public repository.

## Delivery controls

- Separate cached authentication for WaaS and VAS; bounded network timeouts.
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
| VAS | Dynamic catalog, validation, purchase and status code implemented | Supplied VAS credentials return HTTP 400 / bad credentials; replacement required |
| Prembly Tier 3 | Correlated request/status adapter prepared; new paid requests disabled | Terminal approval enum and retrievable report contract, then tests and activation |
| App and WhatsApp | New app screens and secure WhatsApp identity forms drafted | Shared payment screen, WhatsApp VAS/transfer flow and full regression migration still required |
| QR and corporate WaaS | Documented by bank | Product implementation and acceptance tests remain |
| Virtual accounts and card acquiring | Commercial scope only | Technical API/callback/reconciliation contracts are missing |
| Render | Blueprint validated; bounded durable scheduler and idle polling implemented | Authenticated resource cutover and full daily-cycle evidence |

The first 47 targeted banking tests pass locally on SQLite; TypeScript checking
passes. The broader app run exposed 12 failing suites that still use retired
provider/screen contracts (60 passed). The broader backend run also exposed
legacy-contract failures and did not complete locally. These are unresolved
release gates, not evidence of a fully passing application. PostgreSQL validation
is run separately before the unchanged full regression suite in CI.

Keep all provider agreements, credentials and identity attachments out of this
public Git repository. Sandbox keys were installed only in server environment
configuration, with every new-customer activation switch left off.
