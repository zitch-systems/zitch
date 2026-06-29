# Zitch APK — Critical Security Audit (Mobile Client)

> **Scope:** the Zitch Android app ("the APK") — the Expo SDK 51 / React Native 0.74.3
> client in `app/`, `lib/`, `components/`, `constants/`, `hooks/`, plus build/release config
> (`app.json`, `eas.json`, `codemagic.yaml`, `.eas/`, `package.json`). The Django backend
> (`backend/`) was read **only** to determine whether a client weakness is mitigated server‑side.
> **Target threat model:** a malicious user on a rooted/instrumented device, a thief with an
> unlocked or lost phone, a network MITM, and a hostile app co‑installed on the device.
>
> **Method:** 10 parallel review dimensions, then **every** finding adversarially
> re‑verified against the actual code (and backend) by an independent agent that was asked to
> *refute* it. 41 raw findings → 41 survived verification (24 confirmed as‑stated, 17 with
> severity/scope adjusted; 0 false positives). Severities below are the **post‑verification**
> values.
>
> This complements the existing `ZITCH_AUDIT_REPORT.md`, which focused on the **backend and
> admin portals**. This report covers the **client/APK surface**, which that audit did not.

---

## Executive summary

The single most important finding is a **positive** one, and it bounds the severity of
everything else: **the backend independently re‑enforces every money‑critical control.** The
transaction PIN is hashed and re‑verified server‑side on every spend with a row‑locked 5‑attempt
lockout (`backend/common/http.py:225-236`, `backend/accounts/models.py:45-46`); balances, daily
limits and idempotency are enforced in the database (`backend/wallet/services.py`); large
transfers require a durable server‑side `face_verified` gate (`backend/common/http.py:45-65`).
Because of this, **no client‑side weakness here lets an attacker move another user's money
remotely or exceed server limits.** The client checks are UX, and the server is the real gate.

What remains are real risks against the **device‑compromise, lost‑phone, and privacy** threat
models — and for a fintech holding card PANs, money PINs and BVN/NIN, several of them matter:

| # | Severity | Theme | One‑line |
|---|----------|-------|----------|
| A | **HIGH** | PIN / biometric "pay" at rest | The money PIN sits in the keystore with **no OS auth binding**; the biometric gate is a hookable JS boolean, the device passcode can substitute for the fingerprint, and the PIN is read into JS on every keypad mount. |
| B | **HIGH** | Screen‑capture exposure | **No `FLAG_SECURE` / screen‑capture protection anywhere** — card PAN+CVV, the PIN pad, balances and BVN/NIN are captured by screenshots, screen‑recording apps, and the Android recents thumbnail. |
| C | **MEDIUM** | Session retention | A **24h non‑sliding bearer token + the money PIN stay on the device** through the idle lock; the lock is a plaintext flag and biometric unlock does no server round‑trip. |
| D | **MEDIUM** | Transport | **No TLS certificate/SPKI pinning** on money, card‑reveal and KYC traffic; plus several smaller transport‑hygiene gaps. |
| E | **MEDIUM→LOW** | Build / platform hardening | A **debug‑key‑signed preview APK is wired to the production API**; no root/Frida/Play‑Integrity attestation; no obfuscation; EOL Expo SDK. |
| F | **LOW** | Permissions / privacy | **Unused `ACCESS_FINE_LOCATION`** (NDPR), `allowBackup` not disabled, KYC images left in cache, sensitive clipboard writes, clipboard auto‑read on launch. |
| G | **LOW / INFO** | Money‑flow correctness | Loan request has no idempotency key; receipts show client‑cached prices / a hardcoded ₦0 fee. |
| H | **INFO** | Dependency hygiene | Dead `react-native-vector-icons`, legacy `nativewind` v2, web‑only WebAuthn enroll‑as‑auth. |

**Severity distribution (post‑verification): 4 High · 8 Medium · 22 Low · 7 Info.**

**Fix these first:** (1) bind the keystore PIN/token to OS auth (`requireAuthentication`),
(2) set `disableDeviceFallback: true` on money‑approval prompts, (3) add `expo-screen-capture` /
`FLAG_SECURE` to card/PIN/KYC screens, (4) drop the unused `FINE_LOCATION` permission, and
(5) give the preview/CI APK a non‑production API URL. Items 1–2 and 4–5 are small, high‑leverage
changes.

---

## What the client already does right

Credit where due — these are not accidental, and they are why the residual severity is bounded:

- **Idempotency is implemented correctly** on spends: a per‑authorization key is generated
  once via a `useRef` and **reused across retries**, then cleared on success
  (`app/(servicesscreen)/sendmoney.tsx:151-186`, same pattern in airtime/data/cable/electricity/
  betting/exams/fixedsave/cards/loan‑repay). No double‑spend from double‑tap.
- **Token & PIN live in `expo-secure-store`** (Android Keystore), not AsyncStorage
  (`lib/secureStore.ts:30,64`); AsyncStorage holds only non‑secret state.
- **HTTPS is enforced**: release builds rewrite any `http://` to HTTPS
  (`components/configFiles/apiConfig.tsx:15-16`), `usesCleartextTraffic:false` and iOS ATS
  `NSAllowsArbitraryLoads:false` (`app.json:20-22,38`).
- **No secrets in the bundle**: the only `EXPO_PUBLIC_*` var is the API base URL; `.env.example`
  explicitly warns against putting secrets in `EXPO_PUBLIC_`.
- **Every request is bounded** by an `AbortController` timeout, and non‑JSON/offline responses
  degrade to a uniform shape (`lib/api.ts:26-72`).
- **Defense‑in‑depth on large transfers**: a client biometric step‑up *plus* a server‑side
  `face_required` gate (`app/(servicesscreen)/sendmoney.tsx:160-178`).
- **No `console.*` logging** of secrets anywhere in `app/`, `lib/`, `components/`, and **no
  in‑app `react-native-webview`** (external links go through the system browser).

---

## A — Transaction PIN & biometric "pay" model  ·  **HIGH**

This is the highest‑stakes client surface, and four verified findings converge on one root cause:
**the money PIN's protection depends on JavaScript control flow, not on the OS keystore.**

### A1. Money PIN (and access token) stored with no OS auth binding — **HIGH**
`lib/secureStore.ts:24-31,62-70`

`saveTransactionPin`/`getTransactionPin` and `saveToken`/`getToken` call
`SecureStore.setItemAsync`/`getItemAsync` with **no options object** — no `requireAuthentication`,
no `keychainAccessible`. A repo‑wide grep confirms these options appear **nowhere**. So the
keystore releases the cleartext money PIN (and the session token) to any code path that asks,
with **no OS‑enforced biometric/keyguard challenge at read time**. On a rooted or
Frida‑instrumented device, or via a forensic extraction, both factors the backend relies on —
the token *and* the PIN — are recoverable from the same unprotected keystore, collapsing the
backend's documented "a stolen token isn't enough" design (`backend/common/http.py:227-229`).

The in‑file comment *"Retrieval is always gated by the OS biometric prompt"*
(`lib/secureStore.ts:56-57`) is **factually false** — retrieval is gated only by a separate
app‑level `authenticate()` call that JS can skip.

> *Server mitigation:* none — the server cannot distinguish a recovered‑PIN submission from a
> legitimate one; lockout doesn't help once the exact PIN is known. `expo-secure-store ~13.0.2`
> does support these options, so the fix is a drop‑in.

### A2. The PIN is read into JS on every keypad mount, just to toggle UI — **HIGH**
`components/design/ui.tsx:431-438`

`PinPad` calls `getTransactionPin()` **unconditionally on mount** (inside `Promise.all`) purely to
decide whether to render the biometric button. The plaintext PIN is pulled into the JS heap every
time the pad shows, even when the user is going to type it. Combined with A1, the secret is both
unprotected at rest and needlessly resident in memory.

**Fix (A1+A2):** pass `{ requireAuthentication: true, keychainAccessible: WHEN_UNLOCKED_THIS_DEVICE_ONLY, authenticationPrompt: '…' }` on the `txn_pin` **and** `access_token` items so the OS gates the read; and track a **non‑secret `hasPin` boolean** for the UI instead of fetching the PIN on mount. Better still, don't cache the raw PIN at all — register a hardware‑backed device key and have biometric pay produce a **server‑verified signed assertion** over a nonce.

### A3. Device passcode substitutes for biometrics, then auto‑submits the PIN — **HIGH**
`lib/biometrics.ts:125-137` → `components/design/ui.tsx:448-454`, `app/(servicesscreen)/sendmoney.tsx:161`

`authenticate()` uses `disableDeviceFallback: false`, so the **device unlock PIN/passcode** can
satisfy the *"Approve payment"* biometric prompt. On success, `useBiometric()` immediately reads
the cached money PIN and submits it. This **collapses two independent factors** (device unlock and
the money PIN) into one: a thief who shoulder‑surfed the device passcode can approve payments and
reveal the card without ever knowing the money PIN. The shortcut is present on **every**
PIN‑gated money flow (transfers, airtime, data, cable, electricity, exams, betting, loan request,
loan repay, fixed savings, card funding, **and card PAN/CVV reveal**).

**Fix:** set `disableDeviceFallback: true` (and/or `biometricsSecurityLevel: STRONG`) on
money‑authorizing prompts so only an enrolled biometric — never the device passcode — releases
the cached PIN.

### A4. Biometric is a local boolean with no server‑verifiable assertion — **MEDIUM**
`components/design/ui.tsx:448-454`

The biometric step is a plain JS boolean; the server only ever sees the resulting
`transaction_pin` and has no nonce/challenge/signed‑assertion concept (confirmed absent in
`backend/`). On an instrumented device, hooking `authenticate()` to return `true` converts the
cached PIN into money movement with no real scan. (Bounded by server PIN re‑verification + lockout,
hence medium, but the "pay with Face ID" feature provides no assurance under device compromise.)

### A5. 4‑digit PIN with no triviality rejection — **LOW**
`app/(auth)/setpin.tsx:12-77`, server `backend/accounts/views.py:315`

PIN is fixed at 4 digits; neither client nor server rejects `0000`/`1234`/repeated/sequential
PINs (server only checks `len(pin) < 4`). Online brute force is well contained (5 attempts → 15‑min
lock, atomic under a row lock), but trivial‑PIN acceptance raises a thief's odds within the
lockout window. **Fix:** reject trivial/sequential/repeated PINs at setup, consider 6 digits, and
surface the server‑returned remaining‑attempts count (`backend/common/http.py:221-222`) in the UI.

---

## B — Screen‑capture exposure of card data, PIN and KYC  ·  **HIGH**

### B1. No `FLAG_SECURE` / screen‑capture protection anywhere — **HIGH**
`app/(homepage)/cards.tsx:111-112`, `app/(auth)/kyc.tsx:40-44`, `components/design/ui.tsx` (PinPad)

A repo‑wide grep for `FLAG_SECURE` / `preventScreenCapture` / `expo-screen-capture` returns
**zero** matches, and `expo-screen-capture` is **not a dependency**. Expo/RN apps have no
screenshot or recents‑thumbnail protection by default. As a result every sensitive screen is
screenshot‑able, recordable by a co‑installed app holding MediaProjection/accessibility consent,
and — with **no user action** — rendered into the Android **recents/app‑switcher thumbnail**:

- The card screen renders the **full PAN** (`reveal ? panGroups : card.masked`) and **CVV**
  (`CVV ${reveal.cvv}`) after a PIN‑gated reveal (`cards.tsx:111-112`).
- The **PIN pad** (`ui.tsx` `PinPad`, used on every spend) and the **BVN/NIN entry**
  (`kyc.tsx:40,43`) and **wallet balance** are likewise unprotected.

> The full PAN+CVV leak is *conditional* (the user must first PIN‑reveal and not yet tap Hide);
> the always‑on passive exposures are masked PAN, balance, and any in‑progress KYC/PIN entry. For
> a fintech handling card PANs and Nigerian identity numbers, this still warrants **high**.

### B2. Revealed card PAN/CVV never auto‑hides or clears on backgrounding — **MEDIUM**
`app/(homepage)/cards.tsx:83,111-112,129`

After one PIN entry, `reveal = { pan, cvv, expiry, holder }` stays rendered **indefinitely** until
the user manually taps Hide. There is no re‑mask timeout and **no clearing of `reveal` when the
app backgrounds** (`app/_layout.tsx:73-83` only handles the idle lock). The card details persist in
JS memory and in the recents thumbnail.

**Fix (B1+B2):** add `expo-screen-capture` and call `preventScreenCaptureAsync()` on mount of the
card‑reveal, PIN, and KYC screens (release on unmount); apply Android `FLAG_SECURE` via a config
plugin so the recents thumbnail is blanked; auto re‑mask card details after ~30–60 s and clear
`reveal` on `AppState` leaving `active`.

---

## C — Session retention & client‑only lock  ·  **MEDIUM**

### C1. Idle lock keeps a long‑lived 24h token + PIN on the device — **MEDIUM**
`lib/session.ts:14-15,27-29`, `app/(auth)/signin.tsx:42-47`

On idle timeout the session is **locked, not cleared** — the access token and the cached money PIN
stay in the keystore. The server token is a single opaque bearer with a **24h absolute,
non‑sliding TTL and no refresh/rotation/device‑binding** (`backend/accounts/models.py:158-170`,
`settings.py:143`). The biometric unlock path does **no server round‑trip** — it just calls
`authenticate()` then `unlockSession()` + `router.replace('/home')`. A thief who can satisfy the
local gate (own enrolled biometric, coercion, or — per A3 — the device passcode) gets up to 24h of
full wallet access with no server‑side circuit breaker.

### C2. Idle‑lock state is a plaintext AsyncStorage flag — **LOW**
`lib/session.ts:19,27-39`

`z-locked`, `lastActiveAt`, `z-bg-at` live in unencrypted AsyncStorage. On a rooted/USB‑debuggable
device an attacker can delete `z-locked` (or backdate `lastActiveAt`); because the token is **not**
keystore‑auth‑bound (A1), `AuthGuard`'s `token && !locked` check (`components/AuthGuard.tsx:37`)
then passes and the app opens with no biometric/password. The app markets this lock as a
protection (`app/(servicesscreen)/safetytips.tsx`).

### C3. `AuthGuard` `lastKnownAuth` cache can flash protected content during a locked session — **LOW**
`components/AuthGuard.tsx:16,29,38`

A process‑lifetime module variable seeds the guard's initial state, and the async lock/token check
runs only in a later `useEffect`. After a session **locks**, a **cross‑route‑group** navigation
mounts a fresh guard seeded `authed` and renders the target screen for a tick before the redirect
fires — and any API calls it kicks off succeed against the still‑valid token (live financial
data). *(Verification narrowed this: the "flash right after Log out" sub‑claim is refuted — logout
nulls the in‑memory token cache and navigates to `/signin`; only the idle‑locked, cross‑group case
is real.)*

**Fix (C1–C3):** shorten the token TTL or issue short‑lived + refresh tokens so a locked session
expires quickly server‑side; require a server‑validated step on unlock (re‑issue token after
biometric); store the token under a `requireAuthentication` keystore key so the lock flag isn't the
only barrier; reset `lastKnownAuth` inside `clearSession()`/`lockSession()` (or gate the optimistic
render on a synchronous in‑memory lock flag).

---

## D — Transport security  ·  **MEDIUM**

### D1. No TLS certificate / SPKI pinning — **MEDIUM**
`lib/api.ts:36-41`, `app.json:38`

All traffic — bearer token, money PIN, transfers, **card PAN/CVV reveal**, BVN/NIN KYC uploads —
is validated only against the device trust store; there is no pinning library or
`network_security_config`. *(Verification correctly downgraded this from the original High: on
Android 7+ — this app's target — user‑added/MDM/malware CAs are **not** trusted by default, so the
"malicious CA / corporate proxy" vectors don't intercept an unmodified app; the realistic vector is
a rooted/Frida device, where pinning is itself bypassable. Combined with enforced HTTPS + backend
HSTS, this is a defense‑in‑depth gap, not an open door — but a fintech carrying PIN/PAN/BVN over the
wire should pin.)*

**Fix:** add SPKI pinning for `api.zitch.ng` (+ the Render fallback) with a backup pin and a
rotation plan — e.g. `expo-build-properties` emitting an Android `networkSecurityConfig` `<pin-set>`
that also excludes user CAs for release, plus iOS pinning. Fail closed on mismatch.

### D2. Cleartext‑downgrade guard has no host allowlist — **LOW**
`components/configFiles/apiConfig.tsx:9-16`

The guard rewrites only values starting with literal `http://`; **any `https://` host** is honored
as‑is (and the check is case‑sensitive). A tampered/misconfigured build (`EXPO_PUBLIC_API_URL` is
build‑time‑inlined) could silently ship every user's token/PIN/KYC to an arbitrary host. Bounded to
a build‑time/CI threat. **Fix:** in release, hard‑pin `baseUrl` to an explicit allowlist
(`api.zitch.ng` / `zitch-api.onrender.com`); make the scheme check case‑insensitive; reject
non‑HTTPS.

### D3. Server‑supplied URL flows into `Linking.openURL` without a scheme allowlist — **LOW**
`app/(servicesscreen)/linkwhatsapp.tsx:17-20,70,142`

`openWa()` opens the server's `wa_link` verbatim. In normal operation the backend builds a safe
`https://wa.me/…` URL (`backend/whatsapp/views.py:169`), so this only bites under an active MITM
(which D1's absence permits) — an attacker could redirect the trusted button to a phishing/`intent:`
target. **Fix:** construct the `wa.me` URL client‑side, or allowlist `https://wa.me/` /
`https://api.whatsapp.com/` before `openURL`.

### D4. Money/service screens use bare `fetch()`, bypassing the hardened wrapper — **LOW**
`app/(servicesscreen)/buydata.tsx:54-85` (also buycable, sendmoney, exams, betting, fixedsave)

Reference/price fetches call `fetch().then(r=>r.json()).catch(()=>{})` directly, bypassing the
abort timeout, 401 handling and non‑JSON guard in `lib/api.ts`. A hung upstream leaves the loader
stuck; a gateway HTML body is swallowed into an empty plan list with no error. Final debit is
server‑priced, so no money impact. **Fix:** route these through `apiJson()`.

### D5. Access token mirrored into every request body as `access_token` — **LOW**
`lib/api.ts:39`

The token is sent both as the `Authorization` header (correct) and copied into every JSON body
"for backwards compatibility." Bodies are far more likely than auth headers to be captured by crash
reporters, WAF/CDN debug logs and request loggers, widening the leak surface for a live session
token. The backend already prefers the header (`backend/common/http.py:278-284`). **Fix:** drop the
body mirror.

---

## E — Build, release & platform hardening  ·  **MEDIUM → LOW**

### E1. Debug‑key‑signed preview APK wired to the production API — **MEDIUM**
`codemagic.yaml:21,30-40`

The Codemagic workflow runs `expo prebuild` + `gradlew assembleRelease` with **no signing
configured**, so (per its own comment) the release variant is signed with the **universal public
Android debug key** — zero tamper‑evidence: anyone can decompile, patch, and re‑sign with the same
key. Critically, this artifact is pointed at the **production** API (`EXPO_PUBLIC_API_URL=
https://api.zitch.ng`, `codemagic.yaml:21`) and published as a build artifact on every push to
`main`. The backend performs **no Play Integrity / app‑signature attestation** (confirmed absent),
so it cannot reject a repackaged clone.

> *Verification adjusted this down from High:* the **production Play Store** path is the `eas.json`
> `production` profile, which uses EAS‑managed credentials + Play App Signing (not the debug key),
> and the `.eas` preview workflow uses EAS‑managed keys too. The debug‑signed artifact is the
> **Codemagic preview/CI build only** — but it's a fully functional production client with no
> tamper‑evidence, which is the real concern.

**Fix:** point the preview/CI build at a **staging** API; configure a real keystore for any APK
that reaches testers; add Play Integrity verification on money endpoints server‑side.

### E2. No root / emulator / Frida detection and no Play Integrity attestation — **LOW**
`package.json` (no `jailmonkey`/`rootbeer`/integrity libs), `backend/` (no attestation)

*(Verification adjusted from Medium.)* Because the server independently authorizes every sensitive
action (PIN, limits, balances), a Frida/root attacker only controls **their own** already‑authed
session — not a remote‑takeover primitive. The genuine residual risk is **fraud automation /
emulator‑farm abuse / bonus‑abuse** with no tamper signal. **Fix:** add Play Integrity as a
*signal* (not sole enforcement) and degrade high‑risk actions on compromised devices.

### E3. No code obfuscation / ProGuard‑R8, EOL Expo SDK 51 / RN 0.74.3 — **LOW**
`app.json:46-67` (no `expo-build-properties`), `package.json:28,47`

No R8/minification config; the JS ships as Hermes bytecode (decompilable) exposing endpoints and
client‑side constants (e.g. `LARGE_TXN=100000`, which the server re‑enforces anyway). The app is
also frozen on an **out‑of‑support** Expo SDK 51 / RN 0.74.3 (not even the last 0.74.x patch), so it
misses upstream security fixes. *(Both adjusted down: amplifier / maintenance‑debt, not a direct
break; on Android the TLS stack is OS‑provided, and there's no in‑app WebView.)* **Fix:** enable R8
via `expo-build-properties`; plan an upgrade to a supported SDK/RN line.

### E4. EAS `projectId` / `owner` and no native shrinking — **INFO**
`app.json:46-79`

Required Expo identifiers, not secrets (no OTA/push capability without an authenticated token,
which is absent). No action needed beyond keeping EAS tokens out of the repo (they are).

---

## F — Permissions & privacy  ·  **LOW**

### F1. `ACCESS_FINE_LOCATION` requested but never used — **LOW**
`app.json:34`

The most privacy‑sensitive runtime permission is declared, yet **no code reads device location**
(no `expo-location` dependency; grep finds only the manifest line and an unrelated WebAuthn
`window.location.hostname`). For a Nigerian fintech this is an **NDPR data‑minimisation /
lawful‑basis** issue and Play‑review friction, plus latent risk if a future/compromised dependency
exploits the already‑declared entitlement. **Fix:** remove it; if ever needed, add deliberately
with a runtime rationale and prefer COARSE.

### F2. `android:allowBackup` not disabled — **LOW**
`app.json:25-40`

No `allowBackup=false`/`dataExtractionRules`, so it inherits the platform default `true`. *(Adjusted
from Medium: credentials are in the Keystore and are **not** backup‑extractable; targetSdk 34
excludes app data from `adb backup` by default.)* The real residual exposure is **Google cloud Auto
Backup of AsyncStorage PII** (email, phone) + lock state. **Fix:** set `allowBackup=false` via
`expo-build-properties`.

### F3. KYC selfie / NIN‑slip images left in app cache — **LOW**
`app/(auth)/kyc.tsx:94-115`, `app/(auth)/accountdetails.tsx:57-67`

`expo-image-picker` copies captured assets into the app cache; the code uploads `base64` but never
deletes them (no `FileSystem.deleteAsync` anywhere). *(Adjusted from Medium: app‑private cache,
recoverable only on a rooted/forensic device.)* **Fix:** capture and delete `asset.uri` in a
`finally`; clear KYC cache on sign‑out.

### F4. Sensitive values copied to the clipboard with no auto‑clear — **LOW**
`app/(homepage)/home.tsx:60`, `addmoney.tsx:42`, `linkwhatsapp.tsx:82`, `convert.tsx:63`, `bizpayment.tsx:36`

Account number and the short‑lived WhatsApp `LINK` code are written to the global clipboard
(readable by any foreground app) with no clear. *(The LINK code's abuse is limited — binding also
requires sending from the user's registered WhatsApp number, `backend/whatsapp/router.py:269-280`.)*
**Fix:** prefer the prefilled `wa.me` deep link over copying the code; never copy PIN/PAN/OTP.

### F5. `SmartPaste` auto‑reads the clipboard on every home entry — **LOW**
`components/design/SmartPaste.tsx:25-45`, mounted at `app/(homepage)/home.tsx:208`

On mount it reads `Clipboard.getStringAsync()` and, if it finds a 10–11‑digit number, routes it
into the send‑money / airtime screen — ingesting whatever the user last copied from **any** app
(e.g. an OTP/account number from another bank) without an explicit paste action, and nudging it
toward a money flow. **Fix:** read the clipboard only on an explicit "Paste" tap; never pre‑load a
detected value into a money screen without re‑confirmation.

---

## G — Money‑flow correctness  ·  **LOW / INFO**

### G1. Loan request has no idempotency key — **LOW**
`app/(servicesscreen)/getloan.tsx:65`, server `backend/loans/services.py:57-66`

Unlike every other spend, the loan flow sends no `idempotency_key`, and the backend disbursement
credit isn't routed through `spend_key`. A double‑disbursement is prevented by the active‑loan
check + DB unique constraint (409), so the residual is **UX only**: a legitimate retry after a
timed‑out‑but‑succeeded request gets a confusing 409 instead of an idempotent replay. *(The
"no in‑flight guard" sub‑claim was corrected — `PinPad` already blocks input while `busy`.)*
**Fix:** add a `useRef` key like the other screens; route the disbursement through `spend_key`.

### G2. Receipts show client‑cached plan price, not the server‑charged amount — **LOW**
`app/(servicesscreen)/buydata.tsx:89,131`, `buycable.tsx`

The receipt `Total` is rendered from a separately‑fetched, possibly‑stale plan price; the server
charges its own authoritative `plan.price`. No money is lost (server is authoritative), but a
mid‑session price change makes the receipt mismatch the actual debit. **Fix:** return and render the
server's charged amount (requires a small backend change — the success payload currently omits it).

### G3. Hardcoded ₦0 fee on confirm sheets / receipts — **INFO**
`components/design/flowkit.tsx:219`, `sendmoney.tsx:196`, etc.

The Fee row is a static `₦0` and the Total equals the bare amount. Accurate **today** (the backend
adds no fee), but if a fee is ever introduced (the console fixtures already model fees), every
confirm sheet/receipt would silently understate the charge pre‑PIN. **Fix:** source fee + grand
total from a server quote and authorize the PIN against the server total.

---

## H — Dependency & misc hygiene  ·  **INFO**

- **H1. Dead `react-native-vector-icons`** (`package.json:54`) — never imported (only
  `@expo/vector-icons` is used); unused install‑time/transitive surface. Remove it.
- **H2. `nativewind` pinned to abandoned 2.0.11** (`package.json:44`) — build‑time tech‑debt that
  complicates the SDK upgrade; barely used in the APK's own screens. Migrate with the SDK bump.
- **H3. Web WebAuthn fallback treats first‑time enrollment as authentication**
  (`lib/biometrics.ts:53-91`) — **dead code in the APK** (`isWeb`‑gated); only relevant if the
  "preview only" web build is ever exposed to real accounts.
- **H4. Biometric‑enabled flag is a tamperable AsyncStorage value** (`lib/biometrics.ts:5,140-149`)
  — flipping it only surfaces a UI button that still requires a live OS scan + a pre‑existing
  Keystore secret; no path to funds. Move to `expo-secure-store` for tidiness.

---

## Prioritised remediation roadmap

**Now (small, high‑leverage):**
1. `requireAuthentication: true` + `keychainAccessible` on the `txn_pin` **and** `access_token`
   keystore items; stop reading the PIN on `PinPad` mount (use a `hasPin` boolean). *(A1, A2, C2)*
2. `disableDeviceFallback: true` on all money‑approval prompts. *(A3, A4)*
3. Add `expo-screen-capture` + Android `FLAG_SECURE` to card/PIN/KYC screens; auto‑hide & clear
   card details on background. *(B1, B2)*
4. Remove the unused `ACCESS_FINE_LOCATION` permission. *(F1)*
5. Point the preview/CI APK at a staging API; stop publishing a debug‑signed production client.
   *(E1)*
6. Drop the `access_token` body mirror; remove the false "always gated" comment. *(D5, A1)*

**Next (moderate):**
7. SPKI certificate pinning + exclude user CAs in release. *(D1)*
8. Shorten token TTL / add refresh + server‑validated unlock; reset `lastKnownAuth` on lock/logout.
   *(C1, C3)*
9. `allowBackup=false`; delete KYC image cache after upload. *(F2, F3)*
10. Reject trivial PINs; surface lockout/remaining attempts. *(A5)*
11. Add a loan idempotency key; render server‑authoritative amounts on receipts. *(G1, G2)*
12. Harden clipboard usage and `SmartPaste`; allowlist `Linking.openURL` targets; route reference
    fetches through `apiJson`. *(F4, F5, D3, D4)*

**Strategic:**
13. Move biometric pay to a hardware‑backed key + **server‑verified signed assertion** instead of
    replaying a cached PIN. *(A4)*
14. Add Play Integrity attestation as a server‑side signal for money endpoints. *(E1, E2)*
15. Upgrade off EOL Expo SDK 51 / RN 0.74; enable R8; migrate NativeWind; prune dead deps.
    *(E3, H1, H2)*

---

## Methodology & confidence

10 review dimensions (secrets/build, data‑at‑rest, auth/session, PIN/biometric, money flows,
network/transport, PII/logging/deep‑links, permissions/native, dependencies, hardening) were run in
parallel, producing 41 candidate findings. **Each** finding was then handed to an independent
verifier instructed to *refute* it by reading the actual code and checking the Django backend for
server‑side mitigation. Outcome: **24 confirmed as‑stated, 17 adjusted** (severity/scope corrected —
chiefly because the backend re‑enforces money controls, the Android 7+ CA‑trust model, and Play App
Signing on the production path), **0 false positives**. Where a verifier corrected the original
claim, this report uses the corrected facts and notes it inline.

Residual uncertainty is low for client‑code claims (all carry file:line evidence). Backend
mitigation citations were spot‑checked, not exhaustively re‑audited — the standing assumption,
consistent with `ZITCH_AUDIT_REPORT.md`, is that the server is the authoritative enforcement point.
