# Zitch go-live audit — 2026-10-01

> **Release audit.** Evidence cutoff: 2026-10-01 UTC. Repository evidence is
> recorded below and in the release PR checks; deployment IDs and live verification
> remain outstanding while services are suspended. A passing repository check is not
> evidence that the same revision is deployed or that a provider accepts the live
> tenant.

## Release decision

**HOLD.** The Frankfurt Render estate is suspended for billing, the public API and
Meta connector return 503, and the latest known API deployment is
`c00466d`. The prior main revision under review is
`e12d286fd5305be2ea53e5a12eda03329b3e79cb`; the current tested working branch is
`fix/zitch-go-live-audit` and contains the reviewed audit fixes. None of those source
fixes is live evidence.

All Frankfurt API, worker, Meta connector, cron and database resources were reported
suspended on 2026-09-30 because of billing. This blocks live migration, liveness,
provider, queue, callback, reconciliation and customer-state verification. Billing
restoration is necessary, but it does not itself certify any rail.

The API and worker are configured to auto-deploy only after checks pass. The Meta
connector has auto-deploy disabled and therefore needs an explicit deploy of the
reviewed revision after API readiness. The current 503 responses from both public
entry points are consistent with suspension and provide no application-health
evidence.

## Implemented repairs

| Journey | Repair and resulting behavior |
| --- | --- |
| Signup and recovery | Names survive the SMS round trip. Only the newest bound challenge can be consumed. Password/PIN changes authenticate the locked current user, and malformed requests cannot create a challenge or call a provider. |
| Contact verification | Email codes prove the inbox that received them; contact changes retire stale challenges. Replacement phone changes route to Support until a secure replacement-proof route exists. |
| Tier 1 identity | Accepted bank OTP evidence survives delayed provisioning. Concurrent confirmations serialize one OTP consumption. A callback/account number alone cannot grant BVN proof. |
| App purchases | Duplicate submissions are blocked immediately, dismissed PIN entry cannot submit later, and electricity verification is bound to the current meter/provider/type. |
| Pending transfers | Processing/review copy is truthful. Retry keys survive uncertain outcomes and are cleared only for the matching confirmed terminal reference; old responses cannot regress terminal status. |
| WhatsApp purchases | Invalid explicit destinations cannot fall back to the sender. Nigerian phone normalization is shared with the app API, and stale confirmations are revalidated before spending. |
| Person-to-person payments | Real customer payments use the bank transfer rail; the recipient is credited from authenticated bank evidence rather than a second local balance write. |
| Unsupported products | New savings, loans, FX, airtime-to-cash and card top-ups fail closed in production before PIN/provider/money writes; existing obligations stay readable. |
| Transaction notifications | A per-channel durable outbox survives process crashes and metadata rewrites. Accepted channels do not repeat; uncertain dispatch needs review. Explicit refusals have bounded backoff. |
| Outage recovery | Per-account statement checkpoints recover missing windows with overlap. Malformed/partial history cannot advance coverage. Unknown opening balances block new bank spending until audited. |
| Release operations | API configuration is canonical for consumers. Production signing requires successful exact-SHA CI and an AAB signer matching the configured upload certificate. CI now includes PostgreSQL 18. |

## Launch scope

The launch decision must name one of these scopes:

| Scope | Included capability | Decision rule |
| --- | --- | --- |
| `core` | Authenticated accounts; verified contacts; Tier 1 BVN/NUBAN provisioning; supported inbound funding, bank payout/transfer and Wema VAS rails; receipts, alerts, operator controls and reconciliation | Eligible only after every core code, deployment, provider and operational gate below passes. |
| `full` | Core plus fixed savings, loans, FX conversion, airtime-to-cash and card top-ups | **Unavailable.** These writes remain production-disabled until each has bank-backed settlement and complete accounting/reconciliation. |
| Conditional | Virtual-card issuance and controls; Tier 2 and Tier 3 upgrades | Requires the relevant live provider contract and tenant proof in addition to the core gates. Card top-up remains outside core. |

`common.products` is the capability source for the production-disabled products.
`manage.py wema_preflight` defaults to `--launch-scope full`, which must fail while
any declared product is unavailable. A deliberate core review uses:

```bash
python manage.py wema_preflight --launch-scope core --strict
```

The authenticated HTTP equivalent is
`GET /preflight?launch_scope=core&strict=1`. A core result reports every excluded
product and prints `GO` only with `launch_scope=core`. It is not approval for the
whole platform. The preflight is read-only and must not log PII.

## Identity and account tiers

Zitch's product tiers are derived from durable proof:

| Tier | Required proof |
| --- | --- |
| Tier 1 | Verified email, verified phone and verified BVN |
| Tier 2 | Tier 1 plus verified NIN and provider-attested liveness |
| Tier 3 | Tier 2 plus verified address |

The partner bank's account-tier state and limits can have a separate upgrade
sequence. Zitch must enforce the locally derived tier and spend rules until the
corresponding bank upgrade is confirmed. A still image, an account number, a
successful login or an old tier integer cannot substitute for a required proof.

### Interactive liveness contract gate

The native Prembly adapter now targets the documented image liveness endpoint and
fails closed on incomplete or inconsistent results. That code contract does not
prove a browser/WhatsApp interactive challenge. Before Tier 2 is enabled on those
channels, the provider must confirm the live tenant's interactive workflow and
credentials. A full secret-authenticated session readback must bind the subject,
challenge/action metadata, terminal result and provider reference to the requesting
user. A public widget key, client callback, correlation ID or HMAC without that
provider readback is insufficient. Tier 2 and Tier 3 remain outside a certified
launch until this contract succeeds on the live tenant.

## Money and settlement gaps

### Unsupported product writes

Fixed savings, loans, FX conversion, airtime-to-cash and card top-ups currently lack
complete bank-backed customer-money settlement. Local ledger mutations cannot create
or discharge real claims on customer NUBAN funds. Their production write paths must
stay fail-closed, including direct services and app/WhatsApp entry points. Read-only
status, rate and product-list responses should expose the capability state.

Internal person-to-person transfer must use the reviewed bank payout path. It cannot
be certified from unit tests alone; the live debit, provider outcome, recipient
credit, timeout/unknown handling, reversal and reconciliation cases must all be
proved with attributable references.

### NIP fee, VAT and settlement accounting

The [official ALAT debit-wallet documentation](https://playground.alat.ng/api-debit-wallet)
states that non-Wema transfers use NIP charge enquiry and the bank handles the
charge. The charge must not be added to the transfer's principal request. The
published contract does not identify the fee debit account, VAT inclusion, gross
source debit or fee reversal rules. This release does not invent those details.

The current integration can retrieve and display a Wema NIP fee, but the repository
explicitly leaves charging that fee to customers as a product-pricing decision. A
go-live owner must approve and test:

- who bears the NIP fee and when it becomes final;
- whether the displayed fee is debited, absorbed or included in a quoted total;
- the applicable VAT treatment, with finance/tax approval rather than a guessed
  rate;
- ledger accounts and transaction lines for principal, fee and tax;
- provider settlement matching, reversals, refunds, statements and reconciliation.

Until that policy and accounting trail are implemented and reconciled, transfer
economics and settlement cannot be certified even if the principal transfer works.

## Repository and deployment evidence

| Evidence class | Current evidence | Required final evidence |
| --- | --- | --- |
| Repository baseline | Prior main under review: `e12d286fd5305be2ea53e5a12eda03329b3e79cb`; working branch: `fix/zitch-go-live-audit` | **Release owner fill:** reviewed commit SHA, clean tree/diff, full CI links and exact pass counts. |
| Database semantics | The existing SQLite backend lane cannot exercise PostgreSQL `SELECT ... FOR UPDATE`, append-only database constraints or real transaction races. The audit branch adds a PostgreSQL 18 lane with migration consistency checks and the full backend suite. | A successful `Backend (PostgreSQL)` check on the exact release SHA. Skipped SQLite concurrency tests are not substitute evidence. |
| Production API | Latest known actual deploy: `c00466d`; public API currently 503 | Deploy the reviewed SHA, record deployment ID/SHA, migration result, `/healthz`, `/readyz` and scoped strict preflight. |
| Worker/cron config | Blueprint changes make API settings canonical for consumers, require shared cache and stagger fixed jobs | Confirm the deployed services inherit the intended values and that queue consumers and each cron complete on the reviewed SHA. |
| Meta connector | Suspended; auto-deploy is off | Manually deploy the reviewed SHA after API readiness and record authenticated webhook/queue evidence. |
| Provider contracts | Repository adapters and fail-closed checks only | Live Wema, Prembly, communications and card-provider tenant results with non-PII references. |
| Mobile | Repository release workflow checks only | Successful main-SHA workflow, expected signing certificate, Play relationship and physical-device/store-track validation. |

The root `render.yaml` names a different service set from the existing Frankfurt
resources represented by `render.frankfurt.yaml`. Do not sync either Blueprint until
the release owner confirms the existing resource identities and Render preview shows
updates rather than duplicate paid resources. Never overwrite live secrets during
that reconciliation.

## Android release gate

The production AAB path must build the exact reviewed main SHA after the required CI
checks pass. The artifact must record that SHA. Before upload, verify the AAB
signature and compare its SHA-256 certificate fingerprint with the configured upload
keystore; checking that an AAB is merely signed does not establish which key signed
it.

Repository checks still require operational proof: the production keystore secrets
must exist in the selected runner, the upload certificate must match the Play App
Signing configuration, and an owner must complete a successful workflow and test the
result on a physical device and intended Play track. Preview/debug keys cannot be
used for production.

## Restore, deploy and reconcile

The published bank history endpoint has no documented paging inputs. Explicit
truncation/paging hints are rejected, but a silent tenant response cap still needs
live confirmation. Unresolved older windows deliberately pin coverage; inspect
review/backlog diagnostics and complete catch-up before certifying later history.

After billing restoration, use this order and preserve the evidence from each step:

1. Confirm the existing Frankfurt resource identities, database availability and a
   current recoverable backup. Record each deployed SHA before changing it.
2. Deploy the reviewed API first. Its release command must apply every reviewed
   pending migration, including the wallet migrations added since `c00466d`. Stop on
   any migration or startup failure.
3. Verify API `/healthz`, `/readyz`, database connectivity and the required shared
   cache. Run `wema_preflight --launch-scope core --strict`; treat any result as
   evidence for core only.
4. Redeploy the worker and crons after the API migration succeeds. A consumer whose
   schema check raced the API deploy must be redeployed; do not treat a stopped
   consumer as healthy.
5. Manually deploy the Meta connector, then prove authenticated webhook acceptance,
   durable enqueue and worker drain without exposing message content or customer PII.
6. Inspect the last-success time for every scheduled job. The suspension creates an
   unknown catch-up window starting 2026-09-30.
   Apply wallet migrations `0023_transactionalertdelivery` and
   `0024_bankhistorycheckpoint` before starting consumers. Inspect
   `transaction_alerts_status` for review/exhausted counts and statement checkpoint
   errors. Legacy/imported accounts without a certified opening balance remain
   blocked for new bank spend; compare attributable bank/ledger evidence and record
   the finance review before any explicit correction. Do not clear a flag to bypass
   that review.
7. Run controlled, non-overlapping catch-up for Wema transaction reconciliation,
   balance/integrity checks, settlement reporting, AML scanning, expired-session
   cleanup and any eligible maturities. Take before/after counts and provider
   references. Never replay a debit/credit or mutate evidence merely to make a check
   green.
8. Review pending, unknown, reversed, duplicate and mismatched outcomes. Observe at
   least one normal scheduled cycle after catch-up before declaring operations
   stable.

The reviewed Blueprint staggers fixed daily jobs away from Wema reconciliation
boundaries. Its intended UTC cadence is Wema reconciliation every 10 minutes,
balance reconciliation at minute 5 every six hours, maturities at 01:05, integrity
at 02:35, session purge at 03:45, AML scan at 05:05 and settlement reporting at
06:15. A schedule in YAML is configuration evidence only; the post-restoration job
history must prove execution and alert delivery.

## Customer-specific proof and repair

Code tests, aggregate health and a green reconciliation job cannot establish what
happened to a specific customer. First authenticate and match the customer to the
account. Then inspect the relevant `IdentityProof`, provisioning attempt, webhook
event, funding intent, transaction and provider references in one timeline.

Do not infer `otp_verified_at`, BVN/NIN ownership, liveness, tier, funding or transfer
success from account existence, a local flag, a UI screenshot or another customer's
provider result. Restore a flag only from attributable durable provider/bank proof;
otherwise complete the secure verification route. Use the normal idempotent
reconciliation path for accepted pending operations. Never replay a money movement
or grant identity proof simply to clear an error.

## Blocking release gates

- Restore billing and verify that the database and every Frankfurt runtime can
  start; record the live state rather than inferring recovery from the dashboard.
- Select and document `core` or `full`. `full` is blocked by the five unavailable
  bank-backed product capabilities.
- Finish final review and CI on one immutable SHA; deploy that SHA to the API,
  worker, connector and crons in the required sequence.
- Require both backend lanes on that SHA. SQLite remains useful for broad, fast
  coverage, but PostgreSQL 18 must pass the full suite so row locks, transaction
  races and database constraints execute on the production database engine.
- Obtain a strict scoped preflight plus live health, shared-cache, queue, callback,
  provider and reconciliation evidence.
- Resolve the interactive Tier 2 liveness tenant contract before certifying Tier 2
  or Tier 3.
- Resolve NIP fee, VAT, ledger and settlement policy before certifying transfer
  economics.
- Complete the Android signing/Play/device proof before distributing a production
  build.
- Complete suspension-window catch-up and customer-specific review where required.

## Final evidence to add

Local integration evidence before release:

- App: **41 suites / 277 tests passed**, TypeScript passed, ESLint had **0 errors**
  (existing warnings remain).
- Meta connector: **10 suites / 159 tests passed**, TypeScript passed.
- Static production deployment settings and migration drift checks passed.
- Focused money/provider checks: **287 passed**; alert/provider/email-binding checks:
  **84 passed**. Full backend and PostgreSQL release results are recorded by the
  release PR's CI checks and must pass on the exact source revision.

- Reviewed release SHA and PR: **TBD by release owner**
- Full SQLite backend, PostgreSQL 18 backend, app and connector checks and links:
  **TBD by release owner**
- Render deployment IDs and deployed SHA per service: **TBD after restoration**
- Migration output and schema version: **TBD after API deployment**
- Scoped strict preflight report: **TBD after deployment**
- Live provider, callback, queue and reconciliation references: **TBD after live
  verification**
- Android workflow, certificate and Play/device evidence: **TBD by mobile release
  owner**
