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

| Surface | URL | Status |
|---|---|---|
| Marketing landing page | `/` | Served from Django (`console` app). Self-contained, responsive (≤1020/≤760/≤480/≤340), light/dark, live WhatsApp demo, embeds the app prototype. |
| App prototype | `/app/` | Served from Django; embedded by the landing hero iframe (`?embed=1`). |
| Operator / admin portal | `/portal/` | React-in-browser SPA, now backed by a **real Django staff API** with login, RBAC, and audited actions. |
| Staff API | `/api/admin/*` | New `admin_api` Django app — see below. |
| Liveness probe | `/healthz` | Moved off `/` (which now serves the landing); `render.yaml` health-check path updated. |

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

> **Operator setup:** create a staff user (`python manage.py createsuperuser` → `super_admin`), then sign in at `/portal/`.
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

- **2 Critical** — WhatsApp webhook accepts **unsigned** callbacks when `APP_SECRET` is unset (no prod guard);
  empty WhatsApp message-ids **bypass replay dedupe**. Both enable victim impersonation / replay on the public webhook.
- **~9 High** — optional idempotency keys (double-charge on retry across every spend endpoint), `fund_card`
  bypassing KYC limits, **no rate limiting** on money/enumeration/WhatsApp-inbound endpoints, WhatsApp
  link-code brute-force + identity-binding gaps, and PIN values reaching the message log in clear.
- Plus Medium/Low correctness, validation, and performance items.

**Go-live recommendation: CONDITIONAL.** The Critical items and the High items in §13–14 must be closed first.
Many of the highest-impact ones were **fixed in this pass** (marked below); the remainder are scoped with exact
remediation. With those closed, the platform is launch-ready for a controlled rollout.

### Production Readiness Scores

| Category | Score | Notes |
|---|---:|---|
| **Overall** | **78 / 100** | Strong core; channel + hardening gaps to close. |
| Security | 74 / 100 | Excellent money primitives; WhatsApp webhook + rate-limit gaps drag it down (several fixed this pass). |
| Code Quality | 85 / 100 | Clean, documented, tested (174 tests). Input-validation 500s and a few seams remain. |
| Scalability | 72 / 100 | Synchronous webhook/broadcast processing, missing caches/indexes (some added). |
| Compliance (PCI/CBN/NDPR) | 70 / 100 | KYC tiers + audit log + no card PAN storage are good; PII-in-logs, ledger immutability, RBAC depth remain. |

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
| Missing composite indexes on WhatsApp hot paths (`PendingAction(msisdn,-created)`, `WaMessageLog(msisdn,direction,-created)`, `AuditLog(action/target/actor)`) | Low | Add `Meta.indexes`. | **OPEN** |

---

## 6. API Testing Results (Phase 6, summary)

Endpoint-by-endpoint review against happy-path / invalid input / missing auth / wrong role / rate limit / edge
cases / duplicate requests. Representative findings (full matrix folded into §3–§5):

| Endpoint | Issue | Status |
|---|---|---|
| `POST /api/transfer/resolve/`, `/api/transfers/resolve/` | enumeration oracle, unthrottled | **FIXED** (rate-limited) |
| `POST /api/cards/fund/` | no KYC limit; unthrottled; unsanitised amount | **FIXED** |
| all spend endpoints | duplicate request not deduped when key omitted | **FIXED** (server fallback) where touched; VTU **partially OPEN** |
| `POST /api/savings/*`, `/api/loans/*` | `Infinity`/`int()` → 500 | **FIXED** |
| `POST /webhooks/whatsapp` | unsigned accepted (no key); replay via empty id; no throttle | **FIXED** |
| `GET /api/admin/*` | new — auth/role enforced; verified 401 unauth, 403 under-privileged | **NEW, tested** |

All 174 backend tests pass post-change.

---

## 7. WhatsApp Banking Review (Phase 7)

The deterministic-first design is sound: the AI only **proposes** intents and routes into PIN-gated deterministic
flows — it never moves money directly, and the kill switch is enforced at global/link/conversation scope. Money-level
idempotency (`wa-{pendingaction.id}`) prevents literal double-debit. **Fixed this pass:** prod signature guard,
empty-id replay drop, inbound throttle, PIN-shape redaction. **Still open (pre-launch):** bind link-code to the
registered number + cap guesses + raise entropy; enforce tier/face limits on VTU chat paths; per-msisdn
`PendingAction` lock; real RBAC on operator broadcast/handover/reply (currently `is_staff`-only); queue broadcasts.

---

## 8. Mobile App Review (Phase 8)

- **Token storage:** `expo-secure-store` on native, AsyncStorage on web — appropriate. Idle-timeout lock + biometric
  gating present. No hard-coded secrets found in app source (API base in `components/configFiles/apiConfig`).
- **Idempotency:** the app generates and reuses a per-authorization key on spends (good) — loan repay was the one
  gap, **now fixed** (sends a key).
- **Recommend (OPEN):** certificate pinning for the API host; confirm no PII in client logs; ensure the web build is
  hosted before pointing the landing iframe at the real app (it currently embeds the prototype).

---

## 9. Fraud & Risk Assessment (Phase 9)

| Vector | Current mitigation | Residual / fix | Status |
|---|---|---|---|
| Double-spend (retry/double-tap) | idempotency key + unique index | server fallback when key omitted | **FIXED** (loans/transfers/cards/savings); VTU partial — **OPEN** |
| Balance manipulation / race | row locks + atomic + DB checks | — | **GOOD** |
| KYC/tier bypass via card load | — | `check_send_limits` on `fund_card` | **FIXED** |
| KYC bypass via WhatsApp VTU | transfers gated only | gate VTU steps | **OPEN** |
| WhatsApp identity fraud | link code | unbound code + brute-force | **partially FIXED** (throttle); binding **OPEN** |
| Airtime→cash free-money seam | mock returns success | gate in prod | **OPEN** (deploy gate) |
| FX arbitrage | single-use, TTL'd, locked quote; CNY blocked | — | **GOOD** |
| Reversal/chargeback abuse | locked, status-guarded `reverse_transfer`/`settle_funding` | optimistic payout `PENDING` leak | **OPEN** |

---

## 10. Performance Assessment (Phase 10)

- **1k users:** fine. First pressure points: synchronous WhatsApp webhook handling and the LocMemCache rate limiter.
- **10k:** synchronous broadcast loop + per-message inline DB writes on the webhook will back up workers; missing
  composite indexes (some added) start to bite on history/maturity sweeps.
- **100k:** needs queue-backed webhook/broadcast/notification processing, Redis-backed cache + rate limits, read
  replicas, and caching of bank lists / FX rates / plan catalogues.
**Quick wins:** Redis cache; queue the webhook + broadcasts; cache `list_banks`/plans/rates; the added indexes.

---

## 11. Compliance Assessment (Phase 11 / PCI · CBN · NDPR)

- **PCI DSS:** No PAN/CVV stored (issuer-tokenised, one-time reveal) — good. Keep card-detail reveal PIN-gated +
  rate-limited (**done**) and TLS-only (**enforced in prod**).
- **CBN:** KYC tiers + per-tier limits + large-txn face step-up present; **close the card-load and WhatsApp-VTU
  bypasses** (card **fixed**, VTU **open**) so limits hold across all rails. Maintain the append-only audit log
  (extend to ledger immutability — **open**).
- **NDPR:** **Remove PII/PINs from logs** — PIN redaction **fixed**; audit broader logging for phone/BVN exposure
  (**open**). Document data-retention + subject-access processes.

---

## 13. Critical Issues — fix before launch
1. **WhatsApp unsigned webhook in prod** — **FIXED** (signature guard + boot assertion). *Verify `WHATSAPP_APP_SECRET`/`VERIFY_TOKEN` are set in the Render dashboard.*
2. **Empty-id WhatsApp replay** — **FIXED**.

## 14. High Priority — within 7 days
1. Idempotency double-charge — **FIXED** for loans/transfers/cards/savings; **finish VTU/betting/exams** server fallback. 
2. `fund_card` KYC bypass — **FIXED**.
3. Rate limiting on money/enumeration/WhatsApp — **FIXED** (move cache to **Redis** for multi-worker accuracy).
4. WhatsApp PIN-in-log — **FIXED**.
5. **WhatsApp link-code binding + guess cap + entropy** — **OPEN**.
6. **Optimistic payout `PENDING` leak** — **OPEN**.
7. **Ledger immutability enforcement** — **OPEN**.

## 15. Medium Priority — within 30 days
Input-validation 500s — **FIXED**. `ConversionRequest.reference` unique — **FIXED**. Open: AI destination
pre-fill hardening; WhatsApp VTU tier limits; `PendingAction` lock; `fund_verify` ownership; exam PIN-count check;
betting/exam Baxi routing; queue webhook/broadcasts; Redis cache + rate limits.

## 16. Low Priority — within 90 days
Network/disco allow-list validation; recipient-resolution precedence + uniqueness; WhatsApp/AuditLog composite
indexes; double-entry counter-accounts; API versioning; certificate pinning; data-retention docs.

---

## 17. Immediate Pre-Launch Checklist
- [ ] Set `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN` (boot now refuses to start live without them).
- [ ] Set a strong `DJANGO_SECRET_KEY`; `DJANGO_DEBUG=false`; restrict `DJANGO_ALLOWED_HOSTS` + `CORS_ALLOWED_ORIGINS`.
- [ ] Point `CACHES` at **Redis** (rate limits + idempotency-fallback bucket are only correct cross-worker with shared cache).
- [ ] Disable / wire the **airtime→cash** collector before enabling that flow in prod.
- [ ] Create staff users + assign `finance`/`support`/`read_only` groups; verify portal RBAC.
- [ ] Run `python manage.py check --deploy`; confirm `/healthz` is the platform health path; upgrade Render plans (no free-tier sleep / expiry) before real money.
- [ ] Host the Expo web build and point the landing iframe at it (currently embeds the prototype).

## 18. 30-Day Improvement Plan
Finish VTU/betting/exams idempotency fallback; ledger immutability (trigger); payout-PENDING handling; WhatsApp
link binding + VTU limits + `PendingAction` lock + ops RBAC; queue webhook/broadcasts; Redis cache for bank
lists/rates/plans; exam PIN-count + betting/exam provider routing; Sentry + transaction/security log dashboards.

## 19. 90-Day Improvement Plan
Double-entry counter-accounts + daily ledger-reconciliation invariant; API versioning; certificate pinning;
AI prompt-injection hardening; full NDPR data-retention/SAR processes; load-test to 100k and add read replicas +
edge rate limiting; expand the admin portal's write-actions and add maker-checker on money actions.

## 20. Final Go-Live Recommendation — **CONDITIONAL (YES, with conditions)**

The money core is genuinely solid and the build/wiring work (landing + operator portal + staff API) is complete and
tested. **Proceed to a controlled production rollout once the §17 checklist and the remaining §13–14 items are
closed** — specifically: confirm the WhatsApp secrets are set (fixes are in place but depend on config), move the
cache/rate-limiter to Redis, gate the airtime→cash seam, and land the WhatsApp link-binding + payout-PENDING +
ledger-immutability fixes. None require restructuring; all are scoped above. Until then, do not enable the WhatsApp
money flows or the airtime→cash conversion in production.

---

*Fixes applied this pass were surgical and in-place; no existing feature, flow, endpoint, or schema was removed or
restructured. Backend test suite: **174 passing**.*
