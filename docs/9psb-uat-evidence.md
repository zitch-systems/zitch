# 9PSB sandbox UAT evidence

**Activation is blocked.** These results establish connectivity and account reads,
not completed customer KYC, settlement or production acceptance.

Last authentication diagnosis: 28 October 2026, 21:23 WAT. The available credential attachments
are two identical copies of the earlier test set. The separately referenced
replacement credential file is absent from the restored workspace.

| Scenario | Observed result |
|---|---|
| WaaS authentication | Passed against the bank sandbox |
| Bank directory | Passed; bank-issued routing codes normalized |
| Supplied sandbox wallet read | Passed; customer NUBAN matched |
| Supplied sandbox wallet name enquiry | Passed; bank and NUBAN matched |
| VAS authentication | Failed: HTTP 400, provider response code 400 |
| VAS catalog, network and data plans | Blocked by VAS authentication |
| BVN, NIN and facial ownership; account opening | Unverified; bank-approved identity/face fixtures are unavailable |
| Funded transfer, exact fee debit and pending recovery | Unverified; requires a verified Zitch test wallet, authorized test recipient and confirmed fees |
| VAS purchase and delivered entitlement | Unverified; requires working VAS authentication |
| Bank-delivered callbacks, duplicates and reversals | Unverified end to end; local contract tests are not bank delivery evidence |
| Tier limits and PND | Unverified; Tier 2 deposit amount and Tier 3 single/daily/directional limits remain ambiguous in supplied correspondence |
| Prembly address and Tier 3 upgrade | Blocked on terminal approval/report contract and bank acceptance tests |

The supplied shared test debit account was read only. It was not attached to a
Zitch customer, credited into a local wallet, used to send funds, or treated as
identity ownership proof. No production activation switches were changed.

## Fixes driven by observed sandbox responses

- Wallet enquiry returns both an internal wallet number and a distinct NUBAN.
  Account binding, recovery and balance checks now use the NUBAN when present;
  an invalid/missing value in that field cannot fall back to an internal number.
- Some routing codes have trailing newlines. Directory normalization strips
  surrounding whitespace, validates numeric routing codes and refuses conflicting
  response codes. Cached results are bound to environment and credentials.
- Accepted/partial HTTP responses cannot complete identity checks or settle money.
  Conflicting identity outcomes and malformed bank/biller names are refused.
- Historical address flags remain visible as historical evidence and cannot be
  displayed as completed current Prembly verification or unlock Tier 3.

## Regression evidence

The expanded local run completed 272 tests: 267 passed and five PostgreSQL-only
checks were skipped. Separate authentication/UAT checks passed after adding
redacted HTTP diagnostics. Existing tests for locked spending limits, immutable
schema retirement, archived liabilities and pending address evidence were migrated
to the new bank model; they were not removed to obtain a passing result.

The latest completed full CI at `028919a` ran 3,555 tests on PostgreSQL and ended
with **673 failures and 1,015 errors**. Its 103 focused bank/Render checks passed,
as did App and Meta connector CI. Full regressions remain a release gate.

## Repeating the connectivity check

Configure sandbox credentials only in the server environment. A private fixture
JSON may contain `account_number` and `mobile`, using bank-provided test values.
Do not commit credentials, identities or the private fixture file.

```sh
python manage.py bank_sandbox_check --fixtures /private/path/9psb-test-fixtures.json
```

The command rejects production configuration and identity fields, performs no
purchases or ledger writes, and prints only redacted evidence. Its output always
distinguishes connectivity from UAT completion and activation readiness.

Before activation, resolve full backend regressions, supply working VAS credentials
and bank-approved identity fixtures, complete funded transaction/callback UAT,
confirm fees and ambiguous limits, and complete the missing provider contracts.
Production additionally requires HTTPS production credentials and bank UAT/ICAD
approval. Sandbox keys are not production activation credentials.
