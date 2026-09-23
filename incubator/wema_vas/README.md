# Wema VAS isolated development service

Status: **synthetic/local preparation only, disabled by default. Not a bank-connected integration.**

This directory is independent of the existing Zitch backend. It is not installed
in `backend/zitch_api/settings.py`, mounted in existing URLs, included in a
Render service, or used by the mobile app, WhatsApp worker, or current cron jobs.
It does not replace the existing bill-payment code that also uses the name VAS.

## Safety boundary

- Separate Django project and SQLite database at `var/synthetic.sqlite3`.
- No imports of the existing `accounts`, `wallet`, `transfers`, or provider code.
- No existing files, deployments, customer records, or balances are changed.
- Only the three explicitly synthetic `711` accounts below are recognized.
- No production mode, outgoing bank client, payout endpoint, or automatic provider fallback.
- `WEMA_VAS_DEV_ENABLED` defaults to false. Every endpoint returns 404 when off.
- A separate Bearer token of at least 32 characters is required when enabled.
- Requests must originate from loopback; forwarded headers do not bypass that rule.
- The process refuses Render or `DATABASE_URL` configuration, does not load `.env`,
  and cannot select the production settings through its management entry point.
- Tests use a disposable database and block socket connections.

Do not expose this service through a proxy/tunnel, point Wema at it, insert real
identities, or use these accounts for real bank transfers. The loopback restriction
is not a replacement for deployment security. SQLite concurrency tests are not
proof of production PostgreSQL behavior.

## Implemented scope

All routes accept authenticated POST JSON, with or without the trailing slash:

| Local endpoint | Behavior |
| --- | --- |
| `/vas/account-lookup` | Static-account lookup, vendor-first name, synthetic BVN/NIN, invalid/inactive codes |
| `/vas/transaction-notification` | Atomic synthetic credit and receipt, session replay protection, conflict rejection |
| `/vas/mini-statement` | Synthetic credits over ten calendar dates anchored to the last transaction day |
| `/vas/kyc-details` | Synthetic identity and simulated balance, also available for blocked accounts |
| `/vas/block-account` | Idempotent restriction with original reason/time retained |

Transaction Search request builders and response classification are pure functions
in `contracts.py`. They make no bank calls, never credit balances, and classify
non-`00` NIBSS results as uncertain rather than automatically failed/refundable.
The production-only bank search URL/authentication and Outward TSQ are not invented.

`services.reconcile_snapshot` compares a supplied synthetic search fixture with
local receipts and reports matches, missing receipts, held funds, mismatches,
and uncertain bank statuses. It performs no writes, automatic corrections, or
refunds. An uploaded fixture is not authenticated bank settlement evidence.

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

Send that payload twice. Both acknowledgements must have the same vendor reference;
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
- Model-level append-only checks are not database-level immutable-ledger controls.
  A production adapter needs PostgreSQL constraints/triggers, transaction audit,
  retention, monitoring, reconciliation, and contention/load testing.

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
- GitHub publication was blocked pending explicit approval to publish the new
  code to the public `zitch-systems/zitch` repository. No remote CI run, PR, merge,
  deployment, or bank activation has occurred for this work.

## Acceptance gates (not completed)

- Production-safe customer/identity and provider-specific ledger design reviewed.
- Staging with separate database/cache/credentials approved and deployed explicitly.
- Wema validation completed, settlement account profiled, live prefix assigned.
- Outbound specification and reconciliation/reversal contract received and tested.
- Fees, limits, KYC policy, and compliance approval confirmed.
- App/WhatsApp rollout, reconciliation ownership, in-flight rollback tested.
- Owner explicitly approves migration/activation. No production migration or
  activation is authorized by this preparation branch.

Sources reviewed: the supplied Account Lookup, Transaction Notification, Mini
Statement, KYC Details, Block Account, Transaction Search, VAS Onboarding and
Fintech Hub PDF documents. Source PDFs and private Slack messages are not copied
into this repository.
