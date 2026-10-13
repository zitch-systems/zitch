# Wema Partnership integration — archival policy

> **Superseded on 13 October 2026.** This is a historical decision record.
> Wema Partnership is now the sole active banking integration. The separate
> virtual-account integration has been removed; do not apply the switches below.
> Use the [current Partnership guide](../wema-partnership-return.md).

Wema Virtual Account Service is the target account/collections integration. The
retained Partnership implementation exists for historical records, old callback
handling and completion/requery of already-started work.

`WEMA_PARTNERSHIP_MODE=archive` prevents new accounts, upgrades, transfers, bill
purchases, card issuance/funding and other fresh instructions. The customer-facing
funding destination is selected separately with `BANK_ACCOUNT_PROVIDER=wema_vas`.
Both controls must reach the API, worker and crons together during maintenance.

Do not delete legacy customer account fields, bank keys, ledger rows, callback
routes or reconciliation jobs while outstanding work or bank retention obligations
remain. Archival is not evidence that bank accounts were closed, balances moved,
pending instructions resolved or production configuration changed.

The former virtual-account rollout runbook was removed with that integration.
Current evidence requirements are in the
[Partnership release review](../partnership-audit-2026-10-13.md).
