# Return to Wema Partnership — 9 October 2026

The owner requested restoration of Partnership on the app and WhatsApp because
the VAS collection integration does not provide the required payouts.

Deploy the return guards before changing runtime selection. On the API, WhatsApp
worker and every Django cron, merge these environment settings:

```
BANK_ACCOUNT_PROVIDER=partnership
WEMA_PARTNERSHIP_MODE=active
WEMA_PARTNERSHIP_RESTORE_VAS=true
KYC_PROVIDER=wema
WEMA_VAS_ENABLE_ENROLLMENT=false
WEMA_VAS_ENABLE_VALIDATION_ENROLLMENT=false
WEMA_VAS_VALIDATION_SELF_SERVICE=false
WEMA_VAS_RELEASE_PHASE=closed
WEMA_VAS_BILLER_ENABLED=false
WHATSAPP_FLOW_VAS_ENROLLMENT_ENABLED=false
```

Keep the Wema biller active, and retain all existing bank keys, URLs, callback
authentication, VAS mode/prefix, identity encryption keys, collection account,
query URLs and historical records. No balances are moved, erased or reclassified.
Keep VAS endpoints enabled to process late notifications idempotently.

The published Flow `1047786551595097` includes Partnership identity, OTP, transfer
and PIN screens as well as the retained VAS screens; no republish is necessary.
Disable its VAS entry points through runtime selection. Partnership BVN account
creation uses Wema; Prembly remains available for the existing biometric steps.

Existing Partnership NUBANs are reused. Customers without one follow the existing
verified account setup/recovery flow; unknown issuance is never reset or retried
blindly. Validation accounts do not affect real funds or block this return.

VAS-only customers with an independent identity proof and no prior Partnership
account, bank proof, bank attempt or cutover can explicitly start their first
Wema account/OTP flow on either channel. The submitted identity must match their
retained hash. Verification flags stay intact; any bank history keeps the recovery
path, and existing OTP attempts resume without another creation request.

A customer with a live VAS account returns only when it is unrestricted, its
canonical collection balance is exactly zero, and it has no held receipt or
unresolved bill. These checks run again under the wallet lock before spending.
A late collection credit stays in the ledger exactly once and immediately puts
that profile into Partnership review, with no funding instructions or spending.
Bank settlement/refund evidence is needed to resolve such cases; changing the
provider does not move money from the collection account to an individual NUBAN.

`vas_deployment_diagnostics` reports aggregate return eligibility on each API
build. Inspect that inventory and deployed configuration after activation.
Reconciliation continues to flag missing collection-bank evidence; the return is
not proof of full go-live readiness or Wema production entitlement.

Both channels consume the same provider state. App builds additionally honour
explicit Partnership available balances and show review status instead of new
account creation. Rebuild the Android test APK from the merged revision.
