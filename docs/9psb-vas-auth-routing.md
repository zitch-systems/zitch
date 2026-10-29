# VAS authentication routing correction - 29 October 2026

The bank's latest reply disputes the authentication URL used in earlier tests.
The new PDF and Postman collection still contain that same URL. The integration
must not interpret a matching document, a 400 response, or successful WaaS
authentication as proof that the VAS endpoint/profile is correct.

## Source conflict

| Source | Authentication evidence |
| --- | --- |
| New VAS PDF, page 2 | POST `http://102.216.128.75:9090/identity/api/v1/authenticate`; JSON `username`, `password` |
| New VAS Postman collection | `base_url_auth` is `http://102.216.128.75:9090/identity/api/v1`; request appends `/authenticate` |
| Bank reply supplied by project owner | Quoted endpoint is for WaaS; VAS has a separate identity endpoint |
| Earlier request trace | Observed HTTP 400 / responseCode 400 / Bad credentials at the attachment URL |

Required clarification: the exact VAS identity authentication URL, including
scheme, host, port and path, for Zitch's sandbox profile. If it differs from the
files, request the corrected Postman environment or a redacted example. The
credentials are not established as the cause. No speculative alternate ports,
hosts, credential swapping or repeated login attempts were made in this change.

The newly supplied files are byte-identical to the earlier copies:

| Attachment | SHA-256 |
| --- | --- |
| `VAS API DOCUMENTATION (updated) (1)(3).pdf` | `aeda07f7d42b35c9670477fee340cf091700f2ed61f1e64ce9abf5b07f5c35b1` |
| `VAS API COLLECTION EXTERNAL.postman_collection(5).json` | `b2f02f0b74e47f7c9e2a60ba26110e7b574b445db337b26c2fa1e47b3b1fcadc` |

Private source files, provider examples containing credentials, and user data
are not committed to this public repository.

## Implemented configuration

| Setting | Meaning |
| --- | --- |
| `NINEPSB_BASE_URL` | WaaS origin; the WaaS API uses `/waas/api/v1` |
| `NINEPSB_VAS_BASE_URL` | Independent full VAS API root, ending in `/vas/api/v1`; matches Postman's `base_url_vas` |
| `NINEPSB_VAS_AUTH_URL` | Explicit full VAS identity authentication URL; no inferred default |
| `NINEPSB_WAAS_*` | WaaS username, password, client ID and client secret |
| `NINEPSB_VAS_*` credentials | VAS username and password only |

Both VAS URLs are unset by default. This is a configuration requirement, not
proof of bank confirmation: the actual URL must be obtained/confirmed before
setting it. A configured URL cannot automatically certify bank UAT.

- VAS never inherits a WaaS host or token. Different VAS API and identity hosts
  are supported. VAS can authenticate without any WaaS credentials configured.
- Known wrong-product authentication/API paths, embedded credentials, URL query
  strings, fragments, malformed ports and control characters are rejected before
  sending credentials. Redirects remain disabled.
- HTTPS is required in production. The existing HTTP exception remains confined
  to the exact documented sandbox origin; it is not an arbitrary insecure-host
  bypass or permission to send real customer data over HTTP.
- Changing either VAS URL creates a new token and catalog cache namespace.
  Changing WaaS settings does not reuse or refresh a VAS token.
- Missing/invalid VAS configuration blocks checkout before wallet lookup or a
  ledger reservation. Authentication failures never try another product/URL.
- Preflight reports the resolved API/authentication URLs and credential-field
  names separately. It never prints credentials, tokens or invalid URL content.
- The API, worker and maintenance Blueprint entries share both VAS settings.
  The environment example now describes 9PSB and removes the retired bank setup.
- Operator diagnostics now describe 9PSB VAS. The aggregate NGN wallet figure is
  labelled customer ledger, not a new-bank float balance: historical liabilities
  have not become 9PSB funds.

## Repeatable verification

First inspect configuration without making a bank request:

```sh
python manage.py bank_preflight --report-only
```

After the exact VAS URL/profile is confirmed and configured, the VAS-only command
cannot authenticate WaaS or report its success as VAS access:

```sh
python manage.py bank_sandbox_check --product vas --fixtures /private/path/9psb-test-fixtures.json
```

The private file may contain only bank-approved `mobile` and/or `account_number`
test fixtures. The command makes no purchases, submits no identities and writes
no ledger entries. Its output always distinguishes connectivity from completed
UAT and activation readiness. Use `--product waas` for the independent WaaS check.

## Validation and deployment

- Authentication/VAS/UAT/Blueprint checks: all 69 passed, including 16 new routing
  and diagnostic tests and existing coverage for all 12 documented VAS routes.
- Banking, accounts, transfers and Blueprint checks: 330 tests; 326 passed and
  four PostgreSQL-only checks skipped on SQLite.
- Operator diagnostic checks: all seven passed.
- Full app: 68 suites, 724 tests and one snapshot passed. TypeScript passed.
- Migration consistency: no model changes detected.
- Full backend regression results are recorded in the release checkpoint; the
  earlier migration already had unresolved full-suite failures. Focused tests
  do not substitute for that release gate or for real bank acceptance.

Render was checked during this work and still serves main commit
`30a365b3c248b0831e79f197541939b6181fb5c4`, deploy
`dep-dbh2ete1egvs73991tbg`. No service, runtime credential, activation flag or
deployment was changed. The migration remains a draft until the endpoint
conflict, full regression failures and actual bank UAT are resolved.
