# Frankfurt release control — 2026-09-19

## Purpose

This runbook promotes the audited Zitch release to Frankfurt without changing a
customer balance, refunding a transaction, recreating a funding account, or
removing the Oregon rollback copies.  It is deliberately narrower than a
Blueprint-wide sync because the live Frankfurt service names differ from the
historical local logical names.

## Live targets

| Role | Frankfurt service ID | Current state |
| --- | --- | --- |
| API | `srv-daluk3tbedkc738c87jg` (`zitch-api-ry6y`) | Active; revision `8f49db2` |
| WhatsApp worker | `srv-dalv4dtbedkc738dtofg` (`zitch-whatsapp-worker-ry6y`) | Active; revision `8f49db2` |
| Wema reconciliation cron | `crn-dalvugvf3r2c73dv0cig` (`zitch-reconcile-wema-ry6y`) | Active; revision `8f49db2` |
| Balance reconciliation cron | `crn-dalv4e5bedkc738dtoj0` | Active; revision `8f49db2` |
| Database | `dpg-dalc6du5vjqs73es0n0g-a` (`zitch-db-ry6y`) | Available, Frankfurt |

Do **not** use a full `render.yaml` sync against the workspace until the
service-name mapping has been deliberately reconciled.  The local Blueprint
contains the intended shared configuration, but a broad sync may target the
retained Oregon resources instead of the `-ry6y` Frankfurt services.

## Safe promotion order

1. Merge the reviewed release only after its GitHub checks pass.  The current
   Frankfurt services auto-deploy commits, so this is a production deployment
   decision rather than a staging-only action.
2. Verify that the API has completed migrations and reports the new commit.
   Then verify the worker and each relevant cron reports the same commit.
3. Keep the existing Frankfurt WhatsApp credentials on the **WhatsApp worker**.
   It is now the sole owner of terminal transaction-alert delivery and retries.
   The Wema reconciliation and maturity crons deliberately set
   `TXN_ALERTS_WHATSAPP=false` and carry no Meta credentials; their terminal
   rows remain retryable for the worker.  Do not copy, invent, rotate, or expose
   any secret during this repair.
4. Configure the **Frankfurt balance reconciliation cron** to use the Frankfurt
   shared cache and run `python manage.py reconcile_balances --fail-nonzero`.
   Until the dashboard command can be updated, the existing
   `reconcile_balances --fail-over` invocation is a compatibility alias that
   also exits non-zero for either discrepancy direction. The command remains
   read-only: a discrepancy emits an audited alert and a non-zero exit status;
   it never corrects a balance or changes a transaction.
5. Run one controlled Wema reconciliation and one balance reconciliation.
   Confirm that notification delivery works and that discrepancy records are
   visible to an operator.  A non-zero balance-reconciliation exit is expected
   while unresolved discrepancies exist and is a release hold, not a request
   to auto-correct them.

## Financial holds

The reported balance differences require authenticated bank transaction history
and ledger provenance.  They remain unresolved until two-person review:

| Case | Required evidence | Forbidden shortcut |
| --- | --- | --- |
| Ledger exceeds bank | Bank statement, funding/settlement trace, and ledger rows | Debit balance, refund, or reverse based only on arithmetic |
| Bank exceeds ledger | Bank statement, matched provider reference, and ledger rows | Credit balance or settle a pending payment without bank confirmation |
| User 25 funding account | Confirmed Wema account-creation/recovery result and ownership evidence | Retry/recreate the account or fabricate a NUBAN |

Pending payments remain pending until the bank confirms a terminal outcome.

## External release gates

- **Meta:** the current connector is read-only.  Publish `txn_alert` in WhatsApp
  Manager (`UTILITY`, `en_US`) and approve it before enabling the template.
  Create and publish a replacement Flow before retiring the current approved
  Flow solely to change its display title.
- **PostgreSQL verification:** the Render read-only connector rejects both
  Frankfurt and Oregon with `SSL/TLS required`.  This is a connector TLS defect;
  fix the connector to require TLS, then run only `SELECT` verification.  Do
  not disable TLS, alter the database, or open an IP allow-list.
- **Real integration proof:** conduct controlled real OTP and liveness checks,
  historical-refund provenance review, and one full Frankfurt daily cron cycle.
  These require real provider/device evidence and cannot be simulated as a
  production sign-off.
- **Android:** run the new production AAB workflow only after the genuine upload
  keystore and Play signing setup are available.  Debug signing is not a
  production substitute.

## Oregon retention

Oregon compute remains suspended.  Retain the Oregon database and cache as
rollback copies until all Frankfurt gates pass and the agreed 7–14 day
post-verification window has elapsed.  Do not delete them during this release.
