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

## APK test checklist

1. Download `app-release.apk` from the successful Codemagic build’s **Artifacts** tab.
2. Install on a clean Android device, then repeat as an in-place upgrade from the previous build.
3. Test denied camera/biometric/photo permissions, no-network startup, slow API, background/foreground lock, and process death.
4. Use provider-approved low-value accounts only. Confirm every debit has exactly one terminal ledger result and reconcile it to provider evidence.
5. Do not promote this debug-signed APK to external users or Play; use configured release signing and an AAB for distribution.
