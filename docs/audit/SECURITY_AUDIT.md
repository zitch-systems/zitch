# Zitch — Security Audit (Client / Mobile)

**Scope:** the React Native / Expo client (`zitch/`). The Django backend (`backend/`) is referenced where the client depends on it but is not exhaustively audited here.
**Date:** 2026-06-28 · **Branch audited:** `new-design-files-v2`
**Method:** static review of the auth/session/storage/transport layer (`lib/`), config (`app.json`, `apiConfig`), and a secrets/cleartext scan of the source tree.

Per the agreed plan, **safe findings are remediated in place**; **risky items that need native modules + a store rebuild are documented here and slated for an isolated branch** (`security/native-hardening`), not merged into the working build.

---

## Executive summary

The client's application-security posture is **already strong**. Credentials live in the OS keychain/keystore, transport is HTTPS-enforced in release, sessions idle-lock and 401-evict, and money-moving requests carry idempotency keys. The **secrets and cleartext scans came back clean**.

The remaining gaps are **device-integrity / anti-tampering controls** (certificate pinning, root/jailbreak/emulator/debugger/tamper detection) that the OWASP MASVS-RESILIENCE tier expects of a payments app. All of these require native modules and a fresh binary, so they are scoped to the isolated branch.

| Severity | Open findings |
|---|---|
| Critical | 0 |
| High | 2 (cert pinning, root/jailbreak detection) — *isolated, native* |
| Medium | 3 (tamper/integrity attestation, token rotation, dep vulnerabilities) |
| Low | 3 (bearer mirrored into body, idempotency RNG, debugger detection) |

---

## What is already implemented well (verified)

| Control | Evidence | Notes |
|---|---|---|
| Access token in OS keychain | `lib/secureStore.ts` — `SecureStore.setItemAsync('access_token')`, AsyncStorage only on web (preview) | In-memory cache cleared on sign-out, never persisted to JS storage on native |
| Transaction PIN in keychain, biometric-gated | `lib/secureStore.ts` (`txn_pin`), `lib/biometrics.ts`, `components/design/ui.tsx` PinPad | Retrieval gated by OS biometric prompt |
| HTTPS enforced in release | `components/configFiles/apiConfig` — non-`__DEV__` `http://` is rewritten to `https://api.zitch.ng` | Defence-in-depth against plaintext token/PIN/BVN exposure |
| App Transport Security on | `app.json` ios `NSAllowsArbitraryLoads:false`, android `usesCleartextTraffic:false` | |
| 401 → session eviction | `lib/api.ts` `onSessionExpired()` clears session + routes to `/signin`, single-flight guarded | |
| Idle + background locking | `lib/session.ts` — 5-min idle lock, 1-min background re-lock, external-activity grace for camera/picker | Locks (keeps token for biometric unlock) vs. full clear on explicit logout |
| Anti-double-charge | `lib/api.ts` `newIdempotencyKey()` on money-moving requests | Server-side dedupe of retries/double-taps |
| Request timeouts | `lib/api.ts` `AbortController` 30s default | No hung screens; bounded blast radius on a slow upstream |
| No hardcoded secrets | repo-wide grep — only `*.env.example` tracked | |

---

## Findings & remediation

### SEC-01 · No TLS certificate pinning — **High** · *isolated (native)*
- **Affected:** all network calls (`lib/api.ts` → `fetch`), transport config (`app.json`).
- **Root cause:** the app trusts the device trust store; a user-installed or malicious CA enables MITM of the bearer token, PIN and BVN/NIN.
- **Fix (branch `security/native-hardening`):** add `expo-build-properties` + pin `api.zitch.ng` leaf/intermediate SPKI hashes (Android `network_security_config.xml`, iOS `NSPinnedDomains`), or adopt `react-native-ssl-pinning`. Ship two pins (current + backup) to survive rotation.
- **Why isolated:** changes native config and requires a new binary; cannot be build-verified from this environment.
- **Remaining risk after fix:** pin mismatch on cert rotation → mitigated by backup pin + a remote kill-switch.

### SEC-02 · No root / jailbreak detection — **High** · *isolated (native)*
- **Affected:** app launch / session bootstrap (`app/_layout.tsx`).
- **Root cause:** the app runs unmodified on rooted/jailbroken devices where the keychain and screen can be compromised.
- **Fix:** integrate `freeRASP` (jscramble) or `jail-monkey`; on detection, degrade (block money movement) or warn, and signal the backend for risk scoring.
- **Remaining risk:** advanced root-hiding (Magisk DenyList) — pair with server-side Play Integrity / DeviceCheck (SEC-03).

### SEC-03 · No app-integrity / tamper attestation — **Medium** · *isolated (native + backend)*
- **Root cause:** no Play Integrity (Android) / DeviceCheck/App Attest (iOS); a repackaged APK or hooked (Frida) process is undetected.
- **Fix:** request an integrity token at sign-in / before high-value transfers and verify it server-side in `accounts`/`transfers`. Requires native module + backend verification endpoint.

### SEC-04 · Single access token, no rotation / refresh — **Medium** · *backend-coordinated*
- **Affected:** `lib/secureStore.ts`, `lib/api.ts`, backend `accounts`/`zitch_api`.
- **Root cause:** one long-lived access token; the idle lock is the only client-side bound. No refresh-token rotation, so a stolen token is valid until server expiry/revocation.
- **Fix:** issue short-lived access + rotating refresh tokens; add a silent-refresh path in `apiPost` on 401-with-refresh; bind refresh tokens to a device id. Client + backend change — stage after the backend supports it.

### SEC-05 · Dependency vulnerabilities — **Medium** · *document, do not auto-fix*
- **Evidence:** `npm audit` reports 64 advisories (4 critical / 31 high / 21 moderate / 8 low), predominantly transitive under the Expo SDK 51 toolchain.
- **Why not auto-fixed:** Expo pins compatible native module versions; a blind `npm audit fix --force` routinely breaks an SDK-51 project. 
- **Fix (planned):** triage with `npm audit --production` (runtime-only), bump only Expo-blessed versions via `npx expo install --fix`, and add Dependabot/Snyk gated on CI (Phase 6). Tracked in REMEDIATION_PLAN.md.

### SEC-06 · Bearer token mirrored into request body — **Low** · *safe to change after migration*
- **Affected:** `lib/api.ts` — `{ access_token: token, ...body }`.
- **Root cause:** backwards-compat while screens migrate off body-auth; tokens in bodies are more likely to land in server/access logs than headers.
- **Fix:** once all endpoints accept the `Authorization` header, drop the body mirror. Left in place now to avoid breaking endpoints still reading `access_token`.

### SEC-07 · Idempotency key uses `Math.random` — **Low**
- **Affected:** `lib/api.ts` `newIdempotencyKey()`.
- **Assessment:** acceptable — the key is a dedupe nonce, not a secret. If you later want unguessable keys, switch to `expo-crypto` `randomUUID()`.

### SEC-08 · No debugger detection — **Low** · *isolated (native)*
- Bundled with SEC-02's RASP integration; low priority for a release build (JS debugging is disabled in production bundles).

---

## Out-of-scope but recommended (backend)
The following belong to `backend/` and a fintech compliance pass (Phase 9), noted for traceability: server-side velocity/fraud limits, transaction signing, audit-log immutability, PCI-DSS scope for card data (`cards` app), and NDPA data-handling for BVN/NIN. These are **not** client fixes.

---

## Disposition

- **Remediated in place:** none required — the safe client controls in this audit's "implemented well" table already exist; this pass **verified** them and corrected the dependency-fix approach (no unsafe `audit fix`).
- **Isolated to `security/native-hardening` branch (do not merge until build-tested):** SEC-01, SEC-02, SEC-03, SEC-08.
- **Backend-coordinated:** SEC-04, and the out-of-scope backend items.
- **Tracked for CI:** SEC-05 (Dependabot/Snyk).

See `REMEDIATION_PLAN.md` for sequencing and `SECURITY_FIXES.md` for the per-change log.
