# Platform repair and release review — 30 September 2026

A bank OTP can succeed before account details become readable. Previously, Zitch returned processing without saving OTP acceptance. The later callback attached the account but did not finish identity proof, leaving a funded customer blocked on airtime and transfers.

OTP acceptance is now saved before account retrieval. The callback and reconciliation finish the authenticated bank holder-name check and save durable proof. Repeated confirmation does not resubmit a spent OTP. App and WhatsApp show processing instead of requesting the number/code again. Account existence alone cannot produce verified identity; ownership conflicts and name mismatches remain blocked.

The existing-account hosted BVN face route remains available when no accepted OTP proof exists. Secure-form failures keep the customer on WhatsApp. Email/SMS delivery settings are inherited from the API by the notification-writing worker and money jobs; the reconciliation job also inherits bank KYC product settings.

PR #523 was merged to main at `f307f5513335e3b003f7f9ea627963ca604db30c`. Its complete CI passed: 2,785 backend tests, 256 app tests, and 159 connector tests. The merged main revision also passed CI and CodeQL. This verifies the code, not production deployment.

The follow-up review found a separate mismatch: NIN alone completed the Tier-1 checklist and derived Tier 1, but payments required BVN. Tier 1 now consistently requires verified contacts and BVN; Tier 2 adds NIN and provider liveness; Tier 3 adds address. Existing NIN verification is preserved for the later upgrade. Old NIN-choice cards return to BVN, and a verified BVN is not requested again.

Status recovery now reads the current user under a row lock and recalculates stale tiers. Historical attempts marked verified by account reconciliation cannot substitute for actual ownership proof. Completed identity flags remain saved; missing flags are restored only from durable identity proof or a successfully verified bank face session. Secure payment/form failures offer WhatsApp retry or support instead of mandatory app installation. The app labels NIN as a Tier-2 check.

Follow-up targeted verification: 121 identity/payment backend checks and 66 secure-handoff checks passed. App typecheck and 256 app tests passed in CI. Full regression testing exposed higher-tier fixtures without the required address or liveness proof; those fixtures were corrected, and the stale-tier airtime regression now asserts rejection at the derived Tier-1 ceiling. The final complete CI must pass before merging; its results and release decision are recorded in [PR #524](https://github.com/zitch-systems/zitch/pull/524).

Render still reports billing suspension on API, worker, connector, databases, and jobs in the follow-up session. The latest retained API deployment is `c00466d940425599452cced0715b291f1a466940`, before PR #523. Resolve billing before deployment and live customer/payment verification. A retained deployment labeled live does not mean its suspended service is operating.

After restoration: deploy the API so wallet migration 0022 is applied; apply the Frankfurt Blueprint and verify API email/SMS and bank KYC source settings; redeploy consumers after the API migration if their schema gate failed; run reconciliation and verify live signup/sign-in, identity, funding/alerts, airtime/data/bills, transfers, receipts, reconciliation/refunds, address verification, and mobile behavior.

Older pending attempts have no OTP acceptance timestamp. Never backfill it from an account number or customer assertion. Restore only from attributable authenticated evidence or the secure bank verification route.

Tier 2 browser/WhatsApp liveness still needs a provider-attested browser liveness contract. A still image or Tier 1 ownership face check cannot substitute for that contract. The whole platform cannot be certified live end-to-end while these dependencies remain unavailable.

Prembly's published [Face Liveliness Check](https://docs.prembly.com/docs/face-livelinness-check) documents a `face/liveliness_check` endpoint with a verification status and confidence response. Its [SDK features](https://docs.prembly.com/docs/advanced-features) describe a live camera challenge and session metadata for webhook reconciliation. The current adapter uses a different endpoint and expects `data.liveness: true`. The exact SDK configuration, authenticated completion contract, and production response must be confirmed before enabling Tier-2 liveness. No guessed success schema or browser-uploaded photo was enabled by this review.

| Journey | Automated evidence | Production status |
| --- | --- | --- |
| Signup, sign-in, contact verification, secure PIN | Existing backend/app suites | Blocked by Render suspension |
| BVN OTP, delayed callback, proof restoration, funded account face route | Focused regression tests; PR #523 complete CI | Fix not yet deployed; customer record not yet inspected |
| Funding and credit reconciliation | Posted-history, idempotency and reversal tests | Last credit fix deployed before suspension; current state unavailable |
| Airtime, data, electricity and other bank bills | Existing provider, transaction, replay and refund tests | No live purchase made during this review |
| Internal/external transfers, receipts and alerts | Existing backend/app/connector suites | Delivery and worker environment inheritance require live verification |
| Savings, loans, cards, FX and scheduled jobs | Existing platform suites | Provider/job behavior requires restored services |
| Tier 2 NIN and provider liveness | Identity and failure-path tests | Browser liveness integration unresolved; current provider contract needs confirmation |
| Tier 3 address | Secure PIN-scoped web form tests | Provider completion contract and live behavior require confirmation |

Live customer repair must first match the account to its owner and inspect proof, attempts, and bank evidence. Never grant verification or replay a debit simply to clear the customer's error. Use reconciliation for accepted pending OTPs, restore flags from attributable proof, or complete the secure bank verification route if historical acceptance was not recorded.
