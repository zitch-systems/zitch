# Bank read availability, 5 November 2026

The reported device is using version 1.0.14. API logs show external recipient
lookups returning HTTP 503 after provider read timeouts at 07:35 WAT, followed
by an airtime request at 07:36 WAT. HTTP 200 can carry the application's pending
response and does not prove delivery. The deployed connectivity report
confirms separate WaaS/VAS authentication and enabled spending for the enrolled
account. No credentials, full account numbers, phone numbers or holder names
are included here.

## Application defects corrected

- Wallet display refresh previously used a four-second read timeout, shorter
  than successful provider reads observed during diagnostics. It now allows
  eight seconds per read. Cold authentication plus wallet enquiry stays within
  the existing app deadline; a shared 30-second lease prevents overlapping
  refreshes. Unconfirmed or restricted accounts remain unavailable for spending.
- Auto-detection authentication previously stopped after three seconds even
  though successful authentication takes longer. Its eight-second read budget
  and eight-second enquiry window fit within the app's twenty-second deadline.
  The shared detection lease now covers that entire window. Recipient identity
  requirements and actual enquiry timeouts are unchanged.
- Payment status evidence used an INFO logger inheriting root WARNING, which
  silently discarded the diagnostic records. Submission and status outcomes now
  reach the configured application audit stream. Records contain only a short
  reference suffix, product/operation, HTTP/code, bounded state labels, binding
  result and known field names. Customer data, raw messages, credentials, tokens,
  full references and payloads are excluded.

## Remaining provider evidence

Fresh direct external enquiries using directory-issued Wema/Access routes,
including both bank code formats and optional sender, still return code 96
without a holder name after approximately 26 seconds. Internal 9PSB enquiries
were previously confirmed using the same credentials and parser. These results
do not establish a valid external recipient or working external route.

The earlier single VAS fixture airtime request, reference suffix `199A91`, still
has an explicit FAILED status with a generic Failed description on the provider
endpoint. It is distinct from the latest customer recharge. No new purchase,
transfer or funding request was submitted in this investigation.

These changes remove application-side read failures and restore the evidence
needed to trace outcomes. They do not turn provider code 96 or a failed recharge
into success. External recipient routing and VAS vending still require a
confirmed working provider profile/fixture. No balance, ledger reservation,
source account, tier or settlement rule is changed. Version 1.0.14 remains
compatible; another APK is not required for these server-side changes.
