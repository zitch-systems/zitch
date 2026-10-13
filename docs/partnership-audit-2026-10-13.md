# Partnership release review — 13 October 2026

**Launch decision: HOLD until the evidence below is complete.** The active account
route is Partnership. VAS enrollment remains closed, while historical callbacks
and reconciliation remain active.

## Repairs in this review

- Financial readiness now reads the latest immutable integrity, bank-balance and
  settlement results. Missing, stale, incomplete or failed results and unverified
  opening histories prevent a preflight GO. Public health checks remain liveness
  checks, so these operational findings do not take down the API.
- Settlement with any unreadable asset rail reports an unavailable position.
  Known balances remain visible as an incomplete subtotal, without inventing a
  surplus or shortfall. Malformed bank balance responses fail safely.
- OTP consumption, expiry and current identity/contact state are checked under
  locks. Push registration and logout are serialized to prevent a late request
  restoring a logged-out session's notification subscription. A server-issued
  session start time survives token refresh and prevents an older account from
  reclaiming a shared phone's notification token. The additive accounts migration
  preserves existing sessions; disabled registrations retain that ownership guard.
- WhatsApp authorization and durable queue insertion are atomic. Queued unlocks
  require the same current channel and credentials. Payment displays respect
  reversal-review evidence.
- App notification taps, pending navigation, session changes and async transfer
  preparation are covered by regression tests. Firebase configuration must be
  present in a production Android build; a sideload test APK remains a test build.
- Malformed deep links use a guarded linear decoder backport compatible with
  the current navigation library. A compatible server-tool patch and explicit
  parser-mitigation tests are included. Remaining dependency findings and their
  observed scope are recorded in the [mobile dependency audit](mobile-dependency-audit-2026-10-13.md).

## Evidence required to remove the hold

| Requirement | Required evidence / action |
| --- | --- |
| External reconciliation | Bank statements and reference-bound debit/credit/fee entries explaining every ledger/bank difference; append only verified corrections and retain audit evidence. |
| Opening histories | Attributable account opening balance and complete history coverage before clearing each review flag. |
| Unknown account issuance | Wema confirms whether the original request created an account and provides the identity-bound result; no blind retry or deletion of the attempt. |
| Pool and residual VAS balances | Authenticated bank reads/statements and settlement mapping. An individual-account API rejecting a corporate pool is not a zero balance. |
| Fees and reversals | Confirm Partnership fee payer, VAT/duties, principal debit, fee debit and fee refund rules. An informational fee quote is not evidence that fees are ledger-booked. |
| Tier 2 | Prembly interactive session configuration and an authenticated server result bound to the customer, action, expiry and captured face used in the bank upgrade. |
| Tier 3 | Wema completion evidence tied to the submitted account, address and verification request. An account-level tier/status alone is insufficient. |
| Android/iOS notifications | Firebase Android app configuration, Expo FCM v1 credentials, iOS signing/APNs, and received/tapped alerts on physical devices, including account switching and offline logout. |
| Live acceptance | Authorized real Tier 1 OTP/face, inflow, outward transfer/status, bill success/refund, duplicate/delayed notifications and missed-notification recovery, with matching bank and ledger entries. |
| Recovery | Restore a current backup into an isolated database, run migrations and integrity checks, and record recovery time. |

The 13 October Wema reply in the VAS integration thread states that its SLA/pricing
was emailed, corporate bill profiling and Fintech Hub access are still in progress,
and its payout service requires a licence. These statements concern the VAS
discussion and do not independently establish or revoke Partnership entitlement.

## Verification

Run the complete CI suite on the merged revision, verify every deployed Django
service uses it, and publish the Android test APK from that same reviewed main
revision. Keep production-store publication gated on signing, push credentials
and live acceptance. Do not clear evidence gates or alter customer funds to turn
the report green.
