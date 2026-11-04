# Payment recovery, 4 November 2026

## Observed failures

The Android 1.0.12 requests at 08:49:55 and 08:51:31 UTC received HTTP 409
with a 137-byte response from `/api/banking/purchase/`. For an airtime request,
the deployed handler's matching response is `idempotency_conflict`: the durable
retry key is bound to different material details. This path returns before PIN
verification or provider dispatch. The client classified every 409 as an unknown
payment outcome, displayed processing, and had no transaction reference to query.
The screenshots' unchanged wallet balance is consistent with that refusal.
The reason the historical binding differs has not been established.

The bank-name lookup requests at approximately 08:49 UTC ended in provider
`ReadTimeout` errors and returned HTTP 503. Both automatic candidate lookup and
manual lookup failed. No recipient name was confirmed. Increasing a timeout or
changing customer-facing text is not evidence that the provider contract works.

## Implemented recovery

- A binding conflict returns the owner's `previous_reference`, separately from
  the requested payment's reference. It does not replay an old success as proof
  that changed checkout details were paid.
- The payment screen describes the previous payment and reads its owner-scoped
  status. It retains the key for unknown, pending, mismatched-reference and
  reversal-review outcomes. It never resubmits a purchase during status polling.
- Once the previous transaction is confirmed terminal outside review, the user
  may explicitly start a new payment. Device storage must provide a different
  durable key, and a fresh review and PIN are required before execution.
- A same-intent, definitively failed replay returns HTTP 400 with its reference,
  avoiding the generic ambiguous-409 path. Reversal review still takes precedence.

## Provider verification still blocked

The new attachment supplies separate WaaS/VAS credentials, their authentication
URLs, the VAS collection, and a VAS debit fixture distinct from the customer's
WaaS account. Credentials and sample bearer tokens were not committed. No source
account substitution, customer balance adjustment or payment was made.

A direct read-only diagnostic was rejected by automatic approval review because
it would disclose credentials and account data to `http://102.216.128.75:9090`
without explicit destination approval. It was not routed through another host or
service. Bank authentication, directory, recipient lookup and VAS catalog checks
remain blocked pending that approval. The fixture's funding/settlement role must
be verified before changing debit routing; substituting it could otherwise create
an unrecorded merchant settlement obligation or incorrectly recredit purchases.

This release fixes recovery from the observed rejected request. It does not
establish end-to-end bank lookup or VAS delivery.
