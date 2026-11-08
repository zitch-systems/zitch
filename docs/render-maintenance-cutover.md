# Render maintenance consolidation

Status: prepared and schema-validated; **not applied**. Keep Blueprint auto-sync
paused until the migration, PostgreSQL tests and channel regression tests pass.
Do not synchronize a new cron alongside the existing seven and assume it saves money.

## Inventory and cost basis

The API and WhatsApp worker already use Starter (0.5 CPU, 512 MB). The database
uses the smallest paid PostgreSQL plan. Retain paid always-on banking services,
database backups, private networking and the persistent no-eviction queue cache.
Observed API memory during 21–28 October was approximately 103–185 MiB.

Render charges each cron by runtime with a $1 monthly minimum. Seven separate
crons therefore have $7 of minimum charges. Consolidating to one removes six
minimums; actual savings depend on billed execution time and the billing period.
This is an estimate, not a measured bill reduction.

Sources reviewed 28 October 2026:
- https://render.com/docs/cronjobs
- https://render.com/docs/blueprint-spec
- https://render.com/docs/configure-environment-variables

## Ordered cutover

1. Confirm database recovery/export coverage and retain the prior main commit.
   Deploy only after all release gates in `9psb-migration.md` are satisfied.
   Run `python manage.py migrate --check` and confirm API `/readyz` and worker
   queue processing. Keep bank customer and VAS flags disabled during cutover.
2. Reuse cron `crn-dalvugvf3r2c73dv0cig`, currently
   `zitch-reconcile-wema-ry6y`. Rename it to **zitch-maintenance-ry6y**. Set schedule
   `*/5 * * * *` and command
   `python manage.py migrate --check && python manage.py run_maintenance`.
   Keep its current Starter plan and Frankfurt region. The new Blueprint names
   match the existing API, worker, database and cache; verify the preview adopts
   these resources and creates no duplicates before applying it.
3. Verify one manual maintenance execution and its persisted `MaintenanceRun`
   results. Each task has an independent lease and period, a PostgreSQL advisory
   lock, and a bounded retry budget. A failed control exits nonzero and does not
   suppress later controls. Financial review findings must remain visible.
4. Suspend these six superseded schedules after the consolidated run is proven:

   | Existing cron | Resource ID | Retained UTC schedule |
   |---|---|---|
   | Maturities | `crn-dalvugvf3r2c73dv0cj0` | 01:05 daily |
   | Integrity | `crn-dalc6re5vjqs73es23r0` | 02:35 daily |
   | Session purge | `crn-dalc6re5vjqs73es23s0` | 03:45 daily |
   | AML | `crn-dalc6re5vjqs73es23rg` | 05:05 daily |
   | Settlement | `crn-dalv4dtbedkc738dtohg` | 06:15 daily |
   | Balance reconciliation | `crn-dalv4e5bedkc738dtoj0` | Every six hours, minute 05 |

5. Observe at least one full daily cycle, including alerts for failed controls,
   before deleting the superseded cron resources. Suspended resources may still
   have minimum charges; inspect the bill. Removing resources or environment keys
   from a Blueprint does not itself delete them from Render.
6. Remove blank retired secret names and unused Prembly face SDK configuration
   from the dashboard. Keep only the address API key for Prembly. Rotate sandbox
   credentials to production values only after bank approval; use the same
   encryption key across API, worker and maintenance to preserve stored evidence.

The current Render connector supports environment updates and deployments but
does not expose rename, schedule/start-command updates, suspension or deletion.
Steps 2, 4 and 5 require the authenticated dashboard or Render CLI/API access.

## Rollback

If scheduling fails, stop the consolidated cron and restore the six original
schedules plus the original reconciliation command. `reconcile_wema` remains a
temporary alias to the new reconciliation command during this rename. Never run
both sets of financial jobs deliberately. Do not reverse the immutable banking
archive migration or restore retired credentials as a scheduling rollback.
