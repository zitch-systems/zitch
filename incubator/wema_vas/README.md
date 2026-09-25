# Wema VAS isolated development service

Status: **isolated synthetic testing plus a separately configured bank-validation service. Neither is bank-connected.**

This directory is independent of the existing Zitch backend. It is not installed
in `backend/zitch_api/settings.py`, mounted in existing URLs, included in a
Render service, or used by the mobile app, WhatsApp worker, or current cron jobs.
It does not replace the existing bill-payment code that also uses the name VAS.

## Safety boundary

- Separate Django project. Synthetic mode uses SQLite at `var/synthetic.sqlite3`;
  validation mode requires its own PostgreSQL database with TLS.
- No imports of the existing `accounts`, `wallet`, `transfers`, or provider code.
- No existing files, deployments, customer records, or balances are changed.
- Synthetic mode recognizes only the three fixture accounts. Validation mode
  recognizes only accounts independently enrolled with encrypted KYC evidence.
- No outgoing bank client, payout endpoint, or automatic provider fallback.
- `WEMA_VAS_DEV_ENABLED` (synthetic) and `WEMA_VAS_ENABLED` (validation) both
  default to false. Every bank endpoint returns 404 when off.
- Validation requires a separate Bearer token of at least 48 characters, explicit
  hosts, TLS, dedicated PostgreSQL credentials, and identity encryption keys.
- Synthetic requests must originate from loopback; forwarded headers do not
  bypass that rule. Validation accepts HTTPS through a trusted proxy only.
- The process refuses the existing `DATABASE_URL`, does not load `.env`, and
  cannot select existing Zitch settings through its entry points.
- Tests use a disposable database and block socket connections.
- Render probes `/readyz`; it returns 503 when the dedicated database cannot be
  queried. `/healthz` remains a process liveness check and does not assert
  successful bank validation.

Never expose synthetic mode through a proxy/tunnel or use its fake accounts in
bank transfers. The standalone validation service must be provisioned only with
its own database, access controls and approved identities. It is not an account
migration and cannot read existing Zitch customers or balance data. SQLite
concurrency tests are not proof of production PostgreSQL behavior.

## Implemented scope

All routes accept authenticated POST JSON, with or without the trailing slash:

| Isolated endpoint | Behavior |
| --- | --- |
| `/vas/account-lookup` | Static-account lookup, vendor-first name, synthetic BVN/NIN, invalid/inactive codes |
| `/vas/transaction-notification` | Atomic local credit and receipt, session replay protection, conflict rejection |
| `/vas/mini-statement` | Local credits over ten calendar dates anchored to the last transaction day |
| `/vas/kyc-details` | Account identity and local posted balance, also available for blocked accounts |
| `/vas/block-account` | Idempotent restriction with original reason/time retained |

Transaction Search request builders and response classification are pure functions
in `contracts.py`. They make no bank calls, never credit balances, and classify
non-`00` NIBSS results as uncertain rather than automatically failed/refundable.
The production-only bank search URL/authentication and Outward TSQ are not invented.

`services.reconcile_snapshot` compares a supplied bank-search snapshot with
local receipts and reports matches, missing receipts, held funds, mismatches,
and uncertain bank statuses. It performs no writes, automatic corrections, or
refunds. An uploaded fixture is not authenticated bank settlement evidence.
`audit_inflows` checks whether accepted receipts match the isolated local balance
without changing either. PostgreSQL installs an append-only receipt trigger.

Fresh notifications for blocked accounts are retained as held receipts without
synthetic value or success acknowledgement. Previously accepted identical retries
still receive `00`, even after blocking. Conflicting session IDs, payment references,
amounts, or account mappings never create another credit. SQLite write contention
returns 503 for retry rather than claiming success.

## Local verification

From this directory, in a dedicated virtual environment with no live settings:

```sh
python -m pip install -r requirements.txt
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test vas_harness --verbosity 2
```

The new `VAS isolated contract tests` workflow runs this suite separately with
read-only repository permissions and no production secrets or deployment steps.
It does not replace or modify the current application's CI workflow.

For an optional local development server:

```sh
python manage.py migrate --noinput
python manage.py seed_synthetic
```

Set `WEMA_VAS_DEV_ENABLED=true` and a freshly generated random
`WEMA_VAS_DEV_TOKEN` (at least 32 characters) in that process only, then:

```sh
python manage.py runserver 127.0.0.1:8011
```

Do not reuse existing bank or callback secrets. Do not commit tokens or a database.
Stopping this independent server disables the experiment; there is no live
customer rollback to perform. Running the seed again neither resets balances
nor removes restrictions.

The simulated accounts are `7110000001`, `7110000002`, `7110000003`.
Names and identity values are deliberately synthetic. They are not verified BVN/NINs
and are not ready to send as onboarding samples to the bank.

Example lookup body:

```json
{"accountnumber": "7110000001"}
```

Example simulated notification body:

```json
{
  "originatoraccountnumber": "0000000000",
  "originatorname": "SYNTHETIC SENDER",
  "bankcode": "000000",
  "bankname": "SYNTHETIC BANK",
  "amount": "3100.05",
  "narration": "Synthetic test only",
  "paymentreference": "SIM-PAYMENT-001",
  "sessionid": "SIM-SESSION-001",
  "craccount": "7110000001",
  "craccountname": "Zitch/SYNTHETIC TEST ONE",
  "created_at": "2026-01-20T16:15:14.983"
}
```

Send that payload twice in local synthetic mode. Both acknowledgements must have the same vendor reference;
the simulated balance must remain 3100.05, not double. Change the amount while
retaining the session ID and expect 409. Block the account and expect inactive
lookup; a new notification must be held without increasing its balance.

## Audit findings and required next phase

Baseline inspected: `f5d8e84a13e90637a1cc047dcfcc95510526c83d`.

1. Existing Zitch retains BVN/NIN as keyed hashes plus last four digits, not raw
   identity values (`backend/accounts/models.py`). VAS requires returning an
   actual BVN/NIN in lookup/KYC responses. Hashes cannot be reversed. Decide an
   approved re-verification/recollection path and encrypted identity vault with
   retention, access auditing, and key rotation before linking real customers.
   Do not weaken or overwrite existing identity proofs to accommodate VAS.
2. The PDFs' credential direction is important: Zitch builds five inbound APIs
   and supplies its URLs/Bearer token to Wema. We do NOT need Wema-issued keys to
   develop those endpoints. Wema supplies the production Transaction Search
   details and assigns a live prefix after validation/collection-account profiling.
3. Local `711` fixtures are not a Wema sandbox. Wema explicitly reported no
   dedicated sandbox. A separate authorized bank-validation environment, TLS,
   secret distribution, KYC arrangements, and approved sample accounts are still needed.
4. The Fintech Hub FAQ describes payouts from the collection account and mentions
   Outward TSQ. It does not provide the outbound API contract, debit source,
   limits, credential scope, timeout/reversal guarantees, or per-customer eligibility.
5. Do not automatically route a pending/timeout payment through the partnership
   provider. The original request might have succeeded. Provider selection must
   be bound to the transaction; resolve status before retry/refund. Rollback must
   retain reconciliation and callbacks for any provider with in-flight funds.
6. Wema's written Slack response on 22 September says partner KYC may include
   address verification depending on customer profile. The reported informal
   exemption needs written reconciliation; this prototype removes no KYC rule.
7. Bill payments are separate from core VAS. Existing Wema billers are untouched.

## Validation environment (separate resource; deployment not performed)

The optional `incubator/wema_vas/render.yaml` describes a new Frankfurt web
service and a **new 1 GB database** with external database access denied. It does not change the root `render.yaml` and has
`autoDeployTrigger: off` and `WEMA_VAS_ENABLED=false`. Applying this Blueprint
would create separately billed infrastructure. The disabled service can start
before bank token and identity keys are supplied; enabling the bank endpoints
requires both valid secrets and a restart. Add both manually to the isolated
service's Render environment. No real customer should be enrolled until Zitch approves a
BVN/NIN verification source, consent, retention and access-audit procedure.
If provisioning is approved, select the custom Blueprint Path
`incubator/wema_vas/render.yaml` on branch `codex/vas-isolated-e2e` in Render.

For validation mode, set `WEMA_VAS_MODE=validation`, `WEMA_VAS_ENABLED=false`,
`WEMA_VAS_BANK_TOKEN` (new random >=48 characters), `WEMA_VAS_IDENTITY_KEYS`
(comma-separated Fernet keys with the newest first), `WEMA_VAS_DJANGO_SECRET`
(new random >=44 characters), `WEMA_VAS_ALLOWED_HOSTS` (explicit service DNS),
`WEMA_VAS_PREFIX=711`, and a dedicated `WEMA_VAS_DB_CONNECTION` from the isolated
database's internal `connectionString` property. Manual provisioning can use
`WEMA_VAS_DB_NAME`, `_USER`, `_PASSWORD`, `_HOST`, `_PORT` instead; do not use
both methods. The database name must start with `zitch_vas_`; its connection
requires SSL mode `require` or
`verify-full`. Set `WEMA_VAS_TRUST_TLS_PROXY=true` only behind a trusted reverse
proxy that strips untrusted forwarded protocol headers. Do not reuse existing
Wema partnership credentials, Zitch database, or customer tables.

After independently validating the customer's BVN/NIN, enrollment evidence and
consent, an authorized operator can feed exactly one JSON object **through
protected stdin** to `python manage.py enroll_verified`. Its required keys are
`customer_reference`, `customer_name`, `bvn`, `nin`, `phone`,
`verification_reference`, `consent_reference`; the command prints only the
allocated number. The references are operator attestations: the command cannot
verify documents, phone ownership, BVN/NIN or consent itself. Never paste real
identities or secrets into chat, Git, a shell history, or CI logs. Store evidence
in the authorized verification system; keep its opaque references here.

The encrypted BVN/NIN/phone are decrypted only for authorized bank lookup/KYC
and strict notification account-name checks. To rotate keys, place the new key
first, retain previous keys, run `python manage.py rotate_identities`, and keep
old keys until backups expire. The command rolls back fully if a record cannot
be decrypted.
Test bank validation with three **approved** `711` accounts, confirm TLS/Bearer,
the five endpoints, duplicate inflow acknowledgement, blocked accounts and
settlement. `python manage.py bank_handoff --base-url https://vas.example.test`
prints the five URLs and three approved sample account numbers without printing
the secret token. It refuses to export samples unless the service still uses
the `711` test prefix, the accounts are active with verification and consent
references, and their encrypted BVN/NIN can be read with the current key. Then
send the five production URLs and token via Wema's prescribed
official secure onboarding channel. Only enable the bank endpoints after
configuration and approved controlled testing. When Wema assigns a live prefix,
configure that prefix before enrolling any live accounts; existing `711` test
accounts are not migrated automatically.

The status returned as `walletbalance` is the service's accepted inflow balance,
not proof of settled funds in Wema's collection account. Outbound payouts remain
disabled until Wema supplies the initiation and Outward TSQ contracts, debit
source, fees/limits and settlement/reversal behavior. A customer's payment must
remain bound to its original provider; a timeout is not permission to reroute.

## Contract assumptions to resolve before bank testing

- Naive `created_at` values use Africa/Lagos in this simulation. Confirm the bank's timezone.
- Mini-statement credit samples use `transactionDate`; the PDF's debit example
  uses `transactiondate`. No outbound/debit entries are synthesized here.
- The docs prescribe the success acknowledgement but not every error HTTP status.
  Current 400/409/503 failures and blocked-inflight treatment are local safety
  choices, not verified bank behavior. Confirm retry/backoff and hold resolution.
- `paymentreference` is additionally unique in the harness. Confirm uniqueness
  scope, permitted lengths, and canonical retry payload fields with Wema.
- Mini-statement has no documented pagination; load limits and response size
  must be agreed and tested before live volume.
- PostgreSQL has an append-only receipt trigger, but account balance postings,
  verification evidence, daily settlement proof and PostgreSQL contention/load
  behavior require operational verification before real money is accepted.
- Statements over 5,000 rows fail closed rather than silently truncating data;
  agree a bank-supported pagination or bulk handling contract first.

## Verification record - 23 September 2026

- Isolated VAS suite: **49 tests passed** on Python 3.12 / Django 5.2.17.
- Django system check and migration-drift check passed; Python compilation and
  Git whitespace checks passed.
- Git comparison against the baseline confirms zero modifications to existing
  tracked files. The only additions are this directory and its test-only workflow.
- Existing backend suite: 2,767 tests ran; three failures and one error. One failure
  was local subprocess configuration (SQLite opt-in missing), not VAS behavior.
- Re-ran the relevant 14 tests in a separate untouched baseline worktree with the
  correct CI SQLite configuration. The configuration test passed; two failures
  and one error remained in `whatsapp.test_flow_contract`: server transitions and
  response builders reference `SIGNUP_EMAIL_CODE`, absent from the Flow JSON.
  These are reproduced baseline failures, not introduced by the isolated service.
  Existing application files were intentionally not repaired in this scope.
- No app/mobile build, actual bank transfer, OTP/liveness, bank-connected
  end-to-end test, or production migration was performed.
- The initial isolated harness was published to the approved branch and its
  dedicated synthetic CI passed. This validation extension is a separate review
  branch on the current `main`, with no deployment or bank activation.

## Verification record - 25 September 2026

- This extension is based on current `main` at `ccbcf80`; the separate WhatsApp
  signup Flow defect described above has since been repaired on `main` (#515).
- **61 isolated tests passed** on Python 3.12 / Django 5.2.17, including
  validation-mode HTTPS, encryption, replay/blocking, token-free bank handoff,
  key rotation, and read-only balance auditing. Django checks, migration drift,
  Python compilation and Git whitespace validation passed locally.
- Validation configuration booted with inert credentials: `/healthz` returned
  200 while the five disabled bank endpoints returned 404. The Blueprint was
  parsed structurally, but Render CLI validation and an actual Render deployment
  have not been performed.
- No PostgreSQL instance or authenticated Wema Transaction Search/Outward TSQ
  endpoint was available to exercise. No real identity, callback, transfer,
  settlement, refund or provider migration was attempted.
- Current isolated suite: **64 tests pass**, including disabled validation
  startup with Render's internal database connection reference and an HTTP
  readiness check that returns 503 when database connectivity is lost.
- Render's preview of the `codex/vas-isolated-e2e` branch and custom Blueprint
  Path `incubator/wema_vas/render.yaml` was blocked before resource creation:
  **"Additional services & databases will exceed limit of 25"**. The same
  workspace displays **"Payment failed"**. No VAS service/database has been
  created, and existing 25 resources were not changed or deleted.
- Separate capacity for two new resources and a resolved payment method are
  required before provisioning paid staging. Do not remove the Oregon rollback
  copies or repurpose the existing partnership PostgreSQL instance to make room.

## Acceptance gates (not completed)

- Production-safe customer/identity and provider-specific ledger design reviewed.
- Staging with its own database and credentials deployed and bank-validated.
- Wema validation completed, settlement account profiled, live prefix assigned.
- Outbound specification and reconciliation/reversal contract received and tested.
- Fees, limits, KYC policy, and compliance approval confirmed.
- App/WhatsApp rollout, reconciliation ownership, in-flight rollback tested.
- Owner explicitly approves migration/activation. No production migration or
  activation is authorized by this preparation branch.

## Supplied PDF contract review - 25 September 2026

Reviewed all 14 newly supplied PDFs, including the 22 September printouts of
*VAS Integration Endpoints* and *Transaction Process Flow*. The duplicate
Account Lookup PDFs are byte-identical. The documents describe vendor-managed
static virtual accounts: Zitch generates ten-digit numbers under Wema's
three-digit prefix. The test prefix `711` is temporarily used for validation;
Wema assigns the live prefix only after validating the five vendor endpoints
and profiling the collection account.

| Documented bank interaction | Implementation in this isolated service | Remaining dependency |
| --- | --- | --- |
| Account Lookup, Notification, Mini Statement, KYC Details, Block Account | Five authenticated POST routes and contract tests | Bank acceptance against approved test accounts and live TLS URL |
| Inbound Transaction Search | Request/response parsing and read-only comparison; no bank network client | Wema supplies production URL and authentication after go-live readiness |
| Payout from the Collections Account | No transfer initiation or bank client | Wema's outward initiation contract, credentials, debit source and limit/fee rules |
| Outward TSQ for pending payouts | No bank network client | Wema's Outward TSQ URL, request/response/auth and status/reversal rules |

The process flow says Wema performs its own NIBSS transaction-status query
before notifying the vendor and crediting the collection/suspense account.
The Fintech Hub FAQ describes outward payouts and pending confirmation, but
provides no outward API specification. Neither document establishes that the
customer's virtual account itself can be debited; payouts shown there are from
the collection account. A successful local callback is not a reconciled bank
statement. There is no bank-connected end-to-end inflow or outward transfer
test yet.

Sources reviewed: the supplied Account Lookup, Transaction Notification, Mini
Statement, KYC Details, Block Account, Transaction Search, VAS Onboarding and
Fintech Hub PDF documents, plus Introduction & Scope, VAS Integration Endpoints,
Transaction Process Flow, Fraud Management and the version 2.0 cover page.
Source PDFs and private Slack messages are not copied
into this repository.
