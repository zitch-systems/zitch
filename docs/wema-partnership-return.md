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
from deployments. Preserve the keys needed to decrypt historical data and backups;
removing a provider is not authorization to make its financial evidence unreadable.

Remove `WEMA_VAS_*`, `WHATSAPP_FLOW_VAS_*`, `WEMA_PARTNERSHIP_RESTORE_VAS`
and the obsolete `BANK_ACCOUNT_PROVIDER` selector from the API, worker and every
Django scheduled job. The two exceptions are `WEMA_VAS_STATUS_LEGEND` and
`WEMA_VAS_REQUERY_AFTER_SECONDS`: those control Partnership airtime/data settlement.
Rename the retained historical decryption key to `HISTORICAL_FUNDING_IDENTITY_KEYS`
without changing its value. This key preserves access to archived evidence and
does not enable account enrollment or provider calls. Keep it secret.

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

## Historical funds and release evidence

The `legacy_funding` app preserves the existing financial tables, migrations,
receipts, reservations and approvals. Its historical Django app label remains
only for database and migration compatibility. It provides no enrollment, bank
callback or payment execution surface.

Customers with historical live balances, held receipts, unresolved bill funding
or account restrictions remain under `legacy_funding_review`. Both channels hide
funding instructions and block new spending while that hold applies. Historical
account numbers cannot become Partnership funding destinations. Spending checks
run again under the wallet lock. Validation records do not represent real funds.

No balance is moved, erased or reclassified by this change. Resolve historical
liabilities through attributable bank statements, settlement/refund evidence and
audited reconciliation. Retired callbacks are no longer ingested; obtain bank
evidence for outstanding or late activity and confirm retirement with the bank
before considering those liabilities closed.

Run `python manage.py partnership_diagnostics` for read-only configuration,
historical funding inventory and financial-control evidence. Then run
`python manage.py wema_preflight --strict` for release gates. Neither command
proves bank entitlement, live provider delivery or settlement acceptance. Follow
the [go-live runbook](wema-go-live-runbook.md) and
[WhatsApp operations guide](whatsapp-production-operations.md) for those checks.
