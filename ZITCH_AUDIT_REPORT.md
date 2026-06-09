# ZITCH — Comprehensive Audit Report (Phases 1–5)

> Scope: Codebase Discovery · Architecture · Security · Full Code Review · Database — per `ZITCH_AUDIT_PROMPT.md`.
> Posture: audit + surgical in-place fixes only; no features removed, no flows changed, no breaking schema edits.
> Date: 2026-06-09 · Suite at time of audit: **189 backend tests green**.

---

## 1 · Executive Summary

ZITCH is a Django 5.1 + Expo (React Native) fintech platform with an unusually strong money core for its stage: the ledger is double-guarded (application checks **and** DB constraints), every spend path is idempotent end-to-end, PIN checks are brute-force-locked at the row level, and the WhatsApp channel is deterministic-first with the AI layer strictly proposal-only behind three kill switches. Most classic fintech failure modes (double-spend, replay, negative balance, float math) are already engineered against — several were found and fixed in earlier audit passes this cycle (see §3.6).

**The biggest risks are operational, not code:** the platform currently runs in MOCK mode for every provider; go-live safety depends on environment configuration (real keys, `DJANGO_DEBUG=false`, pinned `ALLOWED_HOSTS`, paid/always-on hosting, Redis-backed rate limits) rather than missing logic. Compliance items (BVN/NIN encryption at rest, retention policy, CBN licensing posture) need attention before handling real money at scale.

**Go-live recommendation: CONDITIONAL YES** — conditions in §7.

| Category | Score |
|---|---|
| **Overall** | **82 / 100** |
| Security | 84 / 100 |
| Code quality | 86 / 100 |
| Scalability | 72 / 100 |
| Compliance | 68 / 100 |

---

## 2 · Phase 1: Codebase Discovery

**Stack:** Django 5.1 (plain JSON views, no DRF) · PostgreSQL (prod) / SQLite (dev) · opaque bearer tokens (24h TTL) · WhiteNoise static · Render (web + 2 crons) · Expo SDK 51 / expo-router v3 mobile app · WhatsApp Cloud API · Claude (tool-calling, temp 0) intent layer.

**Size:** ~9.6k backend Python LOC (excl. migrations/venv) · 27 models · ~125 routed endpoints · 54 mobile screens · 189 backend tests.

**Apps:** `accounts` (auth/OTP/KYC) · `wallet` (ledger, funding, FX core) · `transfers` (bank payouts + webhook) · `utility` (VTU: airtime/data/cable/electricity via Baxi) · `exams` · `betting` · `loans` · `savings` (Fixed Save + maturities cron) · `cards` (virtual cards) · `convert` · `whatsapp` (webhook, router, AI, ops services) · `portal` (operator API + web surfaces — added this pass) · `common` (http helpers, rate limiting).

**Third-party integrations:** Monnify (funding/payouts, HMAC-SHA512 webhooks) · Baxi (VTU) · Fincra (FX rates/settlement) · Meta WhatsApp Cloud (HMAC-SHA256 webhook) · Sendchamp (SMS/OTP) · Prembly (BVN/NIN/face KYC) · Anthropic (intent parsing). All run MOCK when keys are blank.

**Config:** `.env`-driven (`backend/.env.example` documents every key); `render.yaml` defines web + `zitch-maturities` + `zitch-reconcile-vtu` crons; health probe at `/healthz`.

**Web surfaces (added this pass):** marketing landing at `/`, interactive prototype at `/prototype/`, operator portal at `/portal/` backed by 25 staff endpoints under `/api/ops/`.

---

## 3 · Phase 2+3: Architecture & Security Findings

Severity scale: **C**ritical / **H**igh / **M**edium / **L**ow. *Status: ✅ fixed this pass · 🔧 fixed earlier this cycle · 📋 recommendation (config/process or post-launch).*

### 3.1 Authentication & authorization
| # | Sev | Location | Finding | Status |
|---|---|---|---|---|
| A1 | H | `portal/views.py:login` | New staff login endpoint shipped without a rate limit (10/5min on app signin) — staff creds are the highest-value brute-force target. | ✅ `@ratelimit("ops_login", 10/300s)`; failed + denied logins audit-logged |
| A2 | H | `accounts` | Token race on concurrent logins; `update_info` uniqueness bypass; unauthenticated set-password/PIN | 🔧 fixed in PRs #20–#22 (token guard, scoped uniqueness, auth-gated) |
| A3 | M | `AccessToken` | Opaque token, 24h TTL, deleted on expiry — sound. No refresh-token rotation; a stolen token is valid until expiry. | 📋 add device binding / rotation post-launch |
| A4 | M | `portal` SPA | Operator token kept in `localStorage` (XSS-reachable). Mitigated: staff-only surface, no user-generated HTML rendered, role re-checked server-side on every call. | 📋 move to httpOnly cookie + CSP when the portal grows |
| A5 | L | RBAC | Portal caps (`super_admin/finance/support/read_only`) enforced **server-side per endpoint** (`portal/roles.py`), mirrored in UI. Group-driven; superuser ⇒ super_admin. | sound |

### 3.2 API security
| # | Sev | Location | Finding | Status |
|---|---|---|---|---|
| B1 | H | `common/ratelimit.py` | Fixed-window, per-IP, **LocMem cache ⇒ per-worker counters** — effective limit multiplies by gunicorn workers. | 📋 point `CACHES` at Redis in prod (env-only change); limits exist on signin/OTP/ops-login today |
| B2 | M | `zitch_api/settings.py` | `DEBUG` defaults **true**, `ALLOWED_HOSTS` defaults `*` — safe values are env-driven (`render.yaml` sets prod), but a mis-deploy fails open. | 📋 set `DJANGO_DEBUG=false` + explicit hosts in every prod env; `check --deploy` is clean with them |
| B3 | M | CORS | `CORS_ALLOW_ALL_ORIGINS = DEBUG` (off in prod); credentials off; explicit origin list via env. | sound |
| B4 | L | All views | JSON body parsing centralized in `@api` (size-bounded by server), no `eval`/format-string sinks, ORM-only (no raw SQL found ⇒ no SQLi surface). | sound |

### 3.3 Payments security (critical section)
| # | Sev | Finding | Status |
|---|---|---|---|
| P1 | C→✅ | **Double-spend / races:** every balance mutation runs `select_for_update` inside `transaction.atomic`; DB `CheckConstraint`s forbid negative balances (`wallet`, `currency_wallet`) and non-positive ledger amounts. Idempotency: unique `(user, idempotency_key)` partial constraint + `idempotent_replay` returns the original outcome on retry. | 🔧 hardened + tested earlier this cycle (PRs #21–#22, #26); re-verified |
| P2 | C→✅ | **Webhook forgery:** Monnify funding + disbursement callbacks HMAC-SHA512-verified before any credit; WhatsApp webhook HMAC-SHA256 (`X-Hub-Signature-256`) with dedupe on Meta message id. MOCK mode (blank keys) accepts unsigned — by design for dev; flagged in §7 so prod always has keys set. | sound (config-gated) |
| P3 | H→✅ | **Provider-timeout limbo:** unknown-outcome purchases stay PENDING (`meta.reconcile`) — never blind-refunded; `reconcile_vtu` cron + new portal requery settle or refund via the provider's answer. Refunds replay-safe. | 🔧 + portal surface this pass |
| P4 | H→✅ | **FX quote replay/staleness:** quotes are single-use, expiry-checked, settled atomically with a tagged ledger pair; CNY corridor blocked from settlement in code. **Added this pass:** per-corridor pause switches (`fx_corridor_*_enabled`, default on) enforced inside `create_fx_quote`, flippable + audited from the portal. | ✅ |
| P5 | M | **Large transactions:** ≥₦100k requires durable server-side `face_verified`; tier caps enforced via one shared `send_limit_error` (HTTP + WhatsApp). | 🔧 sound |

### 3.4 WhatsApp + AI security
| # | Sev | Finding | Status |
|---|---|---|---|
| W1 | H→✅ | Identity: numbers must be explicitly linked (one-time code from inside the authenticated app); unknown numbers only ever see the link flow. PINs gate every movement, masked in logs, flows cancel after wrong-PIN budget, per-user row-locked lockout shared with the app. | sound |
| W2 | M | **Prompt injection:** the LLM can only emit one structured intent (tool-call schema); it cannot execute anything — name-enquiry/validation, confirm, and PIN still gate every naira. Deterministic keywords/menus always run first; handover or any kill switch fully bypasses the model. Worst case = wrong *proposal*, surfaced at confirm. | sound by construction |
| W3 | M | Broadcasts: marketing only to `marketing_opt_in=true`; STOP/UNSUBSCRIBE flips it off; Meta 131049 blocks recorded, never retried. | sound |

### 3.5 Data security & compliance
| # | Sev | Finding | Status |
|---|---|---|---|
| D1 | H | **BVN/NIN stored plaintext** in `accounts_user` columns. Encrypting is a schema-touching change (out of audit scope) but an NDPR/CBN expectation. | 📋 field-level encryption (e.g. `django-fernet-fields`-style) + key in env; 30-day item |
| D2 | M | PII in logs: sweep found no `print`/log of BVN/phone in app code; WhatsApp PINs masked before logging. Django error pages off when `DEBUG=false`. | sound |
| D3 | M | PINs/passwords hashed (Django hashers); card PAN/CVV **never stored** (issuer token + last4 only) — PCI scope minimized to SAQ-A-like posture. | sound |
| D4 | M | NDPR: no data-retention/erasure policy implemented (OTP rows, message logs, audit grow unbounded). | 📋 retention windows + pruning cron; 30-day item |
| D5 | L | Secrets: none hardcoded (sweep clean); all via env; `/healthz` reports booleans only. | sound |

### 3.6 Fixed earlier in this audit cycle (PRs #20, #21, #22, #26 — re-verified)
Auth-gated set-password/set-PIN · OTP attempt caps + single-use · token issuance race · `update_info` uniqueness bypass · transfer idempotency + payout reversal webhook · funding double-credit guard (row-lock + `credited` flag) · PIN lockout shared across channels · face-verification requirement for large transfers · maturity payout idempotency.

---

## 4 · Phase 4: Full Code Review

Reviewed all 13 apps (every non-migration module). Beyond §3, code-level findings:

| # | Sev | File | Finding | Status |
|---|---|---|---|---|
| C1 | M | `portal/views.py` (new) | First cut had a bogus `Sum(0)` annotation in `inbox` and a post-mutation `count()` in `recon_run` (would mis-report `checked`). | ✅ fixed before merge; covered by tests |
| C2 | L | `wallet/forex.py:53`, `whatsapp/ai.py:112` | The only two `except Exception` blocks — both intentional + annotated (margin parse fallback; AI must never break the channel). | accepted |
| C3 | L | `utility/views.py` | Plan-price lookups hit per request; fine at current scale. | 📋 cache `seed_plans` data (60s TTL) at scale |
| C4 | L | History endpoints | `[:100]` caps with no cursor pagination — bounded responses, no N+1 (FKs `select_related` where iterated). | 📋 cursor pagination post-launch |
| C5 | L | Frontend (`lib/`, `app/`) | Tokens via `expo-secure-store`; no secrets in bundle; API base centralized. Typecheck runs in CI. | sound |
| C6 | L | Portal SPA | Runs React 18 + Babel-standalone from unpkg **with SRI hashes** (as the design reference does). Acceptable for an internal staff tool; not for a public surface. | 📋 precompile + self-host when the portal hardens |

No incorrect-calculation, unhandled-rejection, or missing-rollback instances found: money math is `Decimal` end-to-end (zero `FloatField`s), and every mutation path is inside `transaction.atomic`.

---

## 5 · Phase 5: Database Review

**Schema integrity — pass.** All financial amounts `DecimalField`; refs unique + indexed everywhere (`Transaction`, `FundingIntent`, `Loan`, `FixedSave`, `FxQuote`); FKs with explicit `on_delete`; NOT NULL by default on critical fields.

**Ledger integrity — pass.** Single-table ledger with signed-by-direction positive amounts (`txn_amount_positive`); FX settles as a debit/credit **pair** tagged by currency; balances can't go negative at the DB (`wallet_balance_non_negative`, `currency_wallet_balance_non_negative`); confirmed rows are never mutated by app code (status transitions only via settle paths). `(user, idempotency_key)` unique backstops dedupe under race.

**Indexes — gaps found, fixed this pass ✅:**
| Index | Why |
|---|---|
| `txn_user_created_idx (user, -created)` | history screens + portal lists page a user's ledger newest-first |
| `wamsg_msisdn_created_idx (msisdn, created)` | operator inbox replays one number's thread |
| `audit_created_idx (-created)`, `audit_action_idx` | audit list ordering; recon's `action` prefix filters |

(Migrations `wallet/0006_transaction_txn_user_created_idx`, `whatsapp/0004_…indexes` — additive only.)

**Transaction safety — pass.** Every financial operation wrapped (`credit`, `execute_payout`, `run_provider_purchase`, `execute_fx`, `settle_funding`, maturity payouts); failures roll back atomically; refunds idempotent.

---

## 6 · Priorities

**Critical (before launch)** — all environment/config:
1. Real provider keys set ⇒ unsigned-webhook MOCK acceptance disappears; verify each webhook with a signed test event.
2. `DJANGO_DEBUG=false`, explicit `DJANGO_ALLOWED_HOSTS`, strong `DJANGO_SECRET_KEY` (render.yaml already wires these — verify on the live service).
3. Paid/always-on Render plan + managed Postgres with backups (free tier sleeps; webhooks need always-on) + enable both crons.

**High (≤7 days):** Redis-backed cache for shared rate limits · Sentry (or equivalent) error reporting · staff accounts into role groups + 2FA on Django admin · CDN/edge rate limiting in front of `/api/`.

**Medium (≤30 days):** BVN/NIN field-level encryption (D1) · NDPR retention/pruning policy (D4) · portal token → httpOnly cookie + CSP (A4) · precompile portal JSX, self-host React (C6) · token rotation (A3).

**Low (≤90 days):** cursor pagination (C4) · plan caching (C3) · queue for webhook fan-out at scale · load test at 1k/10k concurrent (current sync-worker model is the first bottleneck; Postgres connection pooling via `conn_max_age` is set).

---

## 7 · Go-Live Recommendation

**CONDITIONAL YES.** The money core is launch-grade: idempotent, atomic, DB-constrained, brute-force-locked, replay-safe, and 189 tests pin those properties. Conditions: the three **Critical** environment items above, plus the four **High** items inside the first week. The **Medium** compliance pair (D1 encryption, D4 retention) should land within 30 days of handling real customer volume — they are regulatory exposure, not exploit exposure.

---

*Phases 6–11 (endpoint-by-endpoint API testing, WhatsApp command-flow battery, mobile deep-dive, fraud red-team, load profiling, production-readiness drill) are specced in `ZITCH_AUDIT_PROMPT.md` and intentionally out of this pass's scope.*
