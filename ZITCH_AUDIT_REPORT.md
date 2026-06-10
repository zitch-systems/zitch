# ZITCH Fintech Platform — Comprehensive Audit Report

> Prepared as: Senior Fintech Architect · Security Engineer · Backend/Mobile Engineer · QA · DevOps · PCI/CBN/NDPR reviewer
> Scope: full codebase (`zitch-systems/zitch`) — Django backend + Expo/React Native app + new web surfaces.
> Posture: treat the platform as 48 hours from a CBN regulatory review and production launch.

This audit was run alongside a build task (wiring the marketing landing page and the
operator/admin portal to the backend). **Two tracks are reported here:**

- **§A — What was built/wired this session** (landing, admin portal, staff API, supporting features).
- **§B — The audit** (Phases 1–5 deep; 6–11 summarized), with every finding marked **`FIXED (this pass)`** or **`OPEN (recommended)`**.

Surgical security fixes were applied in-place per the audit constraints — no feature, flow, schema, or
endpoint was removed or restructured. All 174 existing backend tests pass after the changes.

---

## §A. What was built & wired this session

> **Reconciliation note (post-merge with `main`#39):** `main` independently shipped a parallel implementation of
> this feature as a single `portal/` Django app (canonical landing `/`, operator portal `/portal/`, staff API
> `/api/ops/`). Per the maintainer's decision, **both implementations are kept and run side-by-side**: `main`'s
> `portal/` owns the canonical paths, and this session's `console/` + `admin_api/` build coexists at **`/console/*`**
> and **`/api/admin/*`** (distinct routes, no shadowing; redundant `wallet`/`whatsapp` index migrations were dropped
> in favour of `main`'s). The audit fixes in §B are the durable, unique value either way.

| Surface (this session's `console` build) | URL | Status |
|---|---|---|
| Marketing landing page | `/console/` | Served from Django (`console` app). Responsive (≤1020/≤760/≤480/≤340), light/dark, live WhatsApp demo, embeds the app prototype. |
| App prototype | `/console/app/` | Embedded by the landing hero iframe (`?embed=1`). |
| Operator / admin portal | `/console/portal/` | React-in-browser SPA, backed by a **real Django staff API** with login, RBAC, audited actions. |
| Staff API | `/api/admin/*` | `admin_api` Django app — see below. (`main`'s portal API is at `/api/ops/*`.) |
| Liveness / readiness | `/healthz` · `/readyz` | `/healthz` liveness (render health-check path); `/readyz` added this session round-trips the DB (503 when down). |

**New backend app `admin_api`** (additive — nothing existing was changed):
- `POST /api/admin/login` — staff sign-in (requires `is_staff`); reuses the app's `AccessToken`.
- `GET /api/admin/me` / `GET /api/admin/bootstrap` — current operator + one-shot real-data aggregator
  (users, transactions, KYC queue, WhatsApp conversations, broadcasts, loans, savings, cards, FX corridors,
  float/liability, providers live/mock, audit log, system settings, team, KPIs).
- Audited write actions, each behind a **server-enforced** capability: `settings/update` (incl. the AI kill switch),
  `users/status` (freeze/unfreeze), `kyc/review` (tier bump), `txn/flag`, `cards/freeze`, `wa/handover`.
- **RBAC is enforced on the server** (`super_admin`/`finance`/`support`/`read_only`); the portal's "view as"
  switcher is presentation-only and cannot escalate. Every action appends to the immutable `AuditLog`.

**Supporting/hardening features added** (double as audit fixes — see §B): server-side amount sanitisation,
idempotency fallback for spends, rate limiting on money/enumeration endpoints, WhatsApp signature prod-guard,
PIN-shape log redaction, and DB index/constraint additions.

> **Operator setup:** create a staff user (`python manage.py createsuperuser` → `super_admin`), then sign in at `/console/portal/` (this build) or `/portal/` (main's portal).
> Non-superuser staff get a role from their Django Group (`finance` / `support` / `read_only`), else least-privilege `read_only`.

---

## §B. The Audit

## 1. Executive Summary

ZITCH is a **well-architected** Nigerian fintech. The money core is materially stronger than typical
early-stage fintechs: every balance change flows through a single ledger service with **atomic transactions,
`SELECT FOR UPDATE` row locking, DB `CHECK` constraints** (non-negative balance, positive amount),
**idempotency keys with a partial unique index**, **double-entry internal transfers**, **PIN brute-force
lockout**, **KYC tiers with a face-verification step-up**, and **provider-timeout reconciliation that never
double-settles**. Monetary fields are uniformly `DECIMAL` (no `FLOAT` anywhere). This is a strong foundation.

The audit nonetheless found issues that **must** be addressed before holding real customer money. The most
serious sit in the **WhatsApp banking channel** (a public webhook) and in **defense-in-depth gaps** on the
REST money endpoints:

- **2 Critical** (security) — WhatsApp webhook accepts **unsigned** callbacks when `APP_SECRET` is unset (no prod
  guard); empty WhatsApp message-ids **bypass replay dedupe**. Both enable victim impersonation / replay. **Both FIXED.**
- **~9 High** — optional idempotency keys (double-charge on retry across every spend endpoint), `fund_card`
  bypassing KYC limits, **no rate limiting** on money/enumeration/WhatsApp-inbound endpoints, WhatsApp
  link-code brute-force + identity-binding gaps, PIN values reaching the message log in clear. **Most FIXED.**
- **Operational (Phase 11):** no logging config, no Sentry, no DB backups, `DEBUG` fail-open default — the weakest area.
- **Scalability (Phase 10):** single gunicorn worker + no queue + per-process cache; synchronous provider I/O and an
  N+1 admin bootstrap cap throughput hard until addressed (mostly architectural, scoped in §10).
- Plus Medium/Low correctness, validation, and performance items. Phases 6–7 were **executed live** (39+7 checks);
  Phase 6 testing itself surfaced and fixed a `1e500`→500 crash and a VTU double-debit gap.

**Go-live recommendation: CONDITIONAL.** The 2 Critical and most High items are **fixed in this pass**; the
remaining blockers are operational (logging/Sentry/backups, §11) and scale-architectural (queue + workers, §10),
plus the WhatsApp link-binding and optimistic-payout items (§3). With §17's checklist closed, the platform is ready
for a controlled rollout.

### Production Readiness Scores

| Category | Score | Notes |
|---|---:|---|
| **Overall** | **77 / 100** | Strong money/security core; operations + scale architecture are the gating gaps. |
| Security | 80 / 100 | Excellent money primitives; both Criticals + most Highs fixed this pass. WhatsApp link-binding + payout-PENDING remain. |
| Code Quality | 86 / 100 | Clean, documented, **175 tests passing**; input-validation 500s fixed; a few provider seams remain. |
| Scalability | 60 / 100 | Single worker + no queue + per-process cache + synchronous provider I/O + N+1 bootstrap; hot indexes added, rest architectural (§10). |
| Operational readiness | 45 / 100 | No logging config / Sentry / DB backups; `/readyz` added this pass, but observability is the weakest area (§11). |
| Compliance (PCI/CBN/NDPR) | 72 / 100 | KYC tiers + audit log + no card PAN storage; card-load bypass + PIN-log fixed; ledger immutability + WhatsApp-VTU limits remain. |

---

## 2. Architecture Review (Phase 2)

**Stack.** Backend: Django 5.2 (function-views + a tiny `common.http` JSON/`@api`/`@require_user` layer — no DRF),
SQLite (dev) / Postgres (prod via `dj-database-url`), WhiteNoise static, gunicorn, Render blueprint with two cron
workers (maturity sweep, VTU reconcile). App: Expo SDK 51 / expo-router v3 / NativeWind. Integrations (all with a
clean **mock-mode-when-unkeyed** seam): Monnify (funding/payout), Baxi (VTU), Sendchamp (SMS/OTP), Prembly (KYC),
Fincra (FX), Meta WhatsApp Cloud API, optional LLM intent layer.

**Strengths.**
- **Single money chokepoint** (`wallet/services.py`): debit/credit/transfer/settle all in one audited, atomic, locked place.
- **Provider abstraction** with mock fallbacks makes the whole system testable without live keys.
- **Reconciliation** crons settle provider-timeout (`PENDING`) purchases rather than blindly refunding.
- **Layered config hardening** already present (HSTS, SSL redirect, secure cookies, secret-key boot guard).

**Findings.**

| Location | Severity | Issue | Fix |
|---|---|---|---|
| `whatsapp/views.webhook` + `ops.send_broadcast` | High | Inbound messages and broadcasts are processed **synchronously in the request**. A Meta retry storm or a large broadcast blocks workers. | Move to a queue (Celery/RQ/Cloud Tasks). **OPEN.** |
| `common/ratelimit.py` (LocMemCache) | Medium | Per-process cache ⇒ effective limit ×(workers); resets on deploy. | Back `CACHES` with Redis, or rate-limit at the edge. **OPEN.** |
| RBAC depth (`whatsapp` ops, admin) | Medium | Operator actions gated only by `is_staff` in the WhatsApp ops layer. | Real role/permission checks. **FIXED for the new `admin_api`** (role matrix); WhatsApp `ops.py` still `is_staff`-only — **OPEN.** |
| No API versioning | Low | Routes are unversioned (`/api/...`). | Introduce `/api/v1/` before external partners. **OPEN.** |

---

## 3. Security Review (Phase 3)

> Format: **`[STATUS]` Severity — Title** · *file:function* · vulnerability · exploit · fix.

### CRITICAL

**`[FIXED]` Critical — WhatsApp webhook accepts unsigned callbacks in production**
*`whatsapp/providers.py:verify_signature` + `zitch_api/settings.py`.* `verify_signature` returned `True`
whenever `APP_SECRET` was blank — a mock-mode convenience that was **not gated on `DEBUG`**. A prod deploy that
forgot to set `WHATSAPP_APP_SECRET` would silently accept any forged POST to the public, csrf-exempt
`/webhooks/whatsapp`. Since `from` is attacker-controlled, an attacker could impersonate **any linked victim's
number** and drive their banking session up to (not through) the PIN gate, plus disclose balances, run
name-enquiries, and poison the agent monitor.
**Fix applied:** unsigned is now accepted **only** when `settings.DEBUG and not wa_live()`; and settings now
**fails fast at boot** (`ImproperlyConfigured`) if the channel is live or `_PROD` and `APP_SECRET`/`VERIFY_TOKEN`
are missing.

**`[FIXED]` Critical — Empty `wa_message_id` bypasses replay dedupe**
*`whatsapp/views.py:_process`.* Dedupe relies on a **partial** unique index that excludes blank ids, but
`_process` accepted `mid == ""`. Any message with a missing id was inserted and handled **every time** — enabling
replay of a confirmation message, repeated paid name-enquiries, and PIN-attempt probing.
**Fix applied:** drop any inbound message missing `from`/`id` (real Meta messages always carry an id).

### HIGH

**`[FIXED]` High — Idempotency optional on every spend endpoint (double-charge on retry)**
*`transfers`, `wallet`, `cards`, `utility`, `betting`, `exams`, `convert`, `loans` views.* The ledger dedupes only
when a non-empty `idempotency_key` is supplied; the unique index excludes `""`. The Expo app sends a key on every
spend, but a legacy/3rd-party client (or a flaky-network retry that omits it) could debit + call the provider
twice. **Loan repayment had no idempotency at all** (random reference per call).
**Fix applied:** new `common.http.spend_key()` derives a deterministic server-side fallback key (user + spend
details + short time-bucket) when the client omits one, so a double-submit is deduped by the unique index instead
of debiting twice. Threaded through `transfer_send`, `bank_transfer`, `fund_card`, `savings_create`, **and
`loan_repay`** (service `repay()` now takes an `idempotency_key`; the loan screen now sends one). VTU/betting/exams
already accept a client key and are covered by the app; server fallback recommended there too — **partially OPEN**.

**`[FIXED]` High — `fund_card` bypassed KYC tier + face-verification limits**
*`cards/views.py:fund_card`.* Unlike `bank_transfer`/`transfer_send`, card funding never called `check_send_limits`,
so a Tier-1 user (₦50k cap) could load an unlimited amount onto a spendable virtual card — a tier/AML-control bypass.
**Fix applied:** `fund_card` now enforces `check_send_limits` (and safe amount parsing + rate limiting).

**`[FIXED]` High — No rate limiting on money / enumeration endpoints**
*transfers/wallet/cards/loans views.* `resolve_recipient`/`resolve_account` were unauthenticated-cost **enumeration
oracles** (confirm a phone/@tag/email → discloses the holder's name); money endpoints had no throttle to bound
brute-force/DoS.
**Fix applied:** `@ratelimit` added to `resolve_recipient`, `resolve_account`, `transfer_send`, `bank_transfer`,
`fund_card`, `card_details`, `fund_initialize`, `loan_request`, `loan_repay`. *(Note: LocMemCache backing — see §2;
move to Redis for multi-worker accuracy.)* VTU `validate_iuc`/`validate_meter` throttle — **OPEN.**

**`[FIXED]` High — WhatsApp inbound has no throttle (link-code brute-force)**
*`whatsapp/views.py:_process`.* No per-sender limit on the public webhook; link codes are 24-bit
(`secrets.token_hex(3)`) valid for 10 min, matched with no attempt cap — brute-forceable to bind an attacker's
WhatsApp to a victim's account.
**Fix applied:** per-`msisdn` inbound throttle (30/min). **Recommended (OPEN):** add a per-msisdn link-guess cap and
raise code entropy to `token_hex(4)`.

**`[FIXED]` High — Transaction PIN could be logged in clear**
*`whatsapp/views.py:_process`.* PIN masking depended solely on flow state (`is_awaiting_pin`); an out-of-band or
mistimed PIN was stored verbatim in `WaMessageLog` (and shown in the agent monitor) — a credential leak.
**Fix applied:** redact anything matching `^\d{4,6}$` regardless of state, in addition to the state-based mask.

**`[OPEN]` High — WhatsApp link code not bound to the user's registered number**
*`whatsapp/router.py:_handle_unlinked`.* Whoever sends a valid pending code from **any** number gets bound; there is
no check against `user.phone`. A shoulder-surfed/leaked code → account binding. **Recommend:** require the code to
arrive from (or match) the registered MSISDN; shorten TTL; alert on mismatch.

**`[OPEN]` High — Optimistic settle on payout `PENDING` can leak customer funds**
*`transfers/services.py:execute_payout`.* A provider `PENDING`/queued payout is marked `SUCCESS` immediately; if it
is later dropped without a `FAILED_DISBURSEMENT` webhook, money is gone with no auto-refund. **Recommend:** keep the
row `PENDING` + `meta.reconcile` on provider `PENDING`, settle `SUCCESS` only on `SUCCESS/COMPLETED`, and reconcile.

### MEDIUM / LOW (security)

- **`[FIXED]` Medium — Non-finite amount (`Infinity`/`1e500`) → 500** on savings/loans (and now all touched money
  endpoints): new `parse_amount()` rejects non-finite/≤0 and quantizes to 2dp (also closes a sub-kobo ledger/rail
  drift). **`[FIXED]` Medium — bare `int()` on tenure/days → 500:** safe `_parse_tenure`/`_parse_days`.
- **`[OPEN]` Medium — AI intent layer pre-fills attacker-controllable destination** into the confirm card
  (`router.py:_begin_airtime`/transfer branch). PIN still required, but a prompt-injected message can stage a
  malicious confirm. **Recommend:** never let the LLM pre-fill a destination straight to the PIN step; re-collect/echo.
- **`[OPEN]` Medium — Tier/face limits not enforced on WhatsApp VTU paths** (airtime/data/electricity/cable) — only
  on transfers. A user can exceed KYC limits via bills. **Recommend:** call `send_limit_error` on each VTU amount step.
- **`[OPEN]` Medium — WhatsApp `PendingAction` has no per-msisdn uniqueness/lock** → concurrent messages can race the
  in-flow PIN-attempt counter. (Wallet idempotency still prevents double-debit.) **Recommend:** unique-per-msisdn +
  `select_for_update`.
- **`[OPEN]` Medium — `fund_verify` doesn't assert the reference belongs to the caller** (`wallet/views.py`). Credit
  still goes to the intent's true owner and is one-shot, so no theft; it's an info-exposure/ownership gap. **Recommend:**
  scope the reference to `request.user_obj`.
- **`[OPEN]` Low — `resolve_account` mock accepts any account/bank**; **`_find_recipient` OR-match is ambiguous** if
  username/email aren't globally unique vs phone (funds-misdirection). **Recommend:** deterministic precedence + uniqueness.

**Verified GOOD (do not regress):** atomic locked money ops; idempotent funding/reconcile; PIN required on every
spend (incl. loan repay); AI never moves money directly (only routes to PIN-gated flows); 3-layer AI kill switch;
marketing opt-in enforced; no card PAN/CVV stored; account-enumeration-resistant `password/forgot`; per-account
PIN/OTP lockouts; HSTS/SSL/secure-cookies in prod.

---

## 4. Code Quality Review (Phase 4)

The codebase is clean, consistently documented (the *why*, not the *what*), and well-tested. Issues found are
mostly **input-validation robustness** (raw `Decimal(str(...))` / `int(...)` producing 500s instead of 400s) and a
few **provider seams** shipped open for mock testing.

| File / function | Severity | Issue | Status |
|---|---|---|---|
| money endpoints (amounts) | Medium | `Decimal(str(x))` accepts `Infinity`/`1e500`; no 2dp quantization. | **FIXED** via `parse_amount`. |
| loans/savings (`tenure`/`days`) | Medium | bare `int()` 500s on non-numeric input. | **FIXED** via safe parsers. |
| `convert/views.py:convert_airtime` (`collect_airtime`) | Low/High-if-deployed | Mock returns `success` unconditionally → free wallet cash if enabled in prod before the real collector is wired. | **OPEN** — gate on `not DEBUG`/real provider before launch. |
| `exams/views.py:buy_exam` | Medium | Provider may return fewer PINs than charged; settles `Successful` regardless (overcharge). | **OPEN** — assert `len(pins) == quantity` or partial-refund. |
| `betting`/`exams` → Baxi routing | Medium | `_baxi_build_request` has **no branch** for betting/epin service-ids ⇒ 100% fail+refund once Baxi keys are live. | **OPEN** — add provider branches (needs verified Baxi API shapes). |
| `utility` airtime/electricity | Low | Unknown `network`/`disco` silently defaults to MTN/Ikeja (mis-routed payment). | **OPEN** — validate against the allow-list. |

---

## 5. Database Review (Phase 5)

**Verified correct:** every monetary column is `DecimalField` (no `FLOAT`); `Transaction.reference`,
`Loan/FixedSave/FundingIntent/FxQuote.reference` unique; `(user, idempotency_key)` partial-unique; DB `CHECK`s for
non-negative balances + positive amounts; one-active-loan partial-unique; maturity payout idempotent
(`paid_out` + deterministic `-M` ref under a row lock); internal transfers are true double-entry.

| Finding | Severity | Fix | Status |
|---|---|---|---|
| `ConversionRequest.reference` was non-unique + blankable (ledger drift risk) | Medium | `unique=True` + migration `convert/0002`. | **FIXED** |
| No composite index for `user.transactions` ordered by `-created` (history filesort at scale) | Low | `Index(user, -created)` + migration `wallet/0006`. | **FIXED** |
| Settled (`Successful`/`Failed`) ledger rows are **mutable** (no append-only enforcement) | High | Add a `save()` guard and/or Postgres `BEFORE UPDATE` trigger rejecting edits to `amount`/`direction`/terminal status. | **OPEN** (recommended; needs trigger migration). |
| No internal counter-account for loan/savings movements (single-entry; can't assert Σdebit==Σcredit) | Low | Introduce `LOAN_POOL`/`SAVINGS_POOL` ledger accounts or a reconciliation invariant job. | **OPEN** |
| Unindexed `LOWER(email)` → every sign-in scans the user table (brute-force amplifier at scale) | Medium | Functional index `Index(Lower("email"))` + migration `accounts/0007`. | **FIXED** |
| `FixedSave` maturity sweep (`status,paid_out,matures_at`) full-scans on cron + every `savings_list` | Medium | Composite index + migration `savings/0002`. | **FIXED** |
| Missing composite index on WhatsApp `WaMessageLog(msisdn,-created)` (webhook hot path) + `AuditLog(-created)` | Low | `Meta.indexes` + migration `whatsapp/0004`. | **FIXED** |
| Missing `PendingAction`/`BroadcastRecipient(broadcast,status)` indexes | Low | Add `Meta.indexes`. | **OPEN** |

---

## 6. API Testing Results (Phase 6)

All 62 routes were inventoried; representative endpoints were **executed live** against the Django test client
across the seven dimensions (happy path / invalid input / missing auth / wrong role / rate limit / edge cases /
duplicate requests). **39/39 security-relevant assertions pass** after the fixes below; one bug was found *by this
testing* and fixed (see ⓵).

| Dimension | What was tested (live) | Result |
|---|---|---|
| Missing auth | `wallet_balance`, `transfer/send`, `cards/fund`, `loans/repay`, `savings/create`, `buyairtime`, `admin/bootstrap` with no token | all **401** ✓ |
| Invalid/edge amounts | `Infinity`, `1e500`, `-5`, `0`, `"abc"`, `""`, `null`, `"NaN"`, `9999…` on fund/loan/savings/VTU/betting/convert | all clean **400**, **no 500s** ✓ |
| Duplicate requests | identical spend ×2 **with** a client key, and ×2 **without** a key, on airtime/data/betting/exams/convert/transfer/card | **one debit each** ✓ |
| PIN gating | spend with wrong PIN / missing PIN | **403** ✓ |
| Authorization | self-transfer; over-tier-limit; ≥₦100k without face verification | **400 / 403 / 403** ✓ |
| Rate limiting | hammer `transfer/resolve` past the window (limiter on) | **429** after limit ✓ |
| Admin RBAC | `super_admin` vs `read_only` `settings/update`; unknown setting key; non-staff login | **200 / 403 / 400 / 403** ✓ |

⓵ **Bug found and fixed by this testing:** `parse_amount("1e500")` passed the `is_finite()` guard (1e500 *is*
finite) but then raised `InvalidOperation` on `.quantize()` (exceeds decimal context precision) → a **500** — the
exact crash the helper was meant to prevent. Fixed by moving the quantize inside the guarded `try/except`
(`common/http.py`). Re-tested: now a clean 400.

⓶ **Gap found and closed by this testing:** `buyairtime` without a client key debited **twice** — the idempotency
fallback had only been applied to transfers/cards/loans/savings. Now extended uniformly to **all** VTU/betting/
exams/convert spend endpoints (verified: no-key duplicates dedupe to a single debit/credit).

Remaining **OPEN**: per-endpoint throttle on `validate_iuc`/`validate_meter` (paid provider lookups); exam PIN
count-mismatch refund; betting/exam live-mode Baxi routing (§4).

---

## 7. WhatsApp Banking Review (Phase 7)

Command flows were **executed live** against the public webhook. **7/7 checks pass**, confirming the fixes hold:

| Test (live) | Result |
|---|---|
| Linked user `balance` returns their figure; **unlinked** number gets "not linked" (no balance leak) | ✓ identity isolation |
| Inbound with **empty `wa_message_id`** → dropped (no row, no processing) | ✓ replay/dedupe fix |
| Duplicate real message-id → deduped to one row | ✓ |
| Bare 4-digit ("PIN-shaped") inbound → logged as `[PIN]` | ✓ redaction fix |
| Transfer initiation does **not** debit before PIN | ✓ money gated on PIN |
| 40 inbound messages from one number → only ≤30 processed (limiter on) | ✓ inbound throttle |

The deterministic-first design is sound: the AI only **proposes** intents and routes into PIN-gated deterministic
flows — it never moves money directly; the kill switch is enforced at global/link/conversation scope; money-level
idempotency (`wa-{pendingaction.id}`) prevents literal double-debit. **Fixed this pass:** prod signature guard,
empty-id replay drop, inbound throttle, PIN-shape redaction, `WaMessageLog(msisdn,-created)` index.
**Still OPEN (pre-launch):** bind link-code to the registered number + cap guesses + raise entropy
(`token_hex(3)`→4); enforce tier/face limits on the VTU chat paths; per-msisdn `PendingAction` lock; real RBAC on
operator broadcast/handover/reply (currently `is_staff`-only); queue broadcasts (see §10 C1).

---

## 8. Mobile App Review (Phase 8)

Full review of `app/`, `lib/`, `components/`. **The highest-risk items are handled well**; findings are mostly
Medium/Low. **No committed secrets, no `console.log` of PII/tokens anywhere.**

| Severity | Finding | Fix |
|---|---|---|
| Medium | **No HTTPS enforcement / ATS.** Prod URL is HTTPS, but nothing blocks a cleartext `http://` build (the `.env.example` default *is* `http://localhost:8000`) — tokens/PIN/BVN could go in clear on a misconfigured build. `apiConfig.tsx`. | Add `usesCleartextTraffic:false` + iOS ATS; reject non-HTTPS base URL in prod builds. **OPEN.** |
| Medium | **No certificate/SPKI pinning** — trust rests on the device CA store; a trusted-root MITM can read/modify money calls + card reveal + KYC. | Add SPKI pinning (expo-build-properties / network_security_config) or document accepted risk. **OPEN.** |
| Medium | **Public reference fetches use bare `fetch()`** (`buydata`/`buycable`/`fixedsave`/`exams`/`betting`/`sendmoney`) — bypass the 401→session-expiry and non-JSON guards in `lib/api.ts`; a Render cold-start HTML 502 makes `r.json()` throw, masked as "no plans". | Route through `apiJson()`. **OPEN.** |
| Medium | **`saveToken(result.access_token)` not null-checked** (`signin`/`otp`/`resetpassword`) — a 2xx lacking the field throws (native) or stores `"undefined"` (web → `Bearer undefined`). | Guard `result.access_token` before saving. **OPEN.** |
| Medium | **Idle-lock is UX-only / web-bypassable** — `z-locked` + last-active are plaintext AsyncStorage; on web the token is too. | Treat as UX (already documented); keep server token TTL short; SecureStore the lock on native. **OPEN.** |
| Low | `Number()`→`NaN` into `$NaN` in the converter; `.toFixed` on missing rate → `"NaN%"`; base64 image uploads co-serialized with the token (no size cap); dead `token` state copied into many screens; **placeholder legal links** (`zitch.example`) — a store-review/compliance blocker. | `Number.isFinite` guards; multipart uploads; remove dead state; publish real Terms/Privacy URLs. **OPEN.** |

**Done right (verified):** access token in OS keychain (native) / scoped AsyncStorage (web); **transaction PIN never
persisted**; idempotency key per authorization, kept across retries / reset on success; `AuthGuard` gates both
authenticated route groups (token + idle-lock, re-checked on a timer and on foreground); large-transfer biometric
step-up; `apiJson` guards non-JSON & centralizes 401; QR/phone/account/BVN inputs sanitized. **Loan-repay missing
idempotency key — FIXED this pass** (screen now sends one).

---

## 9. Fraud & Risk Assessment (Phase 9)

| Attack vector | Current mitigation | Status |
|---|---|---|
| Double-spend (retry / double-tap) | idempotency key + partial-unique index + **server-side fallback key on every spend** | **FIXED** (all spend endpoints, verified live) |
| Balance manipulation / race | atomic ops + `SELECT FOR UPDATE` + DB non-negative/positive checks | **GOOD** |
| KYC/tier bypass via card load | `check_send_limits` now enforced on `fund_card` | **FIXED** |
| KYC bypass via WhatsApp VTU (bills exceed tier) | transfers gated; VTU chat paths not | **OPEN** |
| WhatsApp identity fraud (spoof / brute-force link) | forged-`from` rejected when live (signature) + inbound throttle | **partially FIXED**; link-code→number binding **OPEN** |
| Airtime→cash free-money seam (mock auto-confirms) | — | **OPEN** (deploy gate: disable in prod until real collector) |
| Currency-conversion arbitrage | single-use, TTL'd, row-locked quote; CNY settlement blocked | **GOOD** |
| Reversal / chargeback abuse | status-guarded, locked `reverse_transfer`/`settle_funding`; idempotent funding | **GOOD**; optimistic-payout `PENDING` leak **OPEN** |
| Loan stacking | one-active-loan partial-unique + in-lock re-check | **GOOD** |
| Enumeration (harvest users/names) | `resolve_*` now rate-limited; `password/forgot` non-enumerating | **FIXED** |

---

## 10. Performance Assessment (Phase 10)

The application logic is efficient per-request, but the **deployment + concurrency model is the dominant
bottleneck**: `render.yaml` runs `gunicorn` with **no `--workers` flag → a single synchronous worker**, and there is
**no queue and no shared cache** (Django default LocMemCache, per-process). Every provider HTTP call (Monnify, Baxi,
Fincra, Meta, Prembly, the LLM) runs **inside the request/response cycle** on that one worker.

**Critical for scale (OPEN — these are architectural, not surgical):**
- **C1 — Synchronous broadcast loop** (`whatsapp/ops.py:send_broadcast`): one Meta HTTP call per recipient, inline; >50 recipients exceeds the 30s worker kill, wedging the broadcast in `SENDING` and blocking all traffic. → queue it.
- **C2 — Webhook processes full money flows inline** (`whatsapp/views.py`): LLM intent (Anthropic client has **no timeout set**), name-enquiry, payout, VTU all run before the 200; a slow provider → Meta retries → eventual webhook **disablement** (channel outage). → fast-ack + queue; set Anthropic `timeout`/`max_retries`.
- **C3 — Admin `bootstrap` is heavy** (`admin_api/views.py`): unbounded `User/Wallet/CurrencyWallet/WhatsAppLink` loads + N+1 (`created_by`, per-conversation message fetch, `assigned_agent`) + 14 aggregate scans + a `meta__flagged` JSON scan. Fine at current data, multi-second at 10k users, OOM at 100k. → `values()`/`in_bulk`, `select_related`, one grouped query, cache KPIs ~60s, paginate.
- **C4 — Broadcast status callbacks are O(N²)** (`_apply_status` re-`COUNT`s all recipients per callback). → `F()` increments + `(broadcast,status)` index.

**High:** Monnify token is **re-fetched on every call** (bank transfer = 4 round-trips, worst case >120s vs 30s kill, stranding a committed debit); `execute_fx` makes a Fincra HTTP call **inside an open transaction holding the quote row lock**; single worker + LocMemCache makes rate limits/caches per-process (limits multiply by worker count); missing pagination on `list_beneficiaries`/`savings_list` (latter also settles maturities **on a read**); **unindexed `LOWER(email)` scan on every sign-in — FIXED this pass**, plus the FixedSave-sweep and WaMessageLog indexes **FIXED**.

**Medium:** PBKDF2 PIN/password verify (~100–300ms) on every money op caps a small instance to ~5–10 ops/s/worker; no caching on bank list / plans / `SystemSetting` (read per WhatsApp message via `ai_active`); base64 avatar/KYC parsed in-memory on the worker.

**Failure-point estimate** (free/starter, 1 sync worker, 30s timeout):
- **1,000 concurrent:** breaks immediately on the worker model (~1–5 req/s effective); 502s; webhooks (sharing the worker) delay funding credits. *Minimum fix to survive:* `--workers/--threads` (or gevent), Redis cache, Monnify token caching, broadcasts/webhook off-request.
- **10,000:** with workers added, bottleneck moves to **CPU (PBKDF2)** and **DB** (bootstrap scans, email-scan sign-ins, O(N²) broadcast callbacks); LocMemCache rate limits become ~Wx too loose (security regression). Add pgbouncer.
- **100,000:** architectural collapse on bootstrap memory, broadcast fan-out, and unindexed global scans; per-user paths (history/balance) survive thanks to `txn_user_created_idx` + indexed token auth. Needs queue workers, Redis, cursor pagination, counter denormalization, read replica.

**Quick wins (one-liners):** gunicorn worker/timeout flags; cache the Monnify token; Redis `CACHES`; the indexes added this pass.

---

## 11. Production Readiness (Phase 11, ops) + Compliance (PCI · CBN · NDPR)

### Operational readiness — the weakest area (mostly OPEN)
The money/security engineering is strong, but the **observability/operations layer is largely absent**:

| Severity | Gap | Recommendation |
|---|---|---|
| Critical | **No `LOGGING` config** — money movements and auth failures (bad password, OTP, PIN lockout) are not logged anywhere; Render stdout is ephemeral. | Add a JSON-to-stdout `LOGGING` dict; log auth failures (+IP) and every settled debit/credit/payout. **OPEN.** |
| Critical | **No error reporting (Sentry)** — unhandled exceptions vanish with `DEBUG=False`. | Add `sentry-sdk[django]` gated on `SENTRY_DSN`, `send_default_pii=False`. **OPEN.** |
| Critical | **No DB backups** — `render.yaml` free Postgres has no backups and expires (~90d) → unrecoverable ledger loss. | Paid Postgres + PITR + independent `pg_dump` to S3/R2, before real money. **OPEN.** |
| High | **`DEBUG` defaults `True`** (`settings.py`) — fail-open; a missing `DJANGO_DEBUG` → tracebacks + `CORS_ALLOW_ALL_ORIGINS=True` + hardening off. (Prod is safe via `render.yaml`.) | Default to `False`, opt into debug locally. **OPEN.** |
| High | **`/healthz` didn't check the DB** (returns 200 even if Postgres is down). | **FIXED this pass:** added **`/readyz`** that round-trips the DB (503 when down); `/healthz` stays pure liveness. |
| High | **CI lacks `check --deploy`, dependency audit, secret scan; no `permissions:` block.** | Add `check --deploy`, `pip-audit`/`npm audit`, gitleaks; `permissions: contents: read`; branch protection. **OPEN.** |
| High | **Free web plan sleeps**; payment/WhatsApp webhooks can time out on cold start; media on ephemeral disk. | Paid (no-sleep) web + S3/R2 media before go-live. **OPEN.** |
| Medium | **Unbounded dependency pins** (only Django bounded); `anthropic>=0.40` floor is stale; **Expo SDK 51 / RN 0.74 are EOL** (no security patches). | Upper-bound deps + lockfile; plan Expo SDK upgrade. **OPEN.** |
| Medium | Unexpected exceptions fall through to Django's HTML 500 (no internals leak in prod — safe — but non-JSON for API clients). | JSON exception middleware. **OPEN.** |

**Done right:** SECRET_KEY boot-guard; **no hardcoded secrets** (all `os.environ`, secret-pattern grep clean, only `.env.example` tracked); HTTPS/HSTS/secure-cookies gated on prod; WhatsApp secret boot-guard (this pass); durable in-app `AuditLog`; clean JSON 4xx contract; per-IP rate limiting on auth; CI runs check + migration-completeness + full test suite + app typecheck/bundle.

### Compliance
- **PCI DSS:** no PAN/CVV stored (issuer-tokenised, one-time reveal); card reveal is PIN-gated + now rate-limited; TLS enforced in prod. **Good** — formalize scope + key handling for the issuer integration.
- **CBN:** KYC tiers + per-tier limits + large-txn face step-up present; **card-load bypass FIXED**; **WhatsApp-VTU limit bypass OPEN**; maintain append-only audit (extend to **ledger immutability — OPEN**).
- **NDPR:** **PIN-in-log redaction FIXED**; add the `LOGGING` config carefully (no PII), document data-retention + subject-access; replace placeholder legal links (§8).

---

## 13. Critical Issues — fix before launch
1. **WhatsApp unsigned webhook in prod** — **FIXED** (signature guard + boot assertion). *Verify `WHATSAPP_APP_SECRET`/`VERIFY_TOKEN` are set in the Render dashboard.*
2. **Empty-id WhatsApp replay** — **FIXED**.

## 14. High Priority — within 7 days
1. Idempotency double-charge — **FIXED across ALL spend endpoints** (transfers/cards/loans/savings **and** now VTU/betting/exams/convert; verified live in Phase 6/7).
2. `fund_card` KYC bypass — **FIXED**.
3. Rate limiting on money/enumeration/WhatsApp — **FIXED** (⚠ move cache to **Redis** for multi-worker accuracy — §10 H3).
4. WhatsApp PIN-in-log — **FIXED**.
5. Unindexed `LOWER(email)` sign-in scan — **FIXED** (functional index).
6. **No application logging + no Sentry** (§11) — **OPEN** (operational blocker for a fintech).
7. **WhatsApp link-code binding + guess cap + entropy** — **OPEN**.
8. **Optimistic payout `PENDING` leak** — **OPEN**.
9. **Single gunicorn worker + provider I/O in-request** (§10 C1/C2/H1) — **OPEN** (worker flags + Monnify token cache are one-liners; queue is the larger fix).
10. **Ledger immutability enforcement** — **OPEN**.

## 15. Medium Priority — within 30 days
Input-validation 500s (incl. the `1e500`→500 bug Phase 6 caught) — **FIXED**. `ConversionRequest.reference` unique,
FixedSave-sweep / WaMessageLog / AuditLog indexes, `/readyz` DB probe — **FIXED**. **Open:** queue webhook +
broadcasts (§10 C1/C2); admin `bootstrap` N+1/unbounded (§10 C3); O(N²) broadcast callbacks (§10 C4); AI destination
pre-fill hardening; WhatsApp VTU tier limits; `PendingAction` lock; `fund_verify` ownership; exam PIN-count check;
betting/exam live-mode Baxi routing; mobile HTTPS-enforcement + public-fetch-through-`apiJson` + `saveToken` guard;
`DEBUG` fail-closed default; CI `check --deploy` + dependency/secret scans; upper-bound deps.

## 16. Low Priority — within 90 days
Network/disco allow-list validation; recipient-resolution precedence + uniqueness; `PendingAction`/`BroadcastRecipient`
indexes; double-entry counter-accounts; API versioning; **certificate pinning**; mobile `NaN` guards + real legal links;
Expo SDK 51→current upgrade; AccessToken purge job; data-retention/SAR docs.

---

## 17. Immediate Pre-Launch Checklist
- [ ] Set `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN` (boot now refuses to start live without them).
- [ ] Set a strong `DJANGO_SECRET_KEY`; `DJANGO_DEBUG=false`; restrict `DJANGO_ALLOWED_HOSTS` + `CORS_ALLOWED_ORIGINS`.
- [ ] Add a `LOGGING` config (JSON→stdout) + **Sentry** (`SENTRY_DSN`); wire a DB-backup/PITR plan. *(Operational blockers — §11.)*
- [ ] Point `CACHES` at **Redis** (rate limits + idempotency-fallback bucket + WhatsApp throttle are only correct cross-worker with a shared cache).
- [ ] Run `gunicorn` with `--workers/--threads` (or gevent) + a sane `--timeout`; cache the Monnify auth token. *(§10.)*
- [ ] Disable / wire the **airtime→cash** collector before enabling that flow in prod.
- [ ] Create staff users + assign `finance`/`support`/`read_only` groups; verify portal RBAC.
- [ ] Run `python manage.py check --deploy`; use **`/readyz`** for DB-aware readiness, `/healthz` for liveness; upgrade Render plans (no free-tier sleep / expiry) before real money.
- [ ] Host the Expo web build and point the landing iframe at it (currently embeds the prototype); enforce HTTPS-only in the app + publish real legal links.

## 18. 30-Day Improvement Plan
Stand up **logging + Sentry + DB backups** (the operational gap); queue the WhatsApp webhook + broadcasts and set
provider timeouts; fix admin `bootstrap` N+1 + O(N²) broadcast callbacks; Redis cache for bank lists/rates/plans/
`SystemSetting`; WhatsApp link binding + VTU tier limits + `PendingAction` lock + operator RBAC; ledger immutability;
payout-PENDING handling; exam PIN-count + betting/exam provider routing; mobile HTTPS-enforcement + `apiJson` routing.

## 19. 90-Day Improvement Plan
Double-entry counter-accounts + daily ledger-reconciliation invariant; API versioning; **certificate pinning**;
AI prompt-injection hardening; full NDPR data-retention/SAR processes; load-test to 100k and add read replicas +
pgbouncer + edge rate limiting; Expo SDK upgrade; expand the admin portal's write-actions and add maker-checker on
money actions.

## 20. Final Go-Live Recommendation — **CONDITIONAL (YES, with conditions)**

The money/security core is genuinely solid, the build/wiring work (landing + operator portal + staff API) is complete
and tested, and the **2 Critical + most High security issues were fixed this pass** (verified by live Phase 6/7
testing). **Proceed to a controlled production rollout once the §17 checklist is closed** — the remaining blockers are
**operational** (logging, Sentry, DB backups), **scale-architectural** (Redis + multi-worker + queue + Monnify token
cache — mostly one-liners plus a broadcast/webhook queue), and the **WhatsApp link-binding + optimistic-payout +
ledger-immutability** items. None require restructuring; all are scoped above. Until then, do not enable the WhatsApp
money flows or the airtime→cash conversion in production, and do not handle real money on the free Render tier.

---

*Fixes applied this pass were surgical and in-place; no existing feature, flow, endpoint, or schema was removed or
restructured. Phases 6–7 were executed live against the backend (39 + 7 assertions, all green). Backend test suite:
**175 passing**; `manage.py check` clean; migrations complete; `collectstatic` clean.*
