# Wema provisioning and transaction-status safety — 18 September 2026

## Verified evidence

- Face callbacks reached Zitch, but the associated account-creation requests
  returned HTTP 400. The historical error body was not retained, so the precise
  rejection cause cannot be reconstructed. A duplicate/existing account at the
  bank is possible; a 400 alone does not establish that no bank account exists.
- The Frankfurt API has a populated `WEMA_WALLET_KEY`. The earlier missing-key
  diagnosis was not substantiated. No credentials were copied or replaced.
- At approximately 14:21 UTC, a read-only status lookup for a deliberately
  nonexistent diagnostic reference returned HTTP 400 and "You've not been
  profiled to use this service". This is refusal of the **status lookup**, not
  evidence that an earlier purchase failed. Callback profiling and status-product
  access are distinct checks.
- The saved Wema contract describes numeric transaction-status ranges but does
  not give their outcome meanings. Do not invent a mapping from HTTP codes.

## Critical fixes

- Use the same effective face-verifier URL for customer links and browser CORS.
- Record account-creation outcome, HTTP code, and a fixed diagnostic category
  separately from identity verification. Never store the raw error body or BVN/NIN
  in these fields. Legacy outcomes remain unknown.
- Escalate stalled/legacy account setup; do not claim an unsupported bank lookup
  recovered it or keep promising an account callback without acceptance evidence.
- Reject contradictory/malformed status legends. Ambiguous timeout labels mean
  pending, never failure. Unknown codes remain pending.
- Failed/refused status lookups, malformed responses, conflicting outcomes, and
  mismatched references cannot settle or refund a payment.
- An all-pending legend cannot authorize new VAS purchases in the patched code.
  The confirmed payment outcomes are 200 success, 400 failure and 401
  authentication/API failure. Configure VAS and bills separately even though their
  current values match; unknown outcomes remain pending and neither product may
  silently borrow the other's map. Remita still cannot be enabled for live ambiguous
  outcomes until it has an automated requery/callback contract.

## Initial publication blocker (resolved)

The complete backend suite passed: 2,499 tests; system and migration checks passed.
The initial direct Git push failed because command-line write authentication was
not configured (`could not read Username`). Work paused until the owner authorized
publication through the connected GitHub app. That app published the patch without
reading or replacing credentials. The live Frankfurt legend was initially cleared
on API, WhatsApp worker and Wema cron through their existing Render connection.

Initial verification after the configuration-only deployment:

- API, WhatsApp worker and Wema reconciliation cron all report live deployments
  of existing main commit `052305b` (not the unpublished critical patch).
- API runtime returns an empty airtime legend and `vas_can_settle('airtime') == False`.
- `https://api.zitch.ng/readyz` returns HTTP 200 with `status: true`.
- The WhatsApp worker started successfully. No new purchase, forced settlement,
  refund, or account-creation retry was used for verification.
- Render displays a payment-failed warning; the workspace owner should resolve
  billing to avoid service interruption.

## Code release verified

- Published `66f446a978fb6046b1e42f541153547126aecae3` to `main` through the
  connected GitHub app. Every uploaded blob and the entire Git tree matched the
  locally tested source exactly; no unrelated worktree edits were included.
- API-first deployment applied `wallet.0020_face_account_outcome` successfully
  at 14:46 UTC. The API was live before main was advanced for worker deployment.
- The Frankfurt API, WhatsApp worker and all seven Frankfurt cron services report
  live deployments of `66f446a`. API auto-deploy was restored to its original
  On Commit setting after the staged release. Oregon remains suspended.
- Runtime checks confirm the new fields are queryable, the effective face-verifier
  origin is in CORS, timeouts map to pending, and new airtime purchases are blocked.
- A real HTTP OPTIONS request to the public face callback returns HTTP 200 and
  permits exactly the Pilot verifier origin. Public readiness returns HTTP 200.
- The next scheduled Wema reconciliation run finished successfully at 14:51 UTC;
  its summary reported no settlements, refunds or reversals.
- This does not prove bank account issuance or restore status-product access.
  The external evidence requirements below still apply.

## Release and verification

1. Run backend tests and `makemigrations --check --dry-run`.
2. Deploy the API with additive migration `wallet.0020_face_account_outcome`
   applied before starting workers against the new model.
3. Ensure Frankfurt API, WhatsApp worker and Wema reconciliation cron keep the
   unverified legend empty. Do not touch Oregon, secrets, balances or past
   transaction outcomes.
4. Verify API readiness, database migration, shared-cache health, worker health,
   and the next scheduled Wema reconciliation run. Inspect fixed diagnostic
   categories for the next legitimate account-creation attempt; do not replay
   verification or create duplicate bank customers as a diagnostic.

## Still requires bank evidence

- Working `CheckTransactionStatus` access under the deployed channel/subscription.
- Written per-product enum mappings (airtime/data, bills and Remita), followed by
  controlled checks against known final and pending references before enabling
  new purchases.
- Bank-side investigation of the existing verified customer with no attached
  funding account, and an authenticated account-details callback replay if the
  bank has already issued the NUBAN. Do not fabricate, manually infer, or blindly
  recreate an account.

No payment was manually marked successful/failed or refunded by this patch.
