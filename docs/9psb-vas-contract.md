# VAS integration checkpoint — 29 October 2026

The supplied VAS PDF and Postman collection have been applied to the API, app
checkout, WhatsApp checkout and reconciliation worker. This checkpoint is code
and synthetic test evidence, not completed bank UAT or a production release.
The implementation remains in draft PR #561; activation switches remain off.

## Source identity

The newly supplied files are byte-identical to the earlier VAS attachments:

- PDF SHA-256: `aeda07f7d42b35c9670477fee340cf091700f2ed61f1e64ce9abf5b07f5c35b1`
- Postman SHA-256: `b2f02f0b74e47f7c9e2a60ba26110e7b574b445db337b26c2fa1e47b3b1fcadc`

The source files remain outside this public repository. The collection contains
an example bearer token; it is not a replacement for Zitch's credentials and was
not used. No credentials or raw customer/identity responses are committed.

## Documented endpoint coverage

Sandbox host: `http://102.216.128.75:9090`. HTTP is allowed only for this exact
sandbox origin and approved test data. Production requires HTTPS.

| Operation | Method and path | Integration |
|---|---|---|
| VAS authentication | POST `/identity/api/v1/authenticate` | Separate username/password JSON, Bearer token cache; failure envelopes cannot supply a token |
| Phone network | GET `/vas/api/v1/topup/network?phone=…` | Normalized phone; bank network used for purchase |
| Data plans | GET `/vas/api/v1/topup/dataPlans?phone=…` | Dynamic plans; chosen product and exact price checked by server |
| Airtime | POST `/vas/api/v1/topup/airtime` | Owned debit account, durable 18-character reference, one submission |
| Data | POST `/vas/api/v1/topup/data` | Same controls plus catalog product ID |
| Top-up status | GET `/vas/api/v1/topup/status?transReference=…` | Requery original reference; unknown/404 stays pending |
| Categories | GET `/vas/api/v1/billspayment/categories` | Dynamic app and WhatsApp choices |
| Billers | GET `/vas/api/v1/billspayment/billers/{categoryId}` | Dynamic category selection |
| Input fields | GET `/vas/api/v1/billspayment/fields/{billerId}` | Validated field schema, selections, fixed or open prices, required names |
| Validate customer | POST `/vas/api/v1/billspayment/validate` | Metadata-driven validation fields, encrypted name/otherField, owner/environment/amount binding |
| Pay bill | POST `/vas/api/v1/billspayment/pay` | Server-bound validation, expiry, PIN, single-use authorization and idempotent payment replay |
| Bill status | GET `/vas/api/v1/billspayment/status?transReference=…` | Confirmed settlement and token recovery from the documented status description |

Catalog responses cache for five minutes, scoped to credentials and environment.
Disabled VAS cannot make customer catalog/validation/purchase requests. Runtime
reconciliation can still resolve an already-authorized instruction. No financial
POST is automatically retried after a timeout or authentication response.

## Contract details addressed

- `validation=Y` optional inputs are sent for validation; `N` inputs are omitted.
  `firstname` and `lastname` are collected when required. Bank-returned names
  and `otherField` are bound server-side rather than accepted from checkout.
- The electricity sample's PREPAID/POSTPAID selections omit prices. Those
  selections use the customer's amount; fixed-price plans reject altered prices.
  A different amount in the validation response cannot authorize a debit.
- The PDF marks payment `itemId` mandatory, but the collection's no-plan bill
  payment omits it. The implementation supplies it for catalog selections and
  omits it for billers with no item field, as demonstrated by the collection.
  No artificial item is generated. This combination still needs live acceptance.
- Incidental sample references/phones/accounts conflict with the PDF's lengths.
  Runtime requests retain 18-character references, 11-digit normalized phones
  and owned 10-digit account numbers; malformed sample values are not copied.
- HTTP 200 with `status=failed` / `responseCode=300` is the collection's
  duplicate-reference example. It stays pending and is queried; it cannot cause
  a refund or a second submission. A 404 status lookup also cannot refund.
- Any echoed amount, account, reference, recipient, network or product must
  match the persisted instruction. Contradictions remain unresolved. A generic
  successful status lookup is not itself a successful payment.
- The bill-status example embeds a token and units in `data.description`.
  Only the explicit token/units labels are parsed after confirmed success.
  Arbitrary descriptions, references and `otherField` never become tokens.
  Tokens persist for owner-scoped transaction detail and payment replay.
- When payment delivery succeeds but `isToken=true` has no usable token,
  the payment remains successful and only receipt recovery is queued. Failed,
  missing or mismatched receipt lookups cannot refund that delivered bill.
  App/WhatsApp explain the delayed token; app history can refresh it later.

Migrations 0007 and 0008 add bill-validation environment and receipt-recovery
state. Pre-existing validation rows have unknown environment and cannot authorize
new payments. The receipt flag does not change ledger amounts or debit state.

## Verification

- Local banking/account/transfer/Render regression: 306 tests, 302 passed and
  four PostgreSQL-only skips on SQLite.
- Checkout and transaction-detail tests: 39 passed.
- Full app suite: all 68 suites / 719 tests and one snapshot passed.
- TypeScript check, lint of changed app files and migration consistency: passed.
- Tests cover all 12 documented VAS routes, no-plan bills, required names,
  variable electricity prices, altered prices, duplicate/refund behavior,
  receipt recovery, owner/environment isolation and contradictory bank echoes.

The full backend release suite has unresolved failures at the preceding
`f7ddc47` checkpoint. Focused green checks do not override that release gate.
PostgreSQL and full CI must also be reviewed for this commit before deployment.

At **10:42 WAT on 29 October**, fresh authentication through the actual updated
client using credential attachment (3) returned WaaS HTTP 200 and VAS HTTP 400 /
response code 400. Endpoint and JSON fields match both supplied VAS sources.
No payment, identity submission, live account creation or activation occurred.

## Remaining acceptance work

1. 9PSB must confirm the VAS identity URL/profile because its later correction
   conflicts with both attachments. A 400 response alone does not identify the
   credentials as the cause. See [the routing correction](9psb-vas-auth-routing.md). Sandbox IP whitelisting
   is not required, as its technical team already confirmed.
2. With working credentials and a Zitch-owned authorized funded UAT wallet,
   verify network/catalog reads, supported airtime/data networks, fixed-price and
   no-plan bills, electricity tokens, duplicate submissions and delayed status.
3. Complete full backend regression migration, joint bank UAT and the remaining
   deployment gates in `9psb-release-checkpoint.md`. The callback remains subject
   to actual deployment and bank-delivered verification; this VAS documentation
   does not establish a separate VAS webhook contract.

This VAS contract does not add BVN/NIN/facial, wallet-opening, Prembly address,
QR/corporate, virtual-account or card-acquiring acceptance evidence. Those retain
their separate implementation and UAT requirements in `9psb-migration.md`.
