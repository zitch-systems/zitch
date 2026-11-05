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
| Electricity, betting and cable billers | HTTP 200, populated lists (11, 17 and 3 respectively) |
| Exams and internet billers | HTTP 400/code 400, failed provider response |

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
unchanged.

## Additional bill catalog corrections

Live electricity definitions include `customerPhone` with `validation=Y`, a
field the parser previously rejected. BETLAND similarly requires `phoneNumber`.
Validation now sends the normalized contact under the exact requested key.
The complete field sweep covered all 31 electricity, betting and cable billers;
all 31 form definitions pass after these fixes. Validation now uses the
displayed contact phone when the bank requires it. The app and WhatsApp both
provide that contact; the server binds it to the validation record and refuses
a changed phone before any debit. Earlier app builds that omit the phone use
the verified account contact. An update is needed to validate a different
displayed contact correctly from those builds.

Live cable catalogs include composite item IDs such as a package joined to an
add-on with `+`, omitted validation flags, and repeated item IDs. One repeated
ID describes different packages with different prices. The former parser
rejected the entire catalog. A separate bounded JSON item validator now
supports composite selections in fields, validation and payment. Missing
validation flags default to exclusion from optional validation inputs. Exact
duplicate options collapse; every version of a conflicting ID is excluded, so
the remaining packages are usable without guessing a price. If no unambiguous
selection remains, the biller stays unavailable. URL path IDs retain their
original validation, and the payment remains bound to its exact item and price.

After these corrections, the sampled electricity, betting and cable field
definitions pass the application parser. This is catalog compatibility evidence,
not proof of a customer validation, purchased token or delivered subscription.
Version 1.0.14 (22) includes the contact validation request update.
