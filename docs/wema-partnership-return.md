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

## Retired account deletion

After every API, worker and scheduled job is live with wallet migration 0028,
preview the owner-authorized cleanup using `purge_retired_accounts` with explicit
`--expected-accounts`, `--expected-receipts`, `--expected-unsettled` and
`--operator-reference` values. Add `--apply` only after the preview matches.
The command deletes retired account and receipt rows, including encrypted account
identity. It rejects held receipts, bill bindings, migration approvals, restricted
live accounts, mismatched ledger evidence and wallets still using a retired number.

Real money remains in the existing wallet and transaction ledger. Its payment
evidence moves into an immutable `UnsettledFunding` hold; no duplicate credit is
created. This hold blocks funding and spending and remains in reconciliation
scope until independently evidenced settlement. The old live destination remains
blocked by its digest, without retaining its plaintext account record. Validation
accounts with zero exposure are deleted without touching their owners or any
Partnership wallet. An independent identity proof still permits first Partnership
setup when there is no prior bank account or issuance history.

The purge is atomic and audited. It locks affected wallets and retired tables,
temporarily permits deletion only on those retired tables, and restores their
protections before commit. Ledger and audit protections remain enabled throughout.
Empty historical tables and migration labels remain for deployment compatibility;
there are no retired enrollment, callback or provider execution routes.

After verifying zero retired account/receipt rows, remove
`HISTORICAL_FUNDING_IDENTITY_KEYS` from runtime environments. It has no active
consumer. Database deletion does not close bank-side accounts or settle money;
bank closure and any late deposits require attributable bank evidence.

Run `python manage.py partnership_diagnostics --require-clean-config`, then
`python manage.py wema_preflight --strict` for release gates. Neither proves bank
entitlement, live provider delivery or settlement acceptance. Follow the
[go-live runbook](wema-go-live-runbook.md) for those checks.
