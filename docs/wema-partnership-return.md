# Wema Partnership only

Wema Partnership is the sole active account integration on the app and WhatsApp.
The separate virtual-account integration, its enrollment and callback endpoints,
operator tools, deployment settings and customer screens are removed. Historical
provider-selector environment values cannot reactivate it. Removing application
code does not close an account at the bank or settle an existing liability.

## Deployment and customer journeys

Deploy the same reviewed revision to the API, WhatsApp worker and every Django
scheduled job. Account and Tier 1 identity selection are fixed to Partnership and
Wema respectively. `WEMA_PARTNERSHIP_MODE=active` enables new Partnership business;
any other mode blocks new instructions while allowing accepted Partnership work
to be resolved. Production acceptance requires `WEMA_SIMULATION=false`, valid
Partnership credentials and all release evidence in the
[current review](partnership-audit-2026-10-13.md).

Retain the credentials and callback configuration used by the active Partnership
integration and bank bill payments. Remove obsolete virtual-account configuration
from deployments. Retain historical decryption keys only until the authorized account purge is
complete; the financial ledger and unsettled payment evidence survive separately.

Remove `WEMA_VAS_*`, `WHATSAPP_FLOW_VAS_*`, `WEMA_PARTNERSHIP_RESTORE_VAS`
and the obsolete `BANK_ACCOUNT_PROVIDER` selector from the API, worker and every
Django scheduled job. The two exceptions are `WEMA_VAS_STATUS_LEGEND` and
`WEMA_VAS_REQUERY_AFTER_SECONDS`: those control Partnership airtime/data settlement.
Rename the retained historical decryption key to `HISTORICAL_FUNDING_IDENTITY_KEYS`
without changing its value. This temporary key does not enable account enrollment or provider calls. Remove
it from runtime configuration after the authorized purge completes.

After deployment, run `python manage.py partnership_diagnostics --require-clean-config`.
The report lists obsolete variable names only and exits unsuccessfully if any
remain, including variables with empty values. The old provider-selection runbook
has been removed; Git history retains the historical decision record.

Publish and select the reviewed `backend/whatsapp/flow_assets/pin_flow.json` asset.
Its retired setup screens and actions are removed. Confirm that the live Flow and
all Django services match the reviewed revision; an older published Flow does not
become current when the backend deploys. Rebuild the app from that same revision.

Tier 1 uses BVN **or** NIN with bank SMS OTP or the bank-hosted face route.
Tier 2 requires authenticated Prembly interactive liveness tied to the customer
and the captured face used for the bank upgrade. Tier 3 requires bank completion
tied to the account, submitted address and verification request. Higher-tier
evidence gates stay closed until those contracts and adapters are validated.

Existing validated Partnership NUBANs are reused. Customers without one follow
verified account setup or recovery. Unknown issuance is never reset or retried
blindly. Retained independent identity proof may support an eligible customer's
first Partnership setup, but it does not replace the bank's ownership challenge.
Any prior bank proof, request or account history keeps the recovery path.

## Completed account retirement

The retired account and receipt records have been purged. Account-balance
adapters, the purge command and bill-funding foreign key are removed from runtime
code. Outstanding money lives in the independent `UnsettledFunding` and
`RetiredFundingArchive` accounting records; removing an integration does not
cancel that liability or settle it at the bank. The original transaction ledger
and its database protections remain intact.

The rollout has two stages. Wallet migration 0030 requires zero retired accounts,
receipts, migration approvals and linked bills, then removes the foreign key from
Django's state. Empty physical tables and their otherwise unused Django mappings
remain temporarily so old API and worker processes can finish while every
service updates. Keeping the mappings until the tables disappear also preserves
PostgreSQL test isolation. Only after all services use that revision may the next
migration remove the empty tables, model mappings and obsolete triggers.
A nonzero inventory aborts the migration; it never erases unreviewed records.

Historical migration files and their original labels remain solely so existing
and fresh databases can traverse the same migration graph. The compatibility
package has no enrollment operations, callbacks, customer screens or bank
credentials. Its empty model mappings are removed in stage 2. The old destination digest still prevents accidental
reuse, and outstanding money remains in reconciliation scope.

Remove `HISTORICAL_FUNDING_IDENTITY_KEYS` from runtime environments; it has no
consumer. Bank-side account closure and any late deposits require attributable
bank evidence and are separate from this software removal.

Run `python manage.py partnership_diagnostics --require-clean-config`, then
`python manage.py wema_preflight --strict` for release gates. Neither proves bank
entitlement, live provider delivery or settlement acceptance. Follow the
[go-live runbook](wema-go-live-runbook.md) for those checks.
