# Partnership operational audit — 16 October 2026

**Decision: Partnership is active in production; full end-to-end readiness remains on hold.**
Live infrastructure, Meta configuration, provider correspondence and the current
app/backend source were reviewed independently. Healthy HTTP endpoints and
automated tests do not establish successful customer banking acceptance.

## Production evidence before this repair

- API, WhatsApp worker and seven scheduled jobs run `dc277dca9e95a70ad9ea4038bb5d10ab4461579a`,
  the Partnership-only release. The latest deployment diagnostic reports
  `account_provider=partnership`, `partnership_active=true`, bank configured,
  simulation off, biller enabled and Tier 2 session adapter unavailable.
- API health/readiness endpoints respond 200. No API 500/502/503/504 responses or
  API/worker failure/restart events were found after the 13 October deployment.
  Database is available and memory/connection headroom is healthy.
- Actual worker/cron builds wait for migrations, and start commands check them.
  Current API reports no pending migrations.
- Meta credentials are valid, business verified and number quality GREEN.
  Runtime selects published Partnership Flow `1601841594748416`: 24 screens,
  eight valid routing roots, no dangling/unreachable routes or validation errors.
  `txn_alert` is approved in `en_US`. Meta reports one sent and delivered message
  on 16 October. This aggregate does not prove a completed banking journey.
- Retired virtual-account Flows are deprecated. Historical records do not enable
  virtual-account enrollment or new payments. VTU/VAS labels in bill code mean
  value-added services and are not evidence of a restored virtual-account rail.
- The read-only Meta connector uses an older commit but its complete subtree is
  identical to current main (`8c112bddb67292aea469ab9e8da0c90ed412a81d`). No redeploy is needed
  merely to make its commit label match the banking backend.

## Confirmed blockers

| Area | Evidence | Required resolution |
| --- | --- | --- |
| Bank/ledger balances | Current control reports unresolved ledger-to-bank differences | Obtain reference-bound bank statements, fees/holds/refund evidence; append only independently verified corrections |
| Opening history | Current sweep reports unresolved opening-balance reviews | Verify attributable opening balances and complete account history before clearing holds |
| Settlement assets | Pool read is rejected; historical funding and account issuance require verification | Confirm pool balance/account contract and resolve original bank account issuance plus historical liability |
| Tier 2 | `tier2_face_available()` is false; no trusted interactive Prembly session adapter | Implement and validate provider-attested session results bound to customer, action, expiry and bank upgrade face |
| Tier 3 | Address submission/completion needs account-, address- and request-bound bank evidence | Confirm bank verification contract and complete a correlated acceptance case |
| Mobile delivery | The 13 October Android build explicitly logged absent Firebase configuration and inability to receive remote push notifications; iOS/device acceptance not established | Supply Firebase/FCM configuration, verify iOS signing/APNs and received/tapped alerts, logout and account switching on real devices |
| Banking acceptance | No new real-money payout, bill purchase or live customer identity verification executed in this audit | Controlled authorized test of Tier 1, inflow, payout/status, bill success/refund and duplicate/delayed/missed notification recovery, matched to bank and ledger |
| Recovery | Backup restore acceptance not established | Restore into isolated storage/database, run migration/integrity checks and record recovery time |

Internal integrity passed. This does not cancel external differences. Settlement
asset coverage remains incomplete; no complete surplus, shortfall or loss can be
inferred from a partial asset subtotal. Customer-level evidence and operational
financial amounts belong in restricted operational records. No balances,
historical funding, KYC proof or review holds were rewritten to produce a passing
result. Production database network access controls were not relaxed.

## Repairs in this change

- Reject delayed app response bodies and authentication results after session
  changes; rebind existing push permission following password recovery.
- Issue and atomically redeem credential-bound WhatsApp link invitations, compare
  canonical phone numbers, revoke stale/frozen-account invitations and pending
  invitations at unlink, and preserve messaging preferences on replacement.
- Reject unconfigured or malformed WhatsApp webhook verification tokens safely.
- Require successful bank HTTP/envelope evidence and matching recipient/reference
  data. Conflicting statuses and processor timeouts remain pending for recovery;
  readable balances alone cannot establish permission to debit an account.
- Reject inactive bank routes and malformed recipient account numbers.
- Add explicit incomplete-recovery scheduler status after settlement phases;
  include identity completion, coverage gaps, provider failures and overdue holds.
  Enable `WEMA_RECONCILE_FAIL_INCOMPLETE=true` only on the reconciliation cron.
- A disabled savings product with no unpaid plans is an audited no-op. Any unpaid
  obligation remains a failure requiring review, with no invented local payout.
- Apply the operator portal's second-factor policy to Django admin, including
  diagnostics and existing browser sessions; retain compliance and recovery access.
  Protect wallet, funding-intent and transaction records from generic admin edits,
  creation and deletion while retaining checked account-recovery actions. Make
  token and OTP admin routes read-only, hide credential hashes and retain the
  authorized issuance, reset and revocation services.

The main app retains WhatsApp. The earlier one-off WhatsApp-free APK is a separate
build and is not the standard release. Existing pending unsigned WhatsApp link
invitations expire safely; users request a fresh invitation after deployment.

## Provider acceptance boundaries

Historical Partnership approval and callback profiling were confirmed during the
private provider-correspondence review. Pilot naming alone does not establish
sandbox operation. Separate virtual-account discussions cannot establish or
revoke Partnership entitlement. Current provider behavior and account-bound
acceptance evidence remain required; historical approvals are not a substitute
for current bank/ledger, identity or notification verification.

## Operational notes

- Reconciliation runs every ten minutes. Do not describe it as a ten-second loop.
- WhatsApp business-profile contact currently shows `Admini@zitch.ng`; intended
  administrative address is `admin@zitch.ng`. Available Meta tools are read-only.
- Meta phone code-verification metadata says expired, but successful recent
  delivery means this alone does not establish a messaging outage.
- Do not sync the Frankfurt blueprint to existing production resources: its
  documented ownership warning remains applicable. Merge the single cron flag
  into that existing service's environment without replacing other variables.
- Require complete CI (including PostgreSQL), verified deployed revisions and
  authenticated diagnostics before describing these code repairs as deployed.
  A new test APK must be built from the reviewed main revision; store signing and
  physical-device acceptance remain independent gates.
