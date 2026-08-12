# Zitch production-readiness audit

**Audit date:** 2026-08-01  
**Repository:** `zitch-systems/zitch`  
**Audit branch:** `codex/wema-production-readiness`  
**Prior workflow-only PR:** <https://github.com/zitch-systems/zitch/pull/233>
(merged before the verified product changes; superseded by the audit branch's current PR)

## Decision

**Controlled development E2E: GO after the external Wema gates below are supplied.**  
**Production / real-money release: HOLD.**

The application code now fails closed when bank configuration is absent or a
provider outcome is ambiguous. Automated Django and Expo regressions are green.
Real bank E2E cannot be honestly certified while the channel ID and wallet key are
intentionally removed, authenticated Wema portal documentation/test accounts are
unavailable, and the live infrastructure blockers in this report remain open.

## Scope and evidence

The audit covered the Django API, Expo client, data models/migrations, wallet ledger,
authentication and OTP, KYC, Wema wallet provisioning, bank payout and reconciliation,
Wema callbacks, airtime/data/bills, cards, savings, loans/BNPL, conversion, bank link,
business payments, risk/AML/compliance controls, operator/admin surfaces, WhatsApp,
logging/diagnostics, deployment configuration, dependency posture, and available live
infrastructure.

Evidence inspected:

- GitHub repository, branch protections/history, tests, migrations, deployment files,
  and draft PR.
- Render workspace and logs for `https://zitch-api.onrender.com`, its web service,
  absence of declared cron/worker/cache resources, and managed PostgreSQL instance.
- DigitalOcean account inventory.
- Private Slack channel `#zitch` in the Wema Bank Team workspace, including messages
  from Temi and the Wema integration team.
- Wema developer portal at
  <https://wema-alatdev-apimgt.developer.azure-api.net/>. The public signed-out API and
  product catalogs were empty; exact endpoint schemas still require an authenticated
  portal session.
- The Wema onboarding/callback/tier requirements supplied with this audit.

No secret value is reproduced in this report.

## High-impact findings fixed

| Area | Risk found | Remediation implemented |
|---|---|---|
| Wema payouts | Timeouts, malformed bodies, or unknown status text could be interpreted too optimistically and settle or refund money incorrectly. | Central strict outcome classifier: only explicit success settles, explicit terminal failure refunds, and every unknown/unreachable result remains pending for reconciliation. |
| Wema identity | Client-controlled OTP continuation data and reusable BVN/NIN values could bind a bank response to the wrong request/user. | Added server-bound `WemaProvisioningAttempt`, keyed hashes, expiry/idempotency, BVN/NIN ownership checks, database uniqueness constraints, and race-safe writes. Raw identifiers are not persisted in the attempt. |
| Account provisioning | A weak holder-name match or unavailable name enquiry could provision the wrong account. | Holder-name verification now fails closed and requires at least two shared normalized name tokens. |
| Wema callbacks | Callback trust depended too heavily on a URL token; malformed/oversized payloads and sensitive callback values could reach logs. | Added bounded bodies, strict callback schemas, quarantine behavior, identifier fingerprints/redaction, source-IP enforcement defaults, `securityInfo` matching, rotation checks, and authoritative status requery before settlement. |
| Diagnostic endpoints | Legacy diagnostic tokens, phone numbers, identity inputs, and callback secrets could appear in URLs, access logs, history, or responses. | Wema/SMS diagnostics are POST JSON, all diagnostic authentication uses bearer headers, responses are `no-store`, bodies are bounded, and callback diagnostics return templates without embedded secrets. |
| OTP bypass | A configured test OTP could affect password-reset behavior and production logs exposed raw phone values. | Test OTP is limited to the explicit signup test identity; reset OTP never uses it. Security logs use pseudonymous identifiers and production preflight rejects unsafe bypass settings. |
| Sensitive logging | Provider exceptions and nested payloads could expose phone, email, NUBAN, BVN/NIN, or credentials. | Added a global redacting formatter and provider-specific deep redaction/fingerprinting. Exceptions returned to clients are generic. |
| WhatsApp mode/config | Missing live credentials could silently fall back to mock behavior; sandbox/chat signup could remain enabled in production. | Added explicit `disabled`, `sandbox`, and `live` modes. Production rejects sandbox; live requires all Meta identifiers/secrets; chat signup and PIN collection are disabled. **Since 2026-08-12:** chat signup is enabled in production (`WHATSAPP_ALLOW_CHAT_SIGNUP` defaults true) — the finding that mattered was the PIN, and PIN collection in chat stays dev/test-only under its own separate guard. |
| WhatsApp webhook | Duplicate suppression could permanently discard a message after a worker crash, and metadata was not tightly bound to the configured number. | Added signature and phone-number-ID verification, 1 MiB body limit, durable processing leases, retry release, attempt/error timestamps, malformed event filtering, and monotonic delivery status handling. |
| WhatsApp execution | Production webhooks still performed provider/AI work inline, creating timeout and acknowledged-but-incomplete crash windows. | Added an encrypted Postgres inbound queue, leases/backoff/dead-letter privacy cleanup, a fail-fast worker command, and a paid Render worker/shared-cache topology. The webhook now commits before acknowledging. |
| WhatsApp broadcasts | A single operator could immediately fan out provider calls, with no durable outbox or safe treatment of ambiguous sends. | Added unconditional maker/checker approval, materialised recipients, row leases, 429 backoff, conservative `unknown` outcomes, monotonic Meta callbacks, and operator rollups. |
| WhatsApp privacy/AI | AI routing had no per-link consent and could send long numeric identifiers/email addresses to the model or durable logs. | AI is opt-in per link and existing links migrate to disabled. Model input and stored intents are privacy-filtered; chat logs redact long identifiers and emails. |
| WhatsApp client | Link codes/polling and provider failures were not bounded strongly enough. | Link codes now have 128-bit entropy, polling stops after ten minutes, rate limits are applied, and operator replies fail visibly when Meta delivery fails. |
| Expo KYC/funding | UI mixed Zitch product tiers with Wema bank tiers, allowed oversized image payloads, and echoed raw identity continuation fields. | Separated bank/product limits, capped/compressed KYC images, removed raw identity echoes, added load/retry states, bounded WhatsApp polling, and prevented native screen capture while preserving explicit receipt sharing. |
| Expo platform | Expo 54 retained a high-severity PostCSS build-chain advisory and lagged current React Native/platform support. | Upgraded sequentially through SDK 55/56/57, migrated removed navigation/config APIs, fixed React Compiler lint violations, and produced Android, iOS, and web production bundles. |
| Business payment link | A client-generated unsigned query-string payment link could imply server authorization it did not have. | Removed/disabled the unsafe link until a signed server-issued flow exists. |
| Render cron configuration | Cron processes inherited the web requirement for shared Redis and reconciliation jobs lacked explicit provider status settings. | Cron jobs explicitly disable the web-only shared-cache startup gate; reconciliation jobs declare the required Wema/VTU status configuration. |
| Render topology | The live web service is free, none of the declared cron/worker/cache services exists, and Postgres is publicly reachable. | Blueprint declares paid web/worker/cache/cron resources, checked deploys, shared secret references and an empty Postgres public allow-list. These remain live operator actions and were not applied automatically. |

## Wema requirement conformance

| Supplied requirement | Current state |
|---|---|
| `x-api-key` plus product subscription key | Implemented as `x-api-key`/`access` and `Ocp-Apim-Subscription-Key`, selected per Wema product. Missing required values make `wema_live()` false and fail preflight. |
| Account-creation callback before wallet creation | Four callback routes exist under `/webhooks/wema/`; account creation is guarded by server-bound attempts and callback reconciliation. Bank profiling is externally required. |
| Unique phone, email, NIN, and BVN | User phone/email uniqueness already exists; verified BVN/NIN hashes now have database-enforced cross-user uniqueness. Provisioning attempts are idempotent and do not resubmit an active identity. |
| Development OTP `123456` | The user-supplied Wema OTP is passed only against its server-held tracking attempt. No production application bypass is hardcoded. Safe dev validation still requires the bank test environment. |
| Facial biometric `correlationId` | Not certified. The exact authenticated portal contract and test fixture are still required before implementing/certifying this path. |
| Tier 1: BVN or NIN | Supported provisioning path and conservative Tier 1 limits. |
| Tier 2: BVN plus NIN | Limits are implemented; the exact bank upgrade/provisioning contract remains to be verified against the authenticated portal. |
| Tier 3: BVN, NIN, address | Limits and local KYC state exist; end-to-end bank/address-verification contract remains external. |
| Tier 1 limits | ₦50,000 single inflow, ₦30,000 daily spend, ₦300,000 cumulative balance implemented and tested. |
| Tier 2 limits | ₦100,000 single inflow, ₦100,000 daily spend, ₦500,000 cumulative balance implemented and tested. |
| Tier 3 limits | Bank tier is uncapped; Zitch's independent risk/product limits continue to apply. Unknown provisioned bank tier is conservatively treated as Tier 1. |
| Authentication callback | Required request/response shape is implemented. Authorization requires an owned, fresh, pending payout reference and configured `securityInfo`; failure and internal exceptions deny. |
| Transaction callback | Implemented with idempotency and cooldown. Payload status alone never moves money; the API re-queries Wema over the authenticated channel. |
| Transaction notification | Route exists. Production profiling and exact live notification schema remain external gates. |
| Source-IP restrictions | Defaults include the two Wema-provided egress addresses. The real Render proxy hop must be observed and configured before enforcement is considered verified. |

## Slack findings

The private Wema thread confirms:

- Development wallet creation can be simulated; the documented dev OTP is `123456`,
  and development NUBANs are samples that cannot support real transaction testing.
- Wema described `securityInfo` as a client-held value echoed with the transaction
  reference; the client responds with `authorized`.
- Account, authentication, and transaction callback URLs were reported profiled for
  development on 2026-07-28. Transaction notification is production-only.
- The bank supplied two callback egress IPs, now represented by secure defaults.
- Subscription access was reportedly obtained and a go-live/compliance request was
  discussed, but the thread does not prove production activation, issued channel ID,
  debit/credit test accounts, a successful bank transaction, or production profiling.
- Credential-like values were shared in chat. Treat them as exposed and rotate them
  before any production use; retain replacements only in the deployment secret store.

## Verification results

| Gate | Result |
|---|---|
| Django full regression | **885 tests passed** |
| Latest WhatsApp focused regression | **92 tests passed** |
| Django migration drift | **No changes detected** |
| Django system check | **Passed** |
| Django `check --deploy` with synthetic production settings | **Passed** |
| Expo TypeScript | **Passed** |
| Expo lint | **Passed, zero warnings** |
| Expo Jest | **49/49 passed; 1 snapshot passed** |
| Expo Doctor | **20/20 passed** |
| Expo production export | **Android, iOS and web bundles; 105 static routes** |
| Render YAML parse/semantic checks | **10 resources/services; 7 cron jobs; passed** |
| Python dependency audit | **No known vulnerabilities** |
| npm production dependency audit | **No high findings; one transitive moderate advisory with no upstream fix** |
| Secret-pattern scan of repository | **No credential-like material found** |
| Diff whitespace check | **Passed** |
| Wema production preflight with intentionally removed keys | **Correctly blocked real money: 4 hard gates** |

The Wema preflight failures are: incomplete Wema live keys/channel ID, missing
`WEMA_SECURITY_INFO`, missing/short `WEMA_CALLBACK_TOKEN`, and a sandbox Wema base
URL. This is expected for the current test configuration and proves the release is
fail-closed.

## Open release blockers

### P0 — must close before real-money production

1. Authenticate to the Wema developer portal and capture the exact current endpoint,
   request, response, status legend, Tier 2/3, biometric, and test-account contracts.
2. Rotate every Wema/diagnostic credential that was posted in Slack. Set the channel
   ID, subscription keys, `securityInfo`, a new callback token, and the correct
   environment base URL in Render secrets only.
3. Have Wema confirm development callback profiling, debit and credit test accounts,
   and their source IPs. Observe Render's actual forwarded chain and set the trusted
   proxy hop so IP enforcement resolves the bank—not an internal proxy.
4. Execute safe development wallet creation, authentication, debit, credit, status
   requery, reversal, duplicate callback, late callback, and notification scenarios.
5. Restrict the Render PostgreSQL public allowlist. It is currently `0.0.0.0/0` and
   the basic database has no HA. Establish private/internal access, backups, restore
   validation, pooling/capacity, and an availability plan before production.
6. Apply the reviewed paid web/worker/cache and reconciliation/monitoring cron topology.
   Until `zitch-reconcile-wema` exists, real deposits and pending payouts cannot settle
   reliably. Then validate concurrency, leases/timeouts, Redis/rate limits, alerting,
   and cron overlap under load.
7. Profile the four production callback URLs and run a production-readiness call with
   Wema before enabling any live credential.

### P1 — controlled follow-up

- Expo 57 removes the former high-severity PostCSS path. `npm audit --omit=dev` now
  reports only the moderate UUID buffer-bounds advisory through
  `expo-splash-screen -> @expo/config-plugins -> xcode -> uuid@7.0.3`; npm reports no
  fix. Do not force an incompatible override. Track the upstream Expo/Xcode update and
  reassess [GHSA-w5hq-g745-h8pq](https://github.com/advisories/GHSA-w5hq-g745-h8pq).
- Run Maestro/device E2E on signed Android and iOS builds. This environment verified
  application logic and Expo configuration but did not operate a physical device,
  receive a real SMS/WhatsApp message, or execute a bank transaction.
- Certify facial biometric and Tier 2/3 upgrade flows after obtaining the portal
  contracts; do not infer them from similarly named Wema products.

## Infrastructure disposition

- **Render:** the merged audit commit is live. No error-level logs appeared after that
  deploy in the inspected window. The paid worker/cache/crons/private-database Blueprint
  follow-up is not deployed; reconciliation absence, database exposure and service
  sizing remain blockers.
- **DigitalOcean:** one active 512 MiB droplet was present but not in Zitch's request
  path. It was not modified. It could only serve as a fixed-egress component after an
  explicit network design, hardening, redundancy, monitoring, and capacity review; the
  current single small droplet is not a production banking architecture.

## Controlled development E2E sequence

1. Restore rotated development-only Wema values in Render's secret store.
2. Set a strong callback token and `securityInfo`; keep simulation/test OTP features
   off except in an explicitly isolated pre-launch environment.
3. Run `python manage.py wema_preflight` and require zero hard gates.
4. Give Wema these templates, substituting the secret from the secret store without
   copying it into tickets or chat:
   - `/webhooks/wema/account/<WEMA_CALLBACK_TOKEN>`
   - `/webhooks/wema/authorize/<WEMA_CALLBACK_TOKEN>`
   - `/webhooks/wema/transaction/<WEMA_CALLBACK_TOKEN>`
   - `/webhooks/wema/notification/<WEMA_CALLBACK_TOKEN>` (production)
5. Verify callback IP resolution through Render before enabling the allowlist.
6. Use unique phone, email, BVN, and NIN test values for every wallet-creation case.
7. Execute the bank-provided debit/credit accounts through happy, timeout, duplicate,
   failed, reversed, and delayed-callback cases; reconcile every pending ledger item.
8. Run the signed mobile build and WhatsApp live-mode smoke tests with test recipients.
9. Preserve transaction references, redacted logs, callback fingerprints, and ledger
   reconciliation output as the formal E2E evidence pack.

Until steps 1–7 succeed, the correct release state is **NOT READY for real money**.
