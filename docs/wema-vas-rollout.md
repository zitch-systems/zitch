# Wema Virtual Account Service rollout

## Scope and release status

The integrated Django application in `backend/wema_vas` implements the five
vendor-hosted bank APIs, durable receipt processing, verified identity enrollment,
app/WhatsApp funding instructions and Partnership archive controls. It reuses the
existing PostgreSQL ledger and durable transaction-alert outbox. No new permanent
web service, database or cache is needed. `incubator/wema_vas` is archived as an
isolated prototype.

This is a **collections implementation awaiting bank validation**, not approval
to launch a complete banking product. Partnership account creation and transfers
are archived independently of the retained bill-payment service. Existing bills
keep their established provider contract and customer funding checks. VAS-funded
bills require separate bank approval for debiting the profiled collection account;
they cannot fall back to an old customer NUBAN or the legacy pool. Outgoing VAS
transfers and automatic bank reconciliation remain closed without their contracts.

The implementation follows Wema's [Third Party Virtual Account API documentation](https://wemabank-doc.notion.site/Wema-Bank-Third-Party-Virtual-Account-API-Integration-Documentation-31f13df490b68074aa99df46b1de9a4f),
version 2.0. Static accounts are selected. Prefix `711` is for bank validation;
only Wema's assigned non-711 prefix may be used for real collections.

The [validation handoff pack](wema-vas-handoff.md) contains the Step 4 field
mapping, blank Postman collection/environment, acceptance evidence and remaining
bank questions. It is not a completed credentials submission.

## Contracts

Each bank route accepts POST JSON and a dedicated static Bearer token. A trailing
slash is optional and never redirects the POST. These tokens are independent of
customer session tokens and old Partnership APIM keys.

| Endpoint | Implemented behavior |
| --- | --- |
| `/vas/account-lookup` | Exact 10-digit account lookup, `Zitch/` legal name, encrypted verified BVN/NIN, active/invalid responses |
| `/vas/transaction-notification` | Validated decimal strings; atomic ledger credit, receipt and alert outbox; exact duplicates return the same acknowledgment; conflicting references reject |
| `/vas/mini-statement` | VAS credits, reserved bill debits and once-only refund credits across ten Lagos calendar dates anchored to latest movement; no historical Partnership or held credits |
| `/vas/kyc-details` | Verified identity and VAS-only posted balance, including inactive accounts |
| `/vas/block-account` | Serialized restriction preserving original reason and timestamp; no new spendable credit or spend |
| `/api/wallet/vas/enroll/` | Customer-authenticated POST with existing verified `bvn` or `nin` and literal `consent: true` |
| `/api/wallet/vas/status/` | Customer-authenticated GET or POST; no sensitive identifiers |

`711` notifications create validation receipts and simulated balances only. They
never create customer ledger credits or alerts. Repeating a valid prior credit
after an account block still returns its original successful acknowledgment.
New notifications to a blocked account are retained as immutable held evidence
and return non-success/retry; they require bank-assisted resolution. No unblock
or held-receipt conversion API is assumed.

## Identity and existing accounts

Enrollment requires an active user, verified phone, exact identifier/hash match,
trusted durable identity proof and provider-confirmed legal name. Profile-name
changes cannot rename a bank account. Earlier proof records without a legal-name
snapshot must complete provider lookup plus registered-phone SMS ownership again;
they are never backfilled from mutable profile names. The app provides that path.
Set `KYC_PROVIDER=prembly` on every Django runtime and retain the configured
Prembly API key and any existing application ID. The current BVN/NIN Advance
products require `x-api-key`; their identity-only credential check does not require
an application ID. Other Prembly products retain their existing credential gates.
VAS identity requires a live Prembly lookup
and SMS to the identity-registered phone; it never falls back to Partnership or
mock proof. The app accepts this ownership challenge without a bank tracking ID.
Historical tracked bank OTP completions remain supported.

Identity responses require a successful HTTP result, literal `status: true`,
`response_code: "00"` and a valid holder record. Not-found/blocked results reject
verification; provider downtime, insufficient provider funds and malformed results
are outages, never identity proof. See Prembly's [BVN Advance](https://docs.prembly.com/reference/bvn-advance)
and [response-code contract](https://docs.prembly.com/docs/prembly-api-response-codes-verification-status).

Explicit enrollment consent records encrypted storage and disclosure to Wema.
Raw IDs are encrypted with a separate rotating Fernet keyring; current keyed
identity hashes and KYC flags are preserved. The current API and WhatsApp worker
must share the keyring. Old keys remain until all ciphertext is rotated and
verified; do not rotate the existing KYC hash key. Request bodies and local
variables are excluded from Sentry capture.

WhatsApp reads the same funding-account state as the app and hides legacy,
validation and restricted funding details. VAS enrollment uses a dedicated
encrypted consent/identity Flow; raw identity is never requested in the chat
thread. The new screens remain disabled until the configured approved Flow ID,
published status and complete published JSON contract match the deployed asset.
Missing provider-confirmed name proof requires a provider lookup and SMS ownership
challenge inside the Flow, followed by fresh private identity re-entry. No raw
identifier is retained between those pages. Ordinary WhatsApp payment/login
screens continue to use their existing contract while this gate is off.

Existing non-zero balances or pending transactions block enrollment. A legacy
bank number additionally requires a per-user immutable `MigrationApproval` with
bank cutover evidence and reviewer reference. `vas_approve_cutover` records this
evidence only after checking the exact retained account and zero balance. It
does not contact Wema, settle pending transactions, reset balances or manufacture
approval. Obtain the bank's actual closure/migration instructions first.

Old account fields, ledger rows, callbacks, OTP completions and reconciliation
history remain available. Late Partnership credits are retained in the aggregate
historical wallet but excluded from Wema VAS balance/statement responses. No
customer balance is copied to a new bank account.

## Deployment sequence

1. Finish hosting restoration and backup/restore verification using
   `frankfurt-billing-restoration-2026-10-03.md`. Keep maintenance enabled and
   consumers held until the documented restoration checks pass. Never resume
   the retained old-region database as a second writer.
2. Deploy code and all migrations with defaults: `BANK_ACCOUNT_PROVIDER=partnership`,
   `WEMA_PARTNERSHIP_MODE=active`, `WEMA_VAS_ENABLED=false`, enrollment false.
   Verify schema, PostgreSQL constraints, readiness and shared cache.
3. Set `WEMA_PARTNERSHIP_MODE=archive` consistently on API, worker and all crons
   when customer maintenance is active. This archives new account/transfer business while
   preserving outstanding settlement and historical evidence. Retain legacy
   credentials while old work still needs requery. Do not delete them as cleanup.
   Keep `WEMA_BILLER_MODE=active` to preserve bills and `KYC_PROVIDER=prembly` for
   identity. Keep VAS collection-funded bills disabled until their funding approval
   and bank statement mapping are verified as described below.
4. Stage a distinct strong random token and Fernet keyring through secret
   management. Enable VAS validation with prefix `711`. Enable trusted proxy
   handling only behind the controlled TLS-terminating proxy. Ensure the canonical
   API host, edge rules and origin guard allow the five authenticated routes.
5. Provision three dedicated, verified and consented validation users with no
   legacy account or balance. `vas_provision_validation --user-id … --identity-type
   bvn --consent-reference …` reads the matching ID from a hidden prompt; never
   pass IDs as arguments. Do not reuse these users for production; account mode
   and ownership are immutable.
6. Generate the non-secret submission with `vas_onboarding_package --base-url
   https://api.zitch.ng --service-email <approved-address> --account <711-account-1>
   --account <711-account-2> --account <711-account-3>`. Deliver the bank token
   separately through an approved secure channel. The command does not send email
   or Slack messages. It prints the three sample account numbers but never the
   token, BVN, NIN or customer user IDs.
7. Wema validates all five endpoints, profiles the collection account and supplies
   the live prefix. Retain approval evidence. The `711` stage proves the API
   contract only; it cannot prove real inflow or collection settlement.
8. Select `BANK_ACCOUNT_PROVIDER=wema_vas`, live mode and the assigned prefix only
   after sign-off. Configure `WEMA_VAS_LIVE_APPROVAL_REFERENCE` and
   `WEMA_VAS_COLLECTION_ACCOUNT`; leave `WEMA_VAS_ENABLE_ENROLLMENT=false` and
   `WEMA_VAS_RELEASE_PHASE=closed`. Apply the same configuration to every Django
   runtime. For a bank-approved controlled pilot, set `WEMA_VAS_RELEASE_PHASE=pilot`,
   `WEMA_VAS_PILOT_USER_IDS` to the explicit internal IDs of the approved users,
   and only then enable enrollment. Empty, malformed or missing
   pilot settings admit nobody. The pilot still requires verified identity,
   consent, zero prior liabilities and genuine bank approval.
9. With the approved pilot restricted and the assigned live prefix in use,
   complete bank-led real inflow, notification and collection settlement tests.
   General enrollment remains closed until reconciliation and product launch
   gates are satisfied. Opening `WEMA_VAS_RELEASE_PHASE=general` additionally
   requires a separate `WEMA_VAS_GENERAL_APPROVAL_REFERENCE`; setting a reference
   is not evidence that external bank verification actually occurred. Never reuse or convert
   immutable `711` validation users and accounts for live collections.

## Operator readiness and shared configuration

After migrations, the Render build runs `vas_deployment_diagnostics`. Its single
redacted report includes credential-presence booleans, provider selections and
local readiness. It prints no keys, identity values, collection account or approval
reference. It does not contact providers or establish bank acceptance.

`python manage.py vas_preflight --stage validation` performs read-only, redacted
configuration, schema, migration, immutable-trigger and sample-account checks.
It succeeds only for local readiness to submit the `711` service to Wema. Use
`--stage controlled-live-pilot` for the pilot inspection; external bank evidence
remains pending and the command cannot authorize a public release. No command
calls a bank or creates users, accounts, balances or messages.

The same report is available in the staff diagnostics page at
`/admin/diagnostics/`, and via operator-authenticated GET
`/vas-preflight?stage=validation` or `?stage=controlled-live-pilot`. The HTTP
endpoint accepts the diagnostic Bearer token, not Wema's VAS token; no token or
account identifiers go in the query string. A validation HTTP `200` means only
local submission readiness. A pilot inspection remains HTTP `503` while external
acceptance is unverified, even when its local checks pass.

Both Render Blueprints now declare the VAS settings on the API and inherit them
across all Django consumers. Flow identity/private-key configuration and the two
new enrollment flags are shared between API and WhatsApp worker. The Frankfurt
candidate's **DO NOT MANUALLY SYNC** warning still applies: first reconcile its
resource mapping with the existing services using the billing-restoration
runbook. Do not create duplicate services or writers by applying either file.
Before any approved Blueprint sync, copy/verify the existing Flow values on the
API and retain the
matching keys: replacing a valid worker key with an empty API value would break
existing Flows. `sync: false` keeps release settings operator-owned; it does not
populate missing settings or prove consistency in already deployed services.
Recheck effective values per runtime during the protected restoration sequence.

WhatsApp enrollment needs `WHATSAPP_FLOW_VAS_ENROLLMENT_ENABLED=true` and
`WHATSAPP_FLOW_VAS_APPROVED_FLOW_ID` matching the published `WHATSAPP_FLOW_ID`.
Upload and validate a draft containing the new screens before publishing; do
not assume a previously published Flow is editable. Verify the live endpoint,
screen contract and encryption before selecting the new published ID. Keep the
gate false while the Meta connector or publication verification is unavailable.

To pause a pilot, close enrollment or remove pilot membership. This hides
customer funding instructions, but preserves the five bank endpoints and their
receipt handling for deposits already sent. Never use a token rotation, prefix
change, disabled endpoint or service shutdown as an enrollment pause.

Both provider selection and archive mode reject new Partnership account/transfer initiation;
neither reroutes VAS spending to the old products. Never flip an enrolled customer
back to the old rail as an automatic fallback. After live receipts exist, preserve
the VAS bank endpoints and ledger when rolling back customer UI. Stopping a receipt
endpoint after acknowledgment is not a financial rollback.

## Retained bill payments

`WEMA_BILLER_MODE=active` controls bills separately from the archived account and
transfer products. Legacy bills require the customer's own retained NUBAN, cleared
history review, identity checks and applicable daily limits. Historical queries,
callbacks and refunds remain available when new purchases are disabled.

For an approved live VAS customer, configure `WEMA_VAS_BILLER_ENABLED=true`,
`WEMA_VAS_BILLER_SOURCE_ACCOUNT` equal to `WEMA_VAS_COLLECTION_ACCOUNT`, and a genuine
`WEMA_VAS_BILLER_APPROVAL_REFERENCE`. Apply them consistently to every Django
runtime only after Wema confirms the retained bill API may debit that account.
An active non-711 account, rollout eligibility and adequate canonical VAS funds
remain mandatory. These configuration fields do not manufacture bank approval.

Each new bill has an immutable funding binding. Pending debits reserve funds;
failure releases the reservation once, alongside the existing wallet refund.
Neither mutable transaction metadata nor late historical credits authorize VAS
spending. Callback authorization checks the binding and current account restriction.
Remita remains unavailable where its existing status/requery contract is absent.

Customer balances distinguish the aggregate wallet total from `available_balance`,
`historical_balance` and `vas_balance`. Late Partnership credits remain visible in
the total/history but do not increase the VAS amount available for bills. Restricted
or disabled VAS spending has zero available balance while retaining the recorded
funds. App and WhatsApp bill decisions use that available amount.

Static accounts require credit and debit statements. Utility products do not
provide destination account numbers, so bill debit/refund rows have empty
`accountNo` and `bankName` rather than invented destinations. Agree this mapping,
pending-reservation treatment and the document's inconsistent date-field casing
with Wema before enabling collection-funded bills.

## Inbound Search evidence

`vas_reconcile_snapshot --snapshot <file> --session-id <session>` (or `--account`)
compares an existing bank Search JSON export with local receipts and ledger
bindings. It is read-only, scopes every response row, omits account numbers,
uses keyed session-reference pseudonyms and
never treats a supplied file as authenticated or complete settlement evidence.
It flags missing notifications, held receipts, conflicts and bank uncertainty.
Non-`00` NIBSS outcomes require Wema support; they never trigger an automatic
refund. Production Search URL/authentication and pagination remain unavailable.

## Outstanding bank and operations evidence

- Live prefix, collection account profiling and five-endpoint acceptance.
- Production Transaction Search URL/authentication, access and retry/response
  semantics. Pure request builders/classification exist, but make no network calls.
- Collection-bank balance/statement access, fee and settlement rules, reconciliation
  ownership, evidence of a real end-to-end credit and failure/re-push procedure.
- Payout initiation, idempotency, status enquiry and reversal contracts. Inbound
  Search is never treated as outward TSQ. Retained bills additionally need bank
  confirmation of collection-account debit funding and statement mapping for VAS.
- Existing-account closure/conversion and balance migration instructions.
- KYC operational acceptance, live SMS/liveness evidence and reviewed legal names.
- Hosting restored, background consumers controlled, backups tested, deployment and
  signed mobile builds verified against the same release.

`reconcile_balances` excludes migrated wallets from obsolete per-NUBAN comparisons
and flags collection reconciliation as incomplete. `settlement_report` exposes
known legacy assets but refuses to state a complete solvency position when VAS
bank assets are unverified. Both strict command gates fail in that state. Internal
VAS receipts are never substituted for bank-held assets. No automatic balance
correction is made.

## Hosting decision

Django is the application framework; Render is the hosting platform. Keep Django
and reuse the existing Render services for this bank migration. A separate host
migration would add database, secret, TLS, callback and worker changes at the same
time as a financial integration change. Billing was restored on 6 October 2026;
the API deployed `f2ff3f2` at 11:14 UTC, applied its migrations and passed internal
readiness. Public maintenance and runtime configuration still require separate
verification before bank validation; a healthy API is not proof of launch readiness.

If DigitalOcean is considered later, a 2 GB VM, weekly backup and basic managed
PostgreSQL provide a lower infrastructure floor (about $31.70/month with 20 GB
database storage and local cache, before taxes/other usage), but require operating
system patching, process supervision, monitoring, restore drills and separate
availability planning. This single-VM option is not highly available. Retire
duplicate retained databases only after verified export and restoration.

Pricing references: [Droplets](https://www.digitalocean.com/pricing/droplets),
[managed databases](https://www.digitalocean.com/pricing/managed-databases),
[backups](https://docs.digitalocean.com/products/backups/details/pricing/),
[Render pricing](https://render.com/pricing). Recheck rates before provisioning.
