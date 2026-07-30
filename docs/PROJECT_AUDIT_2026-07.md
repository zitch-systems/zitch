# Zitch end-to-end hardening audit — 25 July 2026

## Executive result

The repository is buildable and materially safer after this pass. The existing Codemagic `android-apk` workflow completed successfully on 25 July 2026 and produced an 89.66 MB `app-release.apk`. TypeScript, Jest, Expo prebuild, Expo static export, Django checks, migration drift checks, production deployment checks, and all backend tests pass.

This is not a certification that every external financial rail is correct in production. Live Wema, Mono, VTU.ng, Sendchamp, card-issuer, KYC, and FX behavior still requires controlled sandbox/production-candidate testing with provider credentials and contractual event samples.

## Changes in this PR

### Mobile/API and privacy

- Restrict release API overrides to the two approved HTTPS Zitch hosts; development overrides are limited to HTTPS or local/private LAN HTTP.
- Replace unbounded direct-fetch list/price calls with the shared bounded JSON request layer, including offline and stale-response handling.
- Validate server-provided WhatsApp links against official `wa.me` and `api.whatsapp.com` HTTPS hosts.
- Stop reading the clipboard automatically on Home; users now explicitly tap “Pay from clipboard”.
- Remove bearer-token duplication from MCP request bodies; tokens stay in the Authorization header.
- Disable Android app-data backup, block unused audio-recording/overlay permissions, and set Android `versionCode` to 10.

### Authentication and operator security

- Add per-account user-login lockout in addition to per-IP limits.
- Select client IPs from the configured trusted-proxy boundary instead of trusting the first spoofable X-Forwarded-For value.
- Mask email/phone identifiers in failed-login logs and refuse token issuance to inactive users.
- Require an explicit password for named operator provisioning.
- Make operator role updates replace stale groups/superuser state.
- Remove the production bypass that allowed seeding known demo operator credentials.

### WhatsApp transaction safety

- In production, never request a raw transaction PIN in WhatsApp chat.
- Prefer Meta Flow secure PIN entry, then SMS OTP; if neither secure channel is configured, block the action and direct the user to the app.
- Added regression coverage proving the pending action is cleared in this state.

### UI/UX and accessibility

- Make shared buttons, list rows, selectors, keypad keys, PIN controls, navigation tabs, alerts, onboarding, and sign-in actions expose meaningful roles, labels, selected/disabled states, and hints.
- Replace a visually-present but inert “+ Add money” control with real navigation.
- Improve async loaders so provider/catalog errors cannot leave service screens indefinitely busy.

## Verification performed

| Area | Result |
| --- | --- |
| TypeScript | `tsc --noEmit` passed |
| Mobile unit tests | 5 suites, 20 tests, 1 snapshot passed |
| Expo compatibility | SDK 51 dependency alignment passed in CI |
| Expo native generation | Android prebuild completed |
| Expo route bundle | 105 static routes exported; 2.08 MB web entry bundle |
| Browser flow | Onboarding, registration, sign-in, validation modal, links, and accessibility tree inspected |
| Django checks | `check`, `makemigrations --check --dry-run`, and `check --deploy` passed |
| Backend tests | 547 tests passed in 198.977s |
| Backend coverage | 79% across production Python code |
| Python advisories | `pip-audit`: no known vulnerabilities |
| Python source scan | Bandit: no high-severity production finding; one medium test-only temp-path finding |
| JavaScript advisories | 64 total: 5 critical, 33 high, 18 moderate, 8 low, primarily obsolete Expo/React Native build tooling |
| Secret patterns | No committed AWS, Google, GitHub, Stripe, OpenAI, or private-key pattern found |
| Codemagic | All configured steps passed; release APK produced in 4m55s |

## Residual risks and required follow-up

### P0 — before real-money production

1. **Validate Wema reversal event shapes.** The reconciler prevents double refunds when an inbound reversal contains the original payout reference. If a live Wema reversal omits that reference, it can be treated as funding and later also refunded by payout-status reconciliation. Capture real reversal/history samples and implement a provider-supported correlation key before production.
2. **Prove ledger-to-bank settlement.** Utility/card/FX rails and per-user Wema NUBAN balances must be reconciled as a two-rail accounting system. Establish an operational settlement model, daily bank-vs-ledger reports, ownership, and alert thresholds.
3. **Verify production simulation guards.** Confirm `TEST_OTP_PHONE`, `TEST_OTP_CODE`, `SIMULATE_DEPOSIT_TOKEN`, `WEMA_SIMULATION`, and `MONO_SIMULATION` are absent/off in every production environment.
4. **Configure real provider credentials.** The Codemagic YAML does not itself provide `EXPO_PUBLIC_MONO_PUBLIC_KEY`; without it the client shows the simulation UI and production bank linking fails closed. Confirm Codemagic environment configuration and test Mono, Wema, VTU, SMS/email OTP, KYC, cards, FX, and WhatsApp with approved accounts.

### P1 — before store/external distribution

1. **Upgrade Expo/React Native.** SDK 51 / React Native 0.74 is unsupported and accounts for the 64 npm advisories. The audit recommends a dedicated SDK 57 migration, not piecemeal forced transitive upgrades.
2. **Raise Android target API.** SDK 51 generates target API 34. Google Play requires API 36 for new apps/updates from 31 August 2026; complete the Expo upgrade and Android 16 behavior testing.
3. **Use production signing and AAB.** The current Codemagic preview APK is intentionally release-mode but debug-signed. It is suitable for internal sideload testing, not Play distribution.
4. **Add device automation.** Current mobile coverage is 20 unit tests plus browser/export inspection. Add Maestro or Detox flows for signup/OTP, sign-in/biometric fallback, add money, transfer, utility purchase, receipts, offline/retry, and session lock on at least Android 8/11/14/16-class devices.
5. **Add ESLint configuration and CI gate.** The package exposes `expo lint` but has no committed ESLint configuration.

### P2 — operational hardening

- Production rate limits and login locks need shared Redis; LocMem limits multiply across workers.
- Confirm the Wema and VTU reconciliation schedules actually run, alert on failure, and survive backend cold starts.
- Raise focused coverage for provider clients: Wema 61%, Mono 69%, generic providers 68%, VTU.ng 73%, WhatsApp provider 39%, WhatsApp views 65%.
- Verify Sentry/log-drain retention, database backups with restore drills, secret rotation, incident response, and operator audit review.
- Replace the hardcoded Codemagic notification recipient with a controlled team/distribution setting when ownership changes.

## Follow-up status — 30 July 2026

The residual-risk items above were triaged and the ones that did not require a third
party were implemented. Recorded here so the audit is not read as current.

**Closed**

| Item | Where |
|---|---|
| P1 Expo/React Native upgrade | SDK 54 / RN 0.81.5 (closed before this pass) |
| P1 ESLint config + CI gate | `eslint.config.js`, gated in CI and Codemagic |
| P1 Android target API 36 | pinned via expo-build-properties. **The SDK 54 upgrade did NOT achieve this** — `ExpoRootProjectPlugin.kt` defaults `targetSdk` to 35 |
| P1 production signing + AAB | `plugins/withAndroidReleaseSigning.js` + the `android-aab` Codemagic workflow, which reads the signer off the finished bundle and refuses a debug-signed one |
| P1 device automation | 10 Maestro flows + `npm run test:e2e`. **Not yet run on a device**, and nothing provisions the Android 8/11/14/16 matrix |
| P0 ledger-to-bank settlement | `manage.py settlement_report` + `zitch-settlement-report` cron + `docs/settlement-operating-model.md`. Two owner rows are deliberately unassigned |
| P2 shared Redis for rate limits | `REDIS_URL` is now a hard production requirement (`production_checks.py`) |
| Deferred: webhook forensic log | `whatsapp.WebhookEvent`, recording refused calls too |
| Deferred: device fingerprint + risk scoring | `lib/deviceIntegrity.ts`, `common/risk.py`, `accounts.KnownDevice` |
| Deferred: root/jailbreak detection, device binding | same |
| Deferred: admin MFA + maker/checker | `accounts/totp.py`, `common/approvals.py`, `docs/operator-controls.md` |
| Deferred: AML/SAR, GDPR export/delete, disputes | the `compliance` app + `docs/compliance-operations.md` |
| Website currency/IMTO claims | removed from both served landing pages and the store listing, with a regression test |

**Still open, and why**

* **P0 Wema items** — the live host and keys, the VAS status legend, the `cardKey`, and the
  reversal history shape. All need the bank. The legend is now an env var
  (`WEMA_VAS_STATUS_LEGEND`) so it lands without a deploy.

  **Callback profiling is further along than this audit records.** Reading the `#zitch`
  Slack channel on 30 July: the four URLs were sent on 2026-07-28 and Wema confirmed
  *"this has been profiled on dev"* the same day, with production profiling deferred to
  cutover by mutual agreement. Wema also granted **Compliance Approval on 2026-07-24** and
  asked their team to "push the Partner live" — an outcome nobody has reported back, so it
  is a follow-up to chase rather than a fresh ask. Note dev is a simulator, so this proves
  routing and authentication and nothing about payload shapes.
  `docs/wema-callback-profiling.md` now carries the real state and the follow-up message.

* **Two config changes on our side, unblocked and not yet made.** Wema supplied their
  gateway egress IPs in writing on 2026-07-27 (`135.236.18.76`, `74.178.162.156` — exactly
  the compiled-in defaults), and stated that allowlisting is the partner's job, so nothing
  on the bank side holds our addresses. The documented precondition for
  `WEMA_CALLBACK_ENFORCE_IPS=true` is therefore met and it is still off. Separately, the
  callback token was posted into a Slack channel in Wema's own workspace, and the same
  token covers dev and production — it should be rotated to a production-only value via
  `WEMA_CALLBACK_TOKEN_PREV` before cutover.
* **Termii sender ID / DND approval** — a carrier decision.
* **P0 production simulation guards and provider credentials** — Render dashboard
  configuration, unchanged by this pass.
* **Certificate pinning** — mechanism shipped, pins deliberately empty. Both API hosts'
  certificates are third-party managed and rotate with fresh keys; pinning them today
  schedules an outage with no remote fix. Needs a key we control.
* **PEP/sanctions screening** — no provider wired. Not simulated either.
* **Portal SPA screens** for the approval queue and the AML/dispute queues. Django admin
  is the interim surface.

## APK test checklist

1. Download `app-release.apk` from the successful Codemagic build’s **Artifacts** tab.
2. Install on a clean Android device, then repeat as an in-place upgrade from the previous build.
3. Test denied camera/biometric/photo permissions, no-network startup, slow API, background/foreground lock, and process death.
4. Use provider-approved low-value accounts only. Confirm every debit has exactly one terminal ledger result and reconcile it to provider evidence.
5. Do not promote this debug-signed APK to external users or Play; use configured release signing and an AAB for distribution.
