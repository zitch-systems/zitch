# Retired wallet balances and service availability

`archive_retired_balances` removes a retired-only balance from the active wallet.
It requires the exact reviewed count and amount and an operator reference. Without
`--apply` it only validates the inventory. Repeating a completed archive is a no-op.

The command refuses Partnership accounts, mixed funds, unexplained ledger entries,
and a wallet/ledger/evidence mismatch. It locks the wallet and original evidence,
appends an internal reclassification debit, creates an immutable archive link, and
zeros the active balance in one transaction. It never calls the bank. The original
credit and unsettled funding evidence remain immutable. An audit failure rolls the
whole operation back. No immutability trigger is disabled.

Archived money is not a refund, cancellation of a customer claim, or proof of a
bank balance. Settlement reports include it once as a separate liability. The
old destination remains blocked, while it no longer contaminates the customer's
new Partnership wallet balance. Bank settlement still needs independent evidence.

Reconciliation isolates errors for individual histories, debit-restriction
retries, address checks, bill lookups and payout lookups. Other accounts and later
phases continue. The final report still records incomplete work and fails the
financial gate; exceptions never fabricate a refund or change a pending payment
into a completed one. The next scheduled run retries unresolved work.

Render's API readiness checks database/cache availability separately. A financial
cron reporting discrepancies does not stop the API or WhatsApp worker. Keep the
financial gates enabled; resolve the bank evidence instead of hiding alerts or
marking a held balance verified. No deployment can guarantee permanent zero downtime.
