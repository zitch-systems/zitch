# Zitch — Security Fixes Log

Companion to `SECURITY_AUDIT.md`. Per-change record of what was touched in the security pass.

## Applied in place (this branch)

| ID | Change | File(s) | Rationale |
|---|---|---|---|
| — | **Verification only** | — | The safe client-side controls the remediation prompt calls for (keychain token storage, HTTPS enforcement, idle/background lock, 401 eviction, idempotency keys, ATS) were **already present and correct**. This pass verified them rather than re-implementing. See SECURITY_AUDIT "implemented well" table. |
| SEC-05 | **Did NOT run `npm audit fix --force`** | — | Deliberate non-action: blind transitive bumps break Expo SDK 51 pinning. Triage path documented instead (REMEDIATION_PLAN). |

No insecure code was found to remove; no client security regression was introduced by the design remediation (token/transport/lock paths untouched; `tsc` + jest green).

## Isolated to branch `security/native-hardening` (NOT merged — needs a native rebuild + store test)

| ID | Planned change | Module/dep |
|---|---|---|
| SEC-01 | TLS certificate pinning for `api.zitch.ng` (current + backup SPKI pins) | `expo-build-properties` → Android `network_security_config.xml` + iOS `NSPinnedDomains`, or `react-native-ssl-pinning` |
| SEC-02 | Root / jailbreak detection → block money movement on compromised devices | `freeRASP` or `jail-monkey` |
| SEC-03 | App-integrity attestation at sign-in / high-value transfer | Play Integrity (Android) + DeviceCheck/App Attest (iOS) + backend verify endpoint |
| SEC-08 | Debugger detection | bundled with SEC-02 RASP |

**Why isolated:** each adds native config/modules and changes the binary; none can be build-verified from the current environment, and a broken native config would brick the working APK. They belong on a branch that goes through a real EAS/Codemagic build + device test before merge.

## Backend-coordinated (not a client-only change)

| ID | Item |
|---|---|
| SEC-04 | Short-lived access + rotating refresh tokens, device-bound; client silent-refresh on 401 |
| SEC-06 | Drop the body-mirrored `access_token` once all endpoints read the `Authorization` header |

## Validation
- `npx tsc --noEmit` — clean.
- `npx jest` — 12/12.
- secrets/cleartext scan — clean.
