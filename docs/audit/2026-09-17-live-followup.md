# Zitch live follow-up — 17 September 2026

## Migration blocker

**Production still routes to Oregon. Frankfurt is not ready for cutover.**

At 17:56 UTC, a tagged GET to `https://api.zitch.ng/readyz` returned 200 and appeared only in the Oregon API's logs (`srv-d9mvmjvlk1mc73dgcr5g`). Recent Meta Flow requests also appeared there, not on either Frankfurt API.

At 18:00 UTC the two regions reported different local ledgers against the same bank accounts:

| Check | Oregon ledger | Frankfurt ledger | Bank balance |
|---|---:|---:|---:|
| User 29 | ₦125.00 | ₦351.00 | ₦186.25 |
| User 30 | ₦1,000.00 | ₦1,000.00 | ₦989.25 |

The ₦226 difference for user 29 equals the four illustrated purchases/transfers (₦57 + ₦56 + ₦56 + ₦57). This is consistent with Frankfurt retaining an earlier copy while production continued in Oregon. Exact transaction provenance still needs database comparison; arithmetic alone is not proof of a restore boundary.

Do not change DNS, delete Oregon, overwrite a database, or enable Frankfurt payment processing until a controlled final sync is planned. Do not edit customer balances to make reconciliation green. No financial adjustment or new migration was performed in this follow-up.

## Observed live resources

| Resource | ID | Finding |
|---|---|---|
| Oregon API `zitch-api` | `srv-d9mvmjvlk1mc73dgcr5g` | Canonical API and Flow requests still arrive here. |
| Frankfurt API `zitch-api-frankfurt` | `srv-dalc6re5vjqs73es23q0` | Original Frankfurt API; onrender hostname remains `zitch-api-ry6y.onrender.com`. |
| Frankfurt API `zitch-api-ry6y` | `srv-daluk3tbedkc738c87jg` | Additional API; hostname `zitch-api-ry6y-k83g.onrender.com`. Do not identify it by its display name alone. |
| Frankfurt WhatsApp worker | `srv-dalv4dtbedkc738dtofg` | Deploy marked live, but repeatedly exits: `Production WhatsApp worker requires WHATSAPP_MODE=live`. |
| Oregon WhatsApp worker | `srv-da3is31t0dsc73fpksug` | Still active; no matching startup/runtime error in the checked post-release window. |
| Frankfurt Wema reconcile | `crn-dalvugvf3r2c73dv0cig` | Scheduled runs succeed, but WhatsApp alerts fail with channel unavailable. |
| Oregon Wema reconcile | `crn-d9mvmevlk1mc73dgckb0` | Still active and processing the same bank account set. |
| Frankfurt Postgres | `dpg-dalc6du5vjqs73es0n0g-a` | Available; 15 GB; autoscaling enabled; no HA. |
| Oregon Postgres | `dpg-d8eo7njeo5us73cn6ki0-a` | Available; 1 GB; autoscaling enabled; no HA. |
| Frankfurt cache `zitch-cache-ry6y` | `red-daldqlm7bikc73fvbf80` | Available; noeviction; persistence journal/snapshot. |
| Frankfurt cache `zitch-cache-frankfurt` | `red-daldq62d0e5s73f2467g` | Additional cache; persistence off. Actual consumer bindings require environment inspection. |
| Oregon cache | `red-d9mvmenlk1mc73dgck3g` | Available; allkeys_lru; persistence off. |

All 19 banking API/worker/cron resources had successfully built/deployed `d727eb88dc95fa842abe9d6776f0e38aa1c4ad49`. This is not proof all processes stay running: the Frankfurt worker failure demonstrates the distinction. The separate Meta connector is still on its 31 August revision, with auto-deploy off.

Neither `zitch-reconcile-vtu` nor the one-time migration cron appeared in the current service inventory. No duplicate API or database was created during this audit.

## Cron outcomes

- Frankfurt/Oregon Wema reconciliation both ran. Frankfurt cannot send its WhatsApp alert leg, and one bank-account recovery remains pending.
- The 18:00 balance checks failed for real ledger/bank divergence in both regions; Frankfurt additionally reports the stale user-29 balance.
- Oregon's daily settlement report failed because the pool balance was unreadable. A partial asset total must not be treated as a reconciled position.
- Session purge, integrity and AML had successful daily runs. Frankfurt's early integrity run checked zero wallets, before the later data copy; that old result does not certify the restored ledger.
- Frankfurt maturities and settlement report had no run in the inspected period; a successful build is not a successful scheduled run.

The read-only Postgres connector failed with TLS/EOF errors. The six historical refunds, exact discrepancy cause and proof provenance therefore remain unverified. Network restrictions were not weakened to obtain access.

## Configuration work required before cutover

Inspect Render's **Environment** page on the authoritative Oregon API and each intended Frankfurt consumer. Copy values inside Render, not into chat. Do not copy Oregon database/cache URLs into Frankfurt.

| Variables | Where / rule |
|---|---|
| `DATABASE_URL`, `REDIS_URL` | Verify each Frankfurt API, worker and relevant cron points to the same intended Frankfurt database/cache. Preserve Oregon as the current authoritative production source until final sync. |
| `DJANGO_SECRET_KEY`, `DJANGO_KYC_HASH_KEY`, `DJANGO_OTP_HASH_KEY`, `WHATSAPP_QUEUE_KEY`, MFA encryption keys | Preserve established values through restore; changing identity hashes/encryption keys can make migrated data unusable. |
| `WHATSAPP_MODE`, `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_BUSINESS_NUMBER` | Required on the intended Frankfurt API and worker. Enable live processing only after database alignment. Setting mode alone is not a substitute for missing credentials. |
| `WHATSAPP_FLOW_ID`, private key/passphrase, result-screen setting | API/worker must use the published, verified Flow and matching decryption key. Do not switch to an unpublished Flow. |
| `TXN_ALERTS_WHATSAPP`, WhatsApp mode/token/phone ID/base URL/template name/language | Required on reconcile/maturities processes that deliver transaction alerts. Retain intentional notification settings; verify source values before adding mandatory Blueprint references. |
| Resend and Termii credentials/senders | Align wherever sign-in OTPs or transaction alerts are sent. Bank NIN/BVN SMS is a separate service. |
| `WHATSAPP_WORKER_RECONCILE=false` | Keep dedicated reconciliation in the cron. Do not enable overlapping money polling just to compensate for missing notification credentials. |
| Wema channel, wallet key, pilot base URL, source account, security/callback configuration | Align API/worker/reconciliation consumers. Blank supported airtime/account-management overrides fall back to the approved wallet subscription. Account-upgrade entitlement still requires confirmation. |
| Bank face callback | Temi approved the exact Oregon onrender callback. Route/allowlist coordination is required; renaming a service does not update the bank registration. |

The Render plugin cannot read existing environment values, manage custom-domain cutover, or suspend old services. Dashboard sign-in was attempted through secure credential entry; GitHub returned “Incorrect username or password.” Credentials were not requested in chat or printed.

## Safe final migration order

1. Confirm the authoritative source database and intended single Frankfurt API/worker/cache by resource IDs and connection settings.
2. Verify the Frankfurt notification/provider configuration without enabling a second payment-processing installation.
3. Schedule a write pause: stop all sources of mutations, including API/Flow requests, workers, money crons and relevant callback handling. Preserve provider retries and unresolved transaction references.
4. Take a fresh source backup after the write pause; use the reviewed migration procedure to restore the intended Frankfurt target. Previous overwrite approval is not a reason to overwrite a new unknown target.
5. Compare ledger counts/totals, latest references, users, ownership evidence and unresolved transactions. Restore the same application hash/encryption keys. Resolve discrepancies with authenticated bank history and documented adjustments, not guessed balances.
6. Route the canonical domain and bank/Meta callbacks consistently. Retire old money consumers so only the authoritative installation processes work.
7. Resume, verify controlled real journeys and scheduled runs, then retain Oregon backup for the agreed rollback window. Do not roll back to a stale database after new Frankfurt writes.

## Release and publication evidence

The d727 release passed GitHub CI: 2,446 backend tests, 153 app tests, TypeScript, ESLint, iOS export and CodeQL. Android build also passed and uploaded `zitch-release-apk` (artifact 10512105244, run 35251749008). That is a test-signed installable APK, not an app-store release or proof that existing phones updated.

Meta credentials can read the configured account/number. Published Flow `1781880196163772` remains the 8 September “Zitch payment PIN 2” version. The connector health endpoint explicitly reports `readOnly: true`, 13 exposed tools. This explains the missing create/publish operation; redeploying identical code does not grant write access. Configuration writes need deliberate authorization or publication through WhatsApp Manager; no access control was bypassed.

The exact current Prembly browser-liveness contract is still unavailable. Tier-2 browser liveness must not be represented as completed. Bank-issued NIN OTP delivery still needs a real controlled handset test against the NIN-registered phone.

## Follow-up software corrections

- Face-start responses that return an existing bank OTP attempt now continue in the matching BVN/NIN form. Add-money sends NIN attempts to KYC instead of feeding NIN tracking to BVN confirmation/resend. Existing verification flags are retained.
- NIN document and face adapters reject failed, pending and malformed provider envelopes, including truthy strings. A liveness flag cannot override a failed provider envelope. This does not establish the unavailable live provider contract.
- Residential address fields reject control characters and overlong values before contacting the bank. The exact address being verified must fit the stored address; no silent truncation is accepted.
- Balance reconciliation with either failure gate now exits unsuccessfully for unreadable bank balances as well as the configured divergence. Existing financial discrepancies are not suppressed.
- WhatsApp menu 13 supports Fixed Save creation, rates, owned plan details, history and maturity payouts; menu 14 supports viewing and repaying an existing loan. Amount/term selection stays in chat and debit authorization uses the encrypted PIN Flow. These routes reuse the app's atomic ledger services and durable idempotency keys.
- Product execution rechecks account/link ownership, freeze/PIN state, expiry, quoted terms and current balance. Credential/link changes invalidate an unexecuted quote. Replayed completed actions cannot debit again. No new borrowing, early withdrawal or invented fees are added.

Regression coverage includes failed provider envelopes, invalid addresses, cross-identity OTP handoff, incomplete bank reads, expired/cancelled actions, changed credentials/quotes/balances, loan ownership, repayment races and duplicate execution.

Local release validation passed: **2,481 backend tests**, **155 app tests and 1 snapshot**, TypeScript, and ESLint with zero errors. Existing lint warnings and the Jest teardown warning remain; the new add-money screen test passes lint without warnings. `git diff --check` also passed. CI and deployment completion for this revision are checked separately after publication. Mocked tests do not certify a handset receiving a bank OTP or the migration's data integrity.
