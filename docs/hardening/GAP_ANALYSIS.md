# Enterprise Hardening — Gap Analysis & Fix Summary

Triage of `ZITCH_CLAUDE_ENTERPRISE_HARDENING.md` against the actual codebase.
Much of the plan was already implemented; this records what's DONE, what this
batch ADDED, and what's deliberately DEFERRED (with reasons). Zero-disruption
rule respected: everything additive, no schema rewrites, no API breaks.

## ✅ Already implemented (verified, with tests)

| Plan item | Where |
|---|---|
| Secrets: fail startup on missing prod SECRET_KEY | `settings.py` raises `ImproperlyConfigured` when DEBUG off |
| Idempotency framework | `Transaction.idempotency_key` + unique `(user, key)` constraint, `spend_key`, `idempotent_replay`, `DuplicateTransaction`; webhook credits keyed on unique ledger `reference` |
| Immutable transaction history | `Transaction.save()` rejects any change to amount/direction/currency; append-only ledger |
| Pessimistic locking for money movement | `select_for_update` on every debit/credit/transfer/settle, deadlock-ordered dual locks |
| DB-level balance guarantees | `CheckConstraint balance >= 0`, unique account number/reference, positive amounts |
| Audit trail (DB, append-only) | `AuditLog` + `record_audit` — webhooks, reconcile runs, admin/ops actions |
| Rate limiting | `@ratelimit` on auth/OTP/funding/account endpoints |
| Per-txn + daily limits by KYC tier | `check_send_limits`, `check_daily_limit` |
| Large-transfer face step-up | `LARGE_TXN_THRESHOLD` + durable `face_verified` |
| Reconciliation engine (provider side) | `reconcile_vtu` cron (requery + settle/refund), payout webhook settle/reverse |
| Observability: Sentry | already wired in settings (DSN-gated, PII off) |
| Biometric transaction approval (mobile) | biometric-first PinPad + settings toggle |
| Screen-capture protection, PIN hardening (mobile) | earlier hardening PRs |
| Webhook signature verification | Kora + Monnify (HMAC-SHA512) fail closed in prod |

## ➕ Added in this batch

1. **Ledger↔balance reconciliation** — `manage.py integrity_check`: recomputes
   every wallet from the append-only ledger (`IN·Successful − OUT·(Pending|Successful)`)
   and flags any drift, writing a snapshot to the immutable `AuditLog`.
   `--fail-nonzero` for cron/CI alerting. This is the plan's "ledger guarantees +
   balance snapshots + reconciliation" control, implemented additively — the
   ledger itself is the double-entry source of truth.
2. **Fraud velocity guard** — `check_velocity` inside `check_send_limits` (every
   send path funnels through it): >`VELOCITY_MAX_OUT_10MIN` (default 20) outbound
   movements in 10 min → 429. Env-tunable, 0 disables, off in tests.

## ⏸ Deferred (with reasons — revisit when scale demands)

| Item | Why deferred |
|---|---|
| Celery/Redis queues + Redis cache | Requires paid infra (Render free tier); current volumes are served synchronously + LocMem. Revisit at real user volume. |
| Separate ledger_accounts/journal tables | The existing append-only `Transaction` ledger + integrity_check gives the guarantee; a parallel double-entry schema is a rewrite the plan itself forbids ("do not rewrite architecture"). |
| webhook_events table | Redelivery is already idempotent (unique ledger refs). A forensic event log is nice-to-have; add with the queue work. |
| Device fingerprinting / geo-anomaly / risk scoring | Needs device data collection the app doesn't gather yet; velocity guard covers the acute drain scenario now. |
| Root/jailbreak detection, cert pinning, device binding | Mobile-release work (app PR + APK). Schedule as its own release. |
| Maker/checker, RBAC matrix, admin MFA | Operator portal exists with audit logging; multi-admin workflows become relevant with a real ops team. |
| AML/SAR workflows, GDPR export/delete, disputes | Compliance-process features; the compliance doc pack was generated earlier — wire to process, not code, first. |
| Partitioned txn tables, materialized views | Premature below millions of rows. |

## Rollback plan (this batch)

Both additions are independent and reversible without data impact:
- `integrity_check` is read-only (plus one AuditLog row per run) — delete the
  command file to remove.
- Velocity guard: set `VELOCITY_MAX_OUT_10MIN=0` (env) to disable instantly, or
  revert the `check_velocity` block in `common/http.py`. No migrations shipped.

## Operations

- Schedule `python manage.py integrity_check --fail-nonzero` daily (cron) once on
  a paid plan; until then run it manually after any money-code deploy.
- Optional: set `SENTRY_DSN` in Render to activate error reporting.
- Optional: tune `VELOCITY_MAX_OUT_10MIN` (default 20/10min).
