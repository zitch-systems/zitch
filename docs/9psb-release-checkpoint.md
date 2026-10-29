# Release checkpoint — 28 October 2026

**29 October VAS update:** the supplied VAS contract is now covered by shared
backend, mobile and WhatsApp changes, including no-plan billers, required customer
names, duplicate-slip recovery and retained electricity receipts. A traced
request to the attachment-specified identity URL returned HTTP 400 / Bad
credentials. The bank now disputes that URL; see the
[authentication routing correction](9psb-vas-auth-routing.md). The latest wider SQLite suite still has 487 failures and 561 errors.
The migration remains a draft and is not deployed. Current details are in
[the VAS contract review](9psb-vas-contract-20261029.md).

The 9PSB migration is not deployed or approved for public banking. Render is
still serving main commit `30a365b3c248b0831e79f197541939b6181fb5c4` through
deploy `dep-dbh2ete1egvs73991tbg`. PR #561 remains a draft. No deployment or
activation flag was changed by this checkpoint.

## Changes verified at this checkpoint

- Restored the email-verification step in the new app KYC screen. Customers can
  request, confirm, resend or change their email before bank identity collection.
  Failed delivery and wrong codes do not advance. Duplicate requests and stale
  session responses are rejected; leaving the screen clears the code.
- Both KYC status APIs now describe the email requirement enforced by bank
  identity setup. Password-recovery routing retains its separate policy.
- Migrated the shared account identity and PIN regressions to the 9PSB API.
  The chained synthetic journey covers signup, email confirmation, BVN/NIN OTP,
  NUBAN read-back, confirmed and duplicate inflows, airtime checkout/replay,
  transfer/replay, recipient credit only after confirmed bank inflow, transaction
  limits and bank-approved Tier 2. Tier 3 stays blocked without address evidence.
- Historical identity flags and proof remain retained; they cannot authorize
  the new bank's KYC state.
- Archived ten obsolete provider-specific test modules, comprising 409 test
  methods. Their exact paths and content hashes are recorded in
  `retired-provider-test-inventory.json`; their complete contents remain at
  commit `e60196f1bd60fee8b6009eddaaf43c9f9270bfaf`. The original partnership
  backup branch remains `archive/wema-partnership-before-9psb-20261028`.
  These modules required retired wire protocols, hosted face sessions or
  standalone Prembly identity lookup. Shared financial/security suites remain
  enabled. Four retirement tests verify closed routes, fixed provider selection
  and non-dispatching legacy methods. No test-discovery exclusions were added.

## Validation

- `python manage.py test accounts.tests banking transfers.tests zitch_api.test_render_blueprint --noinput`:
  286 tests, 282 passed, four PostgreSQL-only skips on local SQLite.
- KYC screen ownership/onboarding suite: 22 tests passed.
- `tsc --noEmit`: passed.
- The full PostgreSQL run at preceding commit `e60196f` passed migrations and
  focused bank checks, but failed with 652 failures and 993 errors across 3,570
  tests. This checkpoint does not claim those full-suite failures are resolved.
  Its replacement full CI must be reviewed before release.

All identity and payment responses in local tests are synthetic. These results
are not 9PSB UAT acceptance and do not demonstrate live account creation.

## Outstanding release gates

1. Finish the shared backend regression migration and obtain passing full
   PostgreSQL/SQLite CI. Then review and merge the release before deployment.
2. Confirm the VAS identity URL/profile and successful authentication; obtain bank-approved identity fixtures and a
   Zitch-owned funded UAT wallet, and complete joint bank UAT/sign-off.
3. Confirm production credentials/transport, callback acceptance, any production
   IP allowlisting, fees, tier limits, ICAD requirements, history and
   reversal correlation. Do not infer these from sandbox connectivity alone.
4. Complete Prembly's terminal address-decision/report contract before enabling
   Tier 3 address requests. QR/corporate flows remain unfinished; virtual-account
   and card-acquiring technical contracts are still needed.
5. Deploy and verify the API and worker, then perform the ordered maintenance
   cron cutover in `render-maintenance-cutover.md`. No Render bill savings have
   been realized by this checkpoint.

See `9psb-uat-evidence.md` for the last redacted live connectivity results and
`9psb-migration.md` for the product/service matrix.

29 October follow-up: full CI for this checkpoint completed with 487 failures
and 565 errors across 3,188 PostgreSQL tests; all 122 focused PostgreSQL bank/
Render checks passed. Both full backend jobs failed. The technical team has
confirmed no test-environment IP whitelist is needed and requested Basic Auth
callback credentials. The corrected credential attachment is now available but
is identical to the earlier set; VAS authentication still returns HTTP 400.
The callback URL is still undeployed (HTTP 404). See the updated UAT evidence
before sending registration details to the bank.
