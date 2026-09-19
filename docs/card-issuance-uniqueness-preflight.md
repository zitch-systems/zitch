# Virtual-card uniqueness preflight

Card issuance now enforces one local virtual card per user. Before migration
`cards.0004_card_issuance_and_uniqueness` is applied to an existing database,
the deploy runs:

```bash
python manage.py audit_card_uniqueness
```

No output from this command contains card tokens, PANs, CVVs, or customer
contact details. A clean result is safe to migrate. If duplicate user IDs are
reported, the build stops before migrations alter the database and the current
production release remains unchanged.

## Duplicate-row remediation

1. Keep live card issuance disabled (`CARD_ISSUER_LIVE_ENABLED=false`).
2. Take and verify a restorable database backup.
3. For each reported user ID, compare every local row with the issuer's card
   lifecycle, funding, and closure evidence. Do not select a row merely because
   it is newest or has a familiar last four digits.
4. Resolve pending card-funding cases before changing local rows. Freeze or
   close non-authoritative cards at the issuer first so no orphan card remains
   spendable.
5. In an approved maintenance operation, retain exactly the one authoritative
   local row and remove only provider-confirmed duplicate rows. Record the
   issuer evidence and operator approval outside the customer-facing metadata.
6. Run the preflight again. Deploy only after it passes, then verify that
   migration `cards.0004` applied and that issuance remains disabled until a
   real issuer create/requery test has passed.

The migration repeats this check and refuses to add the uniqueness constraint
if duplicates reappear between preflight and migration.
