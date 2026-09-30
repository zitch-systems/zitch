# Platform repair and release review — 30 September 2026

A bank OTP can succeed before account details become readable. Previously, Zitch returned processing without saving OTP acceptance. The later callback attached the account but did not finish identity proof, leaving a funded customer blocked on airtime and transfers.

OTP acceptance is now saved before account retrieval. The callback and reconciliation finish the authenticated bank holder-name check and save durable proof. Repeated confirmation does not resubmit a spent OTP. App and WhatsApp show processing instead of requesting the number/code again. Account existence alone cannot produce verified identity; ownership conflicts and name mismatches remain blocked.

The existing-account hosted BVN face route remains available when no accepted OTP proof exists. Secure-form failures keep the customer on WhatsApp. Email/SMS delivery settings are inherited from the API by the notification-writing worker and money jobs; the reconciliation job also inherits bank KYC product settings.

Validation before workspace failure: 111 focused backend checks passed, including delayed callback followed by airtime and duplicate-request protection. App typecheck passed; lint had no errors and 26 existing warnings. 255 app tests passed on the first run; the single timeout passed on a focused rerun of 6 tests. Meta connector typecheck/build and 159 tests passed. Full backend testing was interrupted by the workspace disconnect; final repository CI is required.

Render reports billing suspension on API, worker, connector, databases, and jobs. Resolve billing before deployment and live customer/payment verification. A retained deployment labeled live does not mean its suspended service is operating.

After restoration: deploy the API so wallet migration 0022 is applied; apply the Frankfurt Blueprint and verify API email/SMS and bank KYC source settings; redeploy consumers after the API migration if their schema gate failed; run reconciliation and verify live signup/sign-in, identity, funding/alerts, airtime/data/bills, transfers, receipts, reconciliation/refunds, address verification, and mobile behavior.

Older pending attempts have no OTP acceptance timestamp. Never backfill it from an account number or customer assertion. Restore only from attributable authenticated evidence or the secure bank verification route.

Tier 2 browser/WhatsApp liveness still needs a provider-attested browser liveness contract. A still image or Tier 1 ownership face check cannot substitute for that contract. The whole platform cannot be certified live end-to-end while these dependencies remain unavailable.
