# Provider checks, 5 November 2026 (Africa/Lagos)

These authenticated checks used the supplied WaaS and VAS credentials and the
explicitly approved `http://102.216.128.75:9090` destination. No credentials,
tokens, holder names, complete account numbers or balances are recorded here.
The application baseline was `878db5f` (PR #588).

## Confirmed read results

| Check | Result |
| --- | --- |
| WaaS authentication | HTTP 200, access token received |
| VAS authentication | HTTP 200, success/code 200, access token received |
| Bank directory | HTTP 200, success, populated bank list |
| Customer wallet enquiry | HTTP 200, success/code 00; public NUBAN matches supplied account |
| Supplied VAS fixture wallet enquiry | HTTP 200, success/code 00; supplied debit account matches public NUBAN |
| 9PSB name enquiry for customer and fixture | HTTP 200/code 00, exact account/route and holder verified by the deployed parser |
| VAS categories | HTTP 200, success/code 200, five categories |
| Phone network | HTTP 200, success/code 200 |
| Data plans | HTTP 200, success/code 200, 46 plans |

Both wallet enquiries distinguish a 17-digit internal `number` from the public
10-digit `nuban`. The supplied VAS debit account matches its public NUBAN, not
that internal number. This evidence does not justify changing the application's
VAS source to an internal wallet identifier or a shared merchant account.

## External recipient enquiry

The directory reports Access Bank as `bankCode=044`, `nibssBankCode=000014`, and
Wema/ALAT as `bankCode=035`, `nibssBankCode=000017`.

For the external recipient supplied in the screenshots, both Access and Wema
were queried using both code formats, with and without
`customer.account.senderaccountnumber`. Every variant returned HTTP 200/code 96
after approximately 26 seconds with no holder name. The requested account and
route were echoed; neither a successful external bank route nor a valid
external recipient was established. A checksum candidate is not bank-verified
recipient evidence.

The older WaaS Postman collection was recovered from earlier uploads and read
before these checks. Its primary request has `customer.account.bank/number`;
some saved examples also include `senderaccountnumber`. Its placeholder replies
do not establish a successful external bank lookup. No further upload is needed.

The July 2026 PDF uses code 96 in two contexts: wallet-operation invalid
parameters, and NIP system malfunction. This finding applies to the name-enquiry
endpoint only. It does not decide whether a payment failed or permit a refund.
9PSB still needs to establish the correct enabled external route and a supported
recipient fixture for this profile; these probes cannot distinguish an upstream
route failure from a rejected recipient/profile configuration.

## Application correction and limits

HTTP-200 NIP routing/system errors were treated as an unconfirmed recipient.
They now produce the same retryable lookup-unavailable result as a transport
timeout. Automatic detection propagates the outage instead of returning an
empty candidate list. Verified names remain mandatory, and invalid-account
replies remain distinct from temporary failures.

The 9PSB control replies verify that the current recipient parser works with
actual successful responses. This availability classification does not repair
the external provider's code 96 or prove transfers, VAS purchase delivery,
Tier 2 or Tier 3 completed. Purchase validation did not yield a financial result;
no completed purchase or debit reconciliation is claimed. The customer source,
payment instructions, ledger, pending reservations and settlement rules remain
unchanged. This server-side change needs no new APK for version 1.0.13.
