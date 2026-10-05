# Wema Partnership integration — archival policy

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

See [the VAS rollout runbook](../wema-vas-rollout.md) for validation, migration
evidence, unresolved bank contracts and release gates.
