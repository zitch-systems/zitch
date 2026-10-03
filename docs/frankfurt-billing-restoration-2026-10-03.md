# Frankfurt billing restoration — 3 October 2026

**HOLD.** On 3 October, the API, WhatsApp worker, Meta connector and seven cron
resources were still billing-suspended. API and Meta maintenance were enabled.
The reviewed application release is `9e0023c7858877c4f6d72357ed07978c0d22035d`;
the latest successful API artifact is still `c00466d`. A successful deployment
record does not prove that a suspended service is running.

This restoration order supersedes the historical September promotion order.
Any later repair must name its exact reviewed SHA and passing CI before deployment;
do not silently substitute a moving branch head for the reviewed release.

## Hold consumers before restoring billing

API maintenance protects incoming public traffic. It does **not** stop the
WhatsApp worker or scheduled jobs. Paying an invoice may resume their old
artifacts before migrations or the repaired API are ready. Their
`migrate --check` is a build command, not a resume-time guard.

Before payment or restoration, obtain a provider-confirmed durable hold for the
worker and all crons, and confirm that Meta remains protected. An unsaved
zero-instance input, a source-code change, a cron schedule change, or a suspended
service's rejected update is not evidence of that hold. Record the supported
service-by-service restoration path and confirm payment authorization separately.

## Restore and verify in order

1. Reconfirm existing Frankfurt resource identities. Keep public maintenance and
   the consumer hold in place. Do not synchronize the Blueprint into duplicate
   resources, replace the ledger, or delete the Oregon database.
2. Restore PostgreSQL and shared cache first. Verify an independent recoverable
   backup and restore test; confirm database TLS and private cache access.
3. Deploy the exact reviewed API SHA. Its build applies migrations; verify actual
   completion, including wallet `0023_transactionalertdelivery` and
   `0024_bankhistorycheckpoint`, and record the deployment ID and commit.
4. Verify `/healthz`, `/readyz` and the authenticated scoped preflight:
   `python manage.py wema_preflight --launch-scope core --strict` (HTTP equivalent:
   `/preflight?launch_scope=core&strict=1`). Resolve the actual failures and warning
   list; do not weaken checks to obtain a green result. No-shell operators can use
   `/admin/diagnostics/` while the service remains protected.
5. Deploy worker, crons and Meta to the same reviewed SHA while their supported
   hold/protection remains effective. The Meta connector needs an explicit deploy.
   Verify effective configuration per service without exposing secrets.
6. Use the provider-supported path to release consumers only after schema and
   readiness verification. Inspect durable inbound/outbound queues, transaction
   alert review/exhausted counts, bank-history checkpoints and each job's last
   successful run before controlled catch-up. Preserve idempotency and shared
   locks; never replay a financial write simply to resolve a pending status.
7. Prove signed webhook acceptance, durable enqueueing, worker drain, real SMS OTP,
   the attributable customer BVN/airtime case, alerts and bank/ledger reconciliation.
   Record references and results without copying customer identifiers into logs.
8. Observe normal scheduled processing and resolve outstanding provider/device
   gates before removing public maintenance. Full-product launch remains blocked
   while savings, loans, FX, airtime-to-cash and card top-up lack proved settlement.

## Identity support and deployment costs

Provider or OTP downtime must leave identity verification retryable. It does not
create provider proof or authorize staff to flip BVN, NIN or liveness flags.
Existing hash-only submissions need the customer's secure verification flow;
clearing an unverified submission must preserve completed identity proof.

Preserve the configured build-spend cap. If included build minutes are exhausted,
the cap can block repair builds/pre-deploy work; review the allowance before this
sequence rather than repeatedly triggering unsuccessful deploys. It does not cap
runtime, storage, cron or bandwidth charges. Retire old resources only after
backup, consumer and rollback evidence supports the cleanup.

For the full provider, money and mobile evidence checklist, use the
[go-live audit](go-live-audit-2026-10-01.md). Passing source tests alone does not
certify a deployed customer journey.
