# Zitch platform review — 17 September 2026

This is a code and evidence review, not a certification that every live service or financial balance is correct. Work starts from main `d2f5e393f7125bb1f13b8a56e458091b9eeca823`. The preceding three-week history contains 422 commits; related current implementations and regression suites were checked, not every historical commit replayed in production.

## Changes and evidence

| Area | Finding | Implemented correction |
|---|---|---|
| WhatsApp existing account | Reply 2 required an installed app and Settings → Link WhatsApp. | Secure sign-in on the registered WhatsApp number using verified email OTP plus the existing PIN. No API bearer token is issued. |
| Login revocation | An unfinished sign-in could outlive a credential change. | Bind it to current phone, email, password and PIN; apply shared PIN lockouts, preserve privacy preferences and revoke old unsubmitted forms. |
| Idle reauthentication | Shared payment screens could label an identity unlock as Pending and expose balance before reauthentication. | Neutral identity result; unlock carries no payment status pointer or pre-auth balance. Source Flow labels are neutral. |
| Payment result | Retapping after asynchronous completion could show an expired/failed result. | Requery the action's bound ledger status; retain Pending until a terminal bank result exists. Never turn an unknown outcome into success or promise an unconfirmed refund. |
| Payment lifecycle | Chat cancel/menu could erase a PIN-authorised queued action. | Preserve authorised money actions until the executor records an outcome; reject misleading cancellation after authorisation. |
| Settlement alerts | A retry sweep and settlement signal could send the same message; a failure after a Pending response could miss its reversal notice. | Claim notification delivery atomically, use a separate reversal claim, retry rejected sends and preserve pending metadata under a row lock. External delivery ambiguity or a process crash still needs operational reconciliation. |
| Receipt time | Financial messages displayed UTC without a timezone label. | Render WhatsApp, SMS and email receipt timestamps in Africa/Lagos with a WAT label. |
| Face selection | Selecting face verification could be redirected back to SMS. | The chosen BVN/NIN face path now opens the bank's bound face session. It does not by itself grant Tier 2 liveness. |
| BVN/NIN OTP | KYC routes could use a lookup path instead of the bank's bound OTP workflow. | Start/confirm routes share ownership-bound provisioning attempts, bank tracking reference, expiry and holder matching. Missing tracking IDs cannot claim OTP success. |
| Tier rules | NIN-only proof did not earn Tier 1; pending attempts plus an account number could restore verified identity. | Tier 1 accepts either proven BVN or NIN with verified contacts; Tier 2 requires both identities and live face; unfinished attempts cannot grant verification. |
| Identity proof | Proof recovery could bind evidence to a different saved hash. | Restore only compatible durable proof; do not leave an in-memory verified state after a uniqueness rejection. |
| Face callback trust | An existing bank account or duplicate-account response could be mistaken for proof of a browser-supplied correlation. | Require authenticated provider validation; account existence alone cannot attest face ownership. |
| Liveness | Face similarity or truthy string values could be interpreted as liveness. | Require explicit successful provider liveness evidence; unexpected schemas fail closed. Live provider contract confirmation is still outstanding. |
| Address/Tier 3 | Structured residential address data could be lost and acceptance confused with completion. | Shared structured-address service, Tier 2 prerequisites, and separate pending response. Bank tier changes only after confirmed completion. |
| WhatsApp address | Address verification required the app. | Expiring PIN-protected browser page opened from WhatsApp, scoped to the linked user, with CSRF, revocation, payload limits and one-time submission. |
| App build | Restyled components no longer provided interfaces used by existing screens; native dependencies did not match Expo SDK 51. | Restore real compatible historical UI implementations; align dependencies and KYC responses. New native dependencies require rebuilding the app. |
| App verification forms | BVN tracking was not carried into confirmation; initial NIN was hidden and address fields were incomplete. | Preserve OTP tracking and resend, offer either initial identity, retain structured address fields, and check authoritative verification flags before a success toast. |
| Reconciliation | Local/test lock release was missing; lock backend failure could look like a successful cron run. | Release locks in finally; preserve PostgreSQL advisory locking and make inability to acquire a lock backend visible as a command error. |
| Deployment | Cross-service secrets/cache could drift; optional environment references can block Blueprint sync. | Align required shared configuration while keeping optional overrides optional. Runtime wiring remains to be verified. |

## Validation

Local validation before release is recorded below. The subsequent GitHub Actions
run is the authoritative full-suite result for the pushed revision.

- App TypeScript: passed.
- Full root ESLint: passed, 0 errors and 37 warnings. Archived design handoff prototypes are excluded; shipped code and build scripts remain checked.
- App Jest: 22 suites, 153 tests and 1 snapshot passed. Jest reported a worker teardown warning, with exit status 0.
- Expo iOS export: passed. Earlier web export produced 119 routes and 4 bundles.
- Django system check and migration drift check: passed; no schema migration required.
- Complete backend run: 2,444 tests exercised; one obsolete chooser expectation failed. The test was corrected to select SMS before testing failed secure-form delivery; all 3 privacy fallback tests then passed.
- Final provider pending-response refinement: 147 focused tests passed. The broader KYC/ownership/callback regression gate passed 353 tests before that refinement.
- Alert/credit notification gate: 37 tests passed. Sign-in, secure address page and submitted-action security gate: 80 tests passed.
- Blueprint static checks: 10 services; no duplicate environment keys or references to undeclared service variables. Static validation does not verify runtime secret values or a successful Blueprint sync.
- Latest release checks: [CI and APK workflows](https://github.com/zitch-systems/zitch/actions). Remote completion is reported separately after the push; the report does not label an unfinished run successful.

Mocked provider tests validate software decisions and request contracts. They do not prove SMS delivery, provider entitlement, balances, external settlement, or an installed mobile build.

## Evidence from the bank and attachments

The supplied Wema Questions document and Temi's relevant #zitch messages were reviewed. Slack VAS guidance was excluded as requested; the approved shared wallet subscription is the source for wallet/airtime/data when no explicit product override is configured.

- [ALAT wallet services](https://playground.alat.ng/product-wallet-services) documents wallet operations, notification and account management.
- [ALAT face authentication](https://playground.alat.ng/api-account-creation-face-biometric-authentication) documents BVN/NIN selection, the browser key, callback URI and correlation identifier.
- [ALAT transaction notification](https://playground.alat.ng/api-transaction-notification) documents notification acknowledgment. Notifications prompt authenticated bank-history verification; their amount is not sufficient accounting evidence.
- [Temi's callback confirmation](https://wemabankteam.slack.com/archives/C0BDAQF7U56/p1789381309470919) concerns the exact face callback URL, not blanket certification of every deployment setting.
- [Temi's NIN OTP guidance](https://wemabankteam.slack.com/archives/C0BDAQF7U56/p1787839321568029): the destination is the line registered against NIN, which may differ from the Zitch login phone. Repeatedly resending cannot change that destination.

The user-supplied pilot hosts are `https://lagos-alat-blueapi.azure-api.net/` and `https://face-verification-pilot.azurewebsites.net/`. They must be paired with bank-approved credentials and the exact profiled callback. Public portal examples using another environment are not a reason to silently switch this tenant's host.

The requested `/get-started` page could not be retrieved. Prembly's current liveness API documentation could not be retrieved either. No claim is made to have accessed every portal page or verified an unavailable schema.

The screenshots show successful airtime and transfer notifications and an older duplicated debit alert. They do not prove both were processed in Frankfurt, confirm the underlying ledger entries, or verify the six historical refunds and the ₦10.75 discrepancy.

## Frankfurt release and runtime verification

Do not repeat the database migration or delete an API/database on the basis of its name. Service IDs, region, domain routes, active traffic and database connections must establish which service is authoritative.

1. Confirm the Render workspace `My Workspace` (`tea-d8entvernols73agg0rg`) for the connector. Its selection operation explicitly requires the user's confirmation. Until selected, no live Render health/cron/database audit is certified.
2. Inventory actual Frankfurt API, worker, cache, Postgres and cron IDs. Confirm API domain and Meta Flow endpoint route to the intended API. Check the database host and Redis host on each process without exposing their credentials.
3. Confirm the migration job cannot run again. Retain the Oregon database until reconciliation and rollback requirements are satisfied; do not run an overwrite merely because a prior approval exists.
4. Confirm `DJANGO_SECRET_KEY`, `DJANGO_KYC_HASH_KEY`, `DJANGO_OTP_HASH_KEY`, queue encryption keys and any MFA encryption keys match the established production values. Do not generate replacement identity hash keys after a restore.
5. Check all active money consumers share the authoritative database. Keep worker reconciliation off when the dedicated reconciliation cron is enabled; PostgreSQL advisory locks are the second guard.
6. Check scheduled runs, not only successful cron builds. Inspect Wema reconciliation, balances, settlement, maturities, session purge, integrity and AML run results; remove the retired `reconcile_vtu` job only if it actually still exists.
7. Check billing, backups, database storage autoscaling and connection health. Old screenshots showed a failed payment banner; its current resolution is unverified.
8. Compare historical refunds and discrepancy directly against ledger and authenticated bank history. No balance adjustments were performed in this audit.
9. Verify deployed commit and public health/preflight output. Keep sandbox/pilot readiness findings visible; do not suppress a guard solely to produce a green badge.
10. Audit existing verified identity flags against durable ownership evidence. Blocking the old pending-account/duplicate-response paths prevents new false approvals; it does not retroactively prove the provenance of every previously verified account.

Temi's recorded face callback allowlist confirmation names `https://zitch-api-zxdx.onrender.com/webhooks/wema/face`. Check that the runtime-generated callback matches the exact approved URL and reaches the authoritative Frankfurt database. Neither a renamed service nor a different canonical domain automatically updates the bank's allowlist.

### Environment alignment

| Setting | Destination / rule |
|---|---|
| `DATABASE_URL`, `REDIS_URL` | Each relevant Frankfurt process uses Frankfurt resources. Copy credentials only inside Render; do not paste values into chat. |
| `WEMA_CHANNEL_ID`, `WEMA_WALLET_KEY`, `WEMA_BASE_URL` | Shared approved tenant configuration for API, worker and money/reconciliation crons. |
| `WEMA_AIRTIME_KEY`, `WEMA_ACCT_MGT_KEY`, `WEMA_ACCT_MGT_BASE_URL` | Optional overrides. Blank uses supported wallet-key/global-host fallback. If a separate override is used, copy it consistently to every consumer that needs it. |
| `WEMA_UPGRADE_KEY`, optional `WEMA_UPGRADE_BASE_URL` | Account-upgrade product entitlement; wallet-key fallback must not be invented for this separate product. |
| `WEMA_FACE_VERIFY_URL`, `WEMA_FACE_CB_MODE`, `WEMA_FACE_CALLBACK_IPS` | Approved pilot face host and authenticated callback configuration. Browser Origin is not identity proof. |
| `ZITCH_API_BASE` | Canonical public API origin, also used by secure verification URLs and face callback generation. It must agree with the bank's exact callback registration. |
| `RESEND_API_KEY`, sender settings | Required for real existing-account login email OTP. |
| `TERMII_API_KEY`, approved sender settings | Zitch phone/signup/reset OTPs; separate from bank-issued NIN/BVN OTPs. |
| `WHATSAPP_FLOW_ID` and Flow key/settings | Point to a verified published Flow. Do not change the ID until JSON, routing, endpoint and encryption are validated. |
| `PREMBLY_API_KEY`, `PREMBLY_APP_ID`, base URL | Liveness/document provider configuration; verify actual accepted endpoint/response schema before declaring live Tier 2 complete. |

## WhatsApp publication and remaining product gaps

The read-only Meta connector confirmed published Flow `1781880196163772`, 25 reachable screens, no validation errors, and endpoint `https://api.zitch.ng/webhooks/whatsapp/flow`. The current published name is “Zitch payment PIN 2”. Existing screen IDs support the new email-code/PIN sign-in without adding routes.

Neutral titles in the repository require a new Meta Flow publication. The connector advertises creation tools, but the attempted new draft returned `Tool create_whatsapp_flow not found`. No draft was created and no publication was completed. Fix/update the connector deployment or publish the reviewed JSON through WhatsApp Manager, validate it, then update the runtime Flow ID. Existing published Flows cannot be edited in place.

Tier 2 browser liveness is not complete. The bank-hosted Tier 1 identity face page is not a substitute for the documented combined Tier 2 identity/live-image check. The code does not fabricate liveness or mark a pending upgrade successful. All activities cannot yet be described as WhatsApp-only: loan repayment and savings creation/management still have app-only routes. Disabled signup or missing secure confirmation services also prevent completion in WhatsApp; their security gates must not be bypassed. These remain product work, not a completed migration item.

Native app code changes are not delivered to installed devices merely by deploying Django. The Android workflow must build the new revision; an installable test APK is distinct from a production-signed store release. Live handset verification remains necessary for camera permissions, bank-hosted face completion, OTP receipt, address submission and WhatsApp handoffs.
