# Zitch VAS validation handoff

Status: preparation pack, not a completed live-credentials submission or evidence
of bank acceptance. The base collections application is on `main` in PR #530;
the pilot/WhatsApp release preparation is merged in PR #531. Public access,
credentials and provisioned sample accounts still need verification.

## Submission fields

Wema's Step 4 requires the production HTTPS service to use prefix `711`
temporarily for validation. Zitch's `validation` mode implements that stage; it
does not turn the simulated receipt balance into spendable customer money.

| Field | Value or remaining action |
| --- | --- |
| Base URL | `https://api.zitch.ng` — configured target; verify reachability after deployment |
| Authentication key | Dedicated static Bearer token; generate and configure through secret management; not included in this repository |
| Account Lookup | `POST https://api.zitch.ng/vas/account-lookup` |
| Transaction Notification | `POST https://api.zitch.ng/vas/transaction-notification` |
| Fetch Mini Statement | `POST https://api.zitch.ng/vas/mini-statement` |
| Get KYC Details | `POST https://api.zitch.ng/vas/kyc-details` |
| Block Account | `POST https://api.zitch.ng/vas/block-account` |
| Three sample accounts | Pending: three distinct, active, provisioned 10-digit numbers beginning `711` |
| Account type | Static |
| Service update notification email | Pending: confirm a monitored Zitch group mailbox |

Recipient from Wema's onboarding instructions: `sqa@wemabank.com`, copying
`TechPartnership@wemabank.com`. Subject: **Virtual Account Service Live Credentials
for Zitch**. Do not send this incomplete table as credentials. Deliver the actual
token to the verified bank recipients through the agreed confidential channel;
do not commit it or insert it into a shared test report. Nothing in this pack
sends email or Slack messages.

Generate the final non-secret JSON using `vas_onboarding_package` only after the
three accounts exist and the service is correctly configured. The command checks
active users, matching verified identity evidence and validation-account
isolation. Separately verify the public lookups and bank reachability. See the
[rollout runbook](wema-vas-rollout.md).

## Postman pack

- [Collection](postman/wema-vas.postman_collection.json): seven requests covering
  all five APIs, including lookup of each submission account.
- [Blank environment](postman/wema-vas.postman_environment.json): contains no
  token, real identity, sample account number or preselected live host.

Import both files into a current Postman client supporting
[`pm.execution.skipRequest`](https://learning.postman.com/v11/docs/tests-and-scripts/write-scripts/postman-sandbox-reference/pm-execution).
Disable automatic redirect following in the request/client settings; the bank
routes must accept POST directly. Keep environment secrets local and do not
publish response examples containing BVN, NIN or mobile numbers.

1. Deploy the backend with `WEMA_VAS_MODE=validation`, prefix `711`, enrollment
   disabled, the dedicated token and encryption keys. Verify the database,
   public HTTPS origin and authentication before sharing the package.
2. Provision three dedicated verified and consented users with
   `vas_provision_validation`. Do not substitute made-up numbers or historical
   Partnership accounts. Enter the deployed origin, token and three numbers
   locally in the Postman environment. The default files send no requests.
3. Run the three lookups. The first lookup saves only the returned account name
   for the notification payload. It does not copy BVN/NIN into variables. Confirm
   each name and active state; read the starting KYC balance and mini statement.
4. For a synthetic notification, set a unique `session_id`, a unique
   `payment_reference` and a current ISO timestamp in `created_at`. Set
   `allow_validation_notification=true` only for the agreed validation test.
   The fixed `1.00` amount and all-zero source account are synthetic API data,
   not instructions to send money. Re-send the exact same request with unchanged
   references and timestamp to verify the original acknowledgment is returned.
5. Re-read KYC and the statement. Verify one simulated increase of `1.00`, then
   capture internal evidence that the spendable wallet, production ledger and
   customer alert outbox have not changed. HTTP assertions alone cannot prove
   this internal isolation or bank settlement.
6. Leave `allow_validation_block=false` for the three submission accounts. A
   restriction test needs a **fourth disposable, provisioned `711` account** in
   `block_test_account`, with separate agreement to restrict it. Only then set
   `allow_validation_block=true`. There is no unblock endpoint. This prevents
   our own tests from invalidating the three active accounts supplied to Wema.
7. Reset both permission variables to `false` after the agreed tests. Export
   only the original blank environment for sharing. Lookup response identities
   and actual secret values must not appear in general acceptance reports.

The supplied Postman assertions cover successful endpoint responses and an
unchanged replay. Negative cases below are deliberate variations and will fail
the successful-response assertions; record their expected results separately.
This collection is not a performance or concurrency test and must not be used
as a live funding or bank-settlement client.

## Acceptance evidence

| Check | Expected result |
| --- | --- |
| Authenticated POST JSON on all five routes | No redirect; JSON response with `Cache-Control: no-store` |
| Missing/wrong token | HTTP `401`; no account data returned |
| Authenticated non-POST / wrong content type | HTTP `405` / `415` |
| Each active sample account lookup | HTTP `200`, `status: "00"`, `status_desc: "Okay"`, exact `Zitch/` legal name and verified BVN or NIN |
| Fresh validation account before notification | KYC simulated balance `0.00`; empty statement |
| First valid notification | HTTP `200`, `status: "00"`, durable `transactionreference`; one validation receipt |
| Same notification repeated, including concurrent delivery | Same acknowledgment; no second receipt or increase |
| Same session with a changed amount or payment reference | HTTP `409`; no additional receipt/balance change |
| Same session sent to another provisioned account with that account's exact lookup name | HTTP `409`; no additional receipt/balance change. An unknown account instead returns `07`; a name mismatch returns HTTP `400`. |
| Same payment reference with a new session | HTTP `409`; no partial increase |
| Invalid decimal amount, timestamp or recipient name | HTTP `400`; no financial changes |
| KYC after accepted test receipts | Simulated balance equals accepted amounts exactly once |
| Mini statement | VAS movements from latest movement date plus preceding nine Lagos calendar dates; validation accounts have simulated credits only; live approved bills add reserved debits and once-only refund credits; no Partnership or held receipts |
| Block fourth account, then repeat | `{"message":"Account Restricted Successfully"}`; original reason/time retained |
| Lookup after block | `status: "07"`, `status_desc: "Inactive Account"`; KYC/history remain readable |
| Previously successful notification repeated after block | Original successful acknowledgment retained |
| New notification after block, then repeat | HTTP `503`, `status: "07"`, `status_desc: "Funds held for review"`; one held receipt, no balance increase |

Use an evidence record with deployment commit, timestamp, masked account, test
case, session/reference, expected result, observed HTTP/status and reviewer.
Attach internal receipt/ledger/outbox counts without identities or credentials.
Backend automated tests already exercise concurrency and rollback. Deployed
acceptance and bank-led settlement are distinct evidence still to be collected.

Clarifications to agree with Wema:

- Amounts and timestamps are normalized for replay comparison. Narration is
  validated but excluded from the replay fingerprint; a narration-only change
  is accepted as a replay.
- Static-account statements include credits and approved bill debits/refunds.
  Confirm empty destination fields for utility bills, pending-reservation treatment
  and the document's inconsistent `transactionDate`/`transactiondate` casing before
  enabling VAS-funded bills. More than 5,000 rows in the statement window returns
  `503`; there is no assumed pagination contract.
- A new notification on a restricted account is retained as held evidence.
  Confirm the bank's retry, resolution and re-push procedure; no automatic
  unblock, refund or conversion of held receipts is implemented.

## Remaining integration work

| Work | Dependency and current boundary |
| --- | --- |
| Publish and validate the five bank endpoints | Hosting restored and baseline API internally healthy; verify current configuration and authenticated public reachability under the maintenance plan |
| Complete Step 4 submission | Actual configured token, three provisioned active `711` accounts and monitored group email |
| Controlled real-money pilot | Implemented per-user pilot restriction; activation still requires Wema's endpoint acceptance, assigned live prefix, profiled collection account and written pilot authorization |
| Collection reconciliation | Bank balance/statement access, Transaction Search URL/authentication and agreed retry/re-push/settlement rules |
| Outgoing transfers | Separate payout initiation, idempotency and status/reversal contracts; inbound VAS docs do not define these |
| Retained bill payments | Independent bill lifecycle implemented; VAS use requires confirmed collection-account debit permission and statement mapping; existing bill API is retained |
| Prembly identity | App and encrypted WhatsApp lookup/SMS ownership paths implemented; verify live Prembly credentials and registered-phone SMS delivery |
| Existing customer migration | Bank closure/conversion instructions, reviewed cutover evidence and resolution of existing balances/pending work |
| Fully in-WhatsApp enrollment | Encrypted consent, identity and SMS recovery screens implemented; publish/verify the new Flow before enabling its gate |
| General customer launch | Real settlement evidence, reconciliation, product/payment readiness and tested app/WhatsApp release |

The controlled pilot must occur **after switching to Wema's assigned live
prefix**, with ordinary customers still excluded. Set `WEMA_VAS_RELEASE_PHASE=pilot`
and the explicit `WEMA_VAS_PILOT_USER_IDS` allowlist; the old enable flag alone
cannot open enrollment. Missing or malformed release settings close enrollment.
Do not enable general enrollment merely to create test users, promote `711`
accounts to live accounts, or manually manufacture KYC evidence.

Read-only local readiness is available through `vas_preflight`, the staff
diagnostics page and operator-authenticated `/vas-preflight`. It keeps local
requirements separate from bank acceptance and never reports full go-live from
configuration alone.

Operational recheck on 6 October 2026: billing restoration resumed the Frankfurt
services, PostgreSQL and cache. Initial Python builds failed while PostgreSQL was
still unavailable. A controlled API retry deployed `f2ff3f2` at 11:14 UTC, applied
the pending migrations and returned internal readiness `200`. Public maintenance
remained enabled. Other Python consumers still require release/configuration
verification; the Meta service deployed the same baseline but remains under
maintenance. No newer VAS technical answers were found in the accessible Slack
channels/DMs as of 11:08 UTC. Wema's [24 September reply](https://wemabankteam.slack.com/archives/C0BDAQF7U56/p1790250918782509)
says outbound documentation follows endpoint submission.

The 13 supplied PDFs were reviewed against the implementation, including Search,
fraud controls and onboarding. The Notion root, Search and onboarding pages were
inaccessible during this recheck; the supplied v2.0 copies are the available
contract evidence. They do not provide collection-funded bill permission, Search
production credentials or an outward payout contract. Search snapshot comparison
is implemented as a read-only support tool, not automated bank reconciliation.

Source contract: Wema's [Third Party Virtual Account API documentation, v2.0](https://wemabank-doc.notion.site/Wema-Bank-Third-Party-Virtual-Account-API-Integration-Documentation-31f13df490b68074aa99df46b1de9a4f),
and the [5 October prefix/endpoint instruction](https://wemabankteam.slack.com/archives/C0C6PTZ4SK0/p1791197691968529).
