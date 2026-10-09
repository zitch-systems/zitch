# Live query integration — 9 October 2026

Wema supplied live prefix **908**, a collection account, and separate NIP and
Etranzact inbound query URLs. The account value belongs in Render configuration,
not in the public repository. The bank's Slack request for a live sample is at
https://wemabankteam.slack.com/archives/C0C6PTZ4SK0/p1791482686795739.
This is evidence for staging the live collection setup and arranging a pilot;
it is not evidence of a settled live transaction or approval to debit bills.

## Implemented

- Exact bank endpoint allowlist, POST request validation, bounded HTTP calls,
  redirect refusal, duplicate JSON rejection, no implicit credentials and no
  automatic retries. Both URLs are configured through environment variables.
- Read-only comparison with receipts and ledger bindings, available to operators
  in the admin diagnostics page, via diagnostic-token API and management command.
- Observed bank `02 / No data found` responses are inconclusive. NIP status codes
  are not reused for Etranzact. Unknown or inconsistent data requires bank review.
- Query configuration and retained bill credential presence appear in redacted
  deployment diagnostics. Test money, legacy balances, live collections and bill
  reservations remain separate in both mobile and WhatsApp flows.

## Activation configuration

Apply consistently to the API, WhatsApp worker and seven Django jobs:

| Variable | Intended configuration |
| --- | --- |
| `WEMA_VAS_PREFIX` | `908` |
| `WEMA_VAS_MODE` | `live` |
| `WEMA_VAS_COLLECTION_ACCOUNT` | Bank-supplied collection account |
| `WEMA_VAS_NIP_QUERY_URL` | Bank-supplied FintechTransQuery URL |
| `WEMA_VAS_ETRANZACT_QUERY_URL` | Bank-supplied eTzTransQuery URL |
| `WEMA_VAS_BILLER_SOURCE_ACCOUNT` | Same collection account, staged only |
| `WEMA_VAS_RELEASE_PHASE` | `closed` until the pilot profiles are chosen |
| `WEMA_VAS_ENABLE_ENROLLMENT` | `false` pending controlled-pilot setup |
| `WEMA_VAS_ENABLE_VALIDATION_ENROLLMENT` | `false`; no new 711 test allocations |
| `WEMA_VAS_VALIDATION_SELF_SERVICE` | `false` |
| `WEMA_VAS_BILLER_ENABLED` | `false` until bank debit entitlement is confirmed |

Existing identity keys, callback tokens, historical records and subscriptions
must be preserved. Never change an immutable 711 account into a 908 account.
Each live pilot customer gives separate live identity-sharing consent.

## Remaining live evidence

1. Choose verified pilot profile(s), obtain the customer's live consent, and
   resolve any legacy balance/migration requirements. Generate a separate 908
   sample and have Wema validate account lookup, notification and settlement.
2. Obtain positive query responses and the Etranzact status/reversal contract,
   pagination/rate limits and missed-notification recovery procedure.
3. Wema must confirm that the collection account can fund retained bill APIs,
   provide/confirm live bill subscriptions and status/requery entitlement, and
   agree bank statement debit/refund mapping. The collection account email and
   the inbound query URLs do not establish these permissions.
4. Exercise an authorized real inflow and bill payment, verify duplicate and
   pending handling, collection settlement, and mobile/WhatsApp receipt delivery.
5. Complete Android physical-device/FCM and iOS signing/APNs acceptance and a
   current backup restore drill. Automated tests do not prove device delivery.
6. Outgoing transfers still need a payout initiation and outward status contract.
   These inbound URLs must not be attached to the outgoing transfer adapter.

General release stays closed while these items are outstanding. Existing
authenticated callbacks and historical reconciliation remain available.
