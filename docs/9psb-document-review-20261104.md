# Bank enquiry document review, 4 November 2026

Reviewed the newly supplied July 2026 WaaS PDF (64 pages) and updated VAS PDF
(10 pages) against the deployed revision `bf55c55`.

## Confirmed contracts

- WaaS authentication remains `/waas/api/v1/authenticate` with its four separate
  credentials. Bank directory is `GET /waas/api/v1/get_banks`; recipient enquiry
  is `POST /waas/api/v1/other_banks_enquiry`.
- WaaS PDF page 35 (printed page 32) describes a mandatory `customer` request
  object and a `message/status/data` response. It does not specify the nested
  account fields or a complete request/response example. Page 37 explicitly
  refers to the WaaS Postman collection for directory details. The currently
  supplied Postman collection covers VAS, not these WaaS examples.
- VAS authentication remains `/identity/api/v1/authenticate`, with its own
  username/password and bearer token. Airtime/data requests use the selected
  network, string amount, `debitAccount`, and an 18-character reference. Status
  queries use the original reference as `transReference`. The client matches
  these fields and endpoints.
- The VAS PDF calls `debitAccount` the account to debit. It does not define the
  supplied fixture's relationship to a customer's WaaS wallet or merchant
  settlement accounting. It provides no basis for silently replacing a
  customer's source account with that fixture.

## Corrected recipient response handling

The old adapter accepted only the previously observed top-level NIP response
(`code/customer/account`). It ignored a standard enclosing WaaS `status/data`
wrapper and did not reject every contradictory status/code alongside a NIP
success code.

The common recipient parser now supports the observed customer/account object
either at the top level or enclosed under `data`. Enclosing that payload is a
compatibility interpretation of the PDF's generic wrapper, not a newly observed
external-bank reply. It requires HTTP 200, explicit success evidence, the exact
requested bank route and account, and a nonempty holder name. Every supplied
outcome and supported account representation must agree. Conflicting names,
codes, routes, identifiers, malformed containers and partial responses are
rejected. Payments and identity decisions retain their existing stricter rules.
Connectivity checks use the same recipient parser as transfers.

## Remaining verification

This parser correction did not explain or resolve the observed provider
`ReadTimeout` errors. No provider requests were performed for that initial
review. The earlier WaaS Postman collection was subsequently recovered, and the
user explicitly approved sending the supplied credentials/account details to
the supplied endpoint. The resulting authenticated checks and remaining
external-enquiry failure are recorded in
[the provider check report](9psb-provider-checks-20261105.md). No further document
upload is needed; a successful external-bank enquiry is still unverified.

This change is server-side and requires no replacement APK for version 1.0.13.
