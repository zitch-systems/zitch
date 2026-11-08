# Retired partner bank (Wema / ALAT) archive

9PSB is Zitch's only bank. Nothing in the running code can contact the former
partner bank, open an account there, move money through it or verify identity
with it.

## Where the former integration lives

The complete client, callbacks, operator commands and their tests are kept on
the Git branch `archive/wema-partnership-before-9psb-20261028` (`30a365b`).
Retired test modules, classes and methods are listed with their source commit
and SHA-256 in [`docs/retired-provider-test-inventory.json`](../../retired-provider-test-inventory.json).
The documents in this folder are the runbooks and incident notes from that
integration, kept for reference only.

## Removed from the running code

- The `utility.wema` compatibility client, the bank and face callbacks, and the
  app, WhatsApp and Flow paths for in-chat or in-app account opening, hosted face
  verification, account recovery and resend-OTP.
- The fake-money simulation commands and endpoints (`simulate kyc`, `simulate
  deposit`, `SIMULATE_DEPOSIT_TOKEN`, `WEMA_SIMULATION`).
- The `WEMA*` settings, the face-verifier CORS origins and the statement
  reconciliation, reversal matching and incident-evidence tooling.
- The operator commands `wema_preflight`, `wema_banks_sync`, `seed_wema_plans`,
  `partnership_diagnostics`, `prepare_account_recovery`,
  `repair_partner_bank_accounts` and `reconciliation_evidence`. Use
  `bank_preflight`, `bank_tester_status` and `sync_bank_directory` instead.

## Kept on purpose

- **Database tables and migrations** (`WemaProvisioningAttempt`, `WemaFaceSession`,
  `WemaBiller`, `ReversalEvidence`, identity-proof sources). They hold historical
  customer and ledger evidence, and PostgreSQL archive triggers make that
  evidence immutable.
- **Historical ledger handling**: reversal quarantine and evidence keys
  (`wema_reversal*`, `WEMA-CR-*`) so operators can still review and settle old
  rows.
- **The `reconcile_wema` command name.** It runs the 9PSB reconcile sweep and
  exists only because a Render cron still calls it. Change that cron's command
  to `python manage.py reconcile_9psb`, then remove the alias.
- **Cards recorded as issued by the retired bank.** Every operation on them
  returns `provider_archived`; they can never be funded or unfrozen.
- **In-chat setup sessions still open from the old flow** (`add_account`). They
  are closed with the standard "no longer available" reply, and anything typed
  into them is masked from the message log.
