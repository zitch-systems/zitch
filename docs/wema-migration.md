# Wema / ALAT migration — ⚠️ ARCHIVED (2026-07)

**Status: parked.** The production rails are locked to **Monnify** (wallet funding,
dedicated accounts, BVN/NIN KYC), **Kora** (bank payout + name enquiry, vNIN), and
**VTU.ng** (airtime/data/bills); face/liveness stays on Prembly. All Wema code is
retained, opt-in only — nothing routes to Wema unless a `*_PROVIDER` env is
explicitly set to `wema`. Leave those blank.

**Verified against Wema test keys before archiving** (via `/wema-diagnose`):
auth model (per-product `Ocp-Apim-Subscription-Key` + universal channel id) ✅,
bank list (499 banks) ✅, recipient name enquiry ✅.

**Why parked — blocked on Wema's side:**
1. The sandbox (`apiplayground.alat.ng`) does **not** mint partnership accounts:
   both BVN and NIN wallet-creation return a canned "download ALAT" response with
   no `trackingId` and no OTP — so the account → fund → transfer loop can't be
   exercised end-to-end.
2. The `securityInfo` encryption scheme (required on live money calls) is
   undocumented.
3. The transaction-status code legend is undocumented.

**To resume:** get from Wema (a) a working sandbox provisioning path or a funded
test source account, (b) the securityInfo spec, (c) the status legend — then set
the `WEMA_*` env vars, flip the `*_PROVIDER` selectors, and restore the
`zitch-reconcile-wema` cron in render.yaml (schedule `*/5 * * * *`, command
`python manage.py reconcile_wema`). Everything below is kept as the reference for
that resumption.

---

## What's wired

| Capability | Rail selector | Status |
|-----------|---------------|--------|
| Recipient name enquiry | `PAYOUT_PROVIDER=wema` | built, opt-in |
| Bank payout (transfer out) | `PAYOUT_PROVIDER=wema` | built, opt-in |
| Payout settlement (no webhook) | — | polled by `reconcile_wema` |
| Wallet funding account (NUBAN) | `PAYMENT_PROVIDER=wema` | built (OTP flow), opt-in |
| Inbound deposit crediting (no webhook) | — | polled by `reconcile_wema` |
| VAS — **airtime** | `VAS_PROVIDER=wema` | built, opt-in (debits user NUBAN) |
| VAS — data / bills | `VAS_PROVIDER=wema` | client built; **routing deferred** (catalog) |
| Cards / KYC | — | not yet |

## VAS (airtime / data / bills)

`VAS_PROVIDER=wema` routes **airtime** through Wema's `Client/PurchaseAirtime`,
debiting the sender's own NUBAN (`accountNumber`) — per-user model. Routing is
**per-service**: data, cable and electricity stay on VTU.ng regardless, so turning
Wema VAS on never breaks a service whose catalog isn't mapped.

**Data & bills are deferred** because Wema uses its own `packageCode` (data plans via
`GetDataPlans`) and `packageId` (billers via `GetAllBills`), which differ from the
VTU.ng codes stored in our `DataPlan` table / service ids. Before routing data/bills
through Wema, sync Wema's catalog into the app (a plan/biller mapping step). The Wema
client functions (`purchase_data`, `get_data_plans`, `get_bills`, `validate_bill_customer`,
`pay_bill`, `vas_status`) are built and tested, ready for that wiring.

Requires `WEMA_AIRTIME_KEY` (and `WEMA_BILLS_KEY` later). VERIFY-BEFORE-LIVE: the Wema
network code for airtime (name vs code) and the `clientId` field value.

## Environment variables

Set these in the host (never in source). Booleans-only status is visible at `/healthz`.

- `WEMA_CHANNEL_ID` — the single channel id (sent as `x-api-key`, or `access` on the
  credit/debit-wallet products). **Same value for all products.**
- `WEMA_WALLET_KEY` — Wallet-Services subscription key (`Ocp-Apim-Subscription-Key`);
  covers wallet-creation, account-maintenance, credit and debit.
- `WEMA_SOURCE_ACCOUNT` — our pool NUBAN that funds outbound transfers (see money-flow note).
- `WEMA_SECURITY_INFO` — the encrypted `securityInfo` for money-movement calls. **Not
  enforced in sandbox**; required before live.
- `WEMA_BASE_URL` — `https://apiplayground.alat.ng` (sandbox). Set the live host for go-live.
- `WEMA_SIMULATION=true` — serve the mock flow in a real build without live keys (no money moves).
- `PAYOUT_PROVIDER=wema`, `PAYMENT_PROVIDER=wema` — flip the rails on.

To test payout in sandbox: `PAYOUT_PROVIDER=wema` + `WEMA_CHANNEL_ID` + `WEMA_WALLET_KEY` +
`WEMA_SOURCE_ACCOUNT` (leave `WEMA_SECURITY_INFO` blank), redeploy, run a name-enquiry + a
small transfer. `/healthz` should show `payout_provider: "wema"`.

## ⚠️ Open decisions — confirm with Wema before go-live

1. **Money-flow model — RESOLVED: per-user balances.** Each user's NUBAN holds its own
   balance. `payout_send` debits the **sender's own NUBAN** (`execute_payout` passes the
   sender's `wallet.account_number` as `source_account`); the shared `WEMA_SOURCE_ACCOUNT`
   pool is only a fallback for a sender who has no Wema NUBAN yet (mixed migration). A live
   payout with neither fails closed (refundable). Note: to pay out via Wema a user must
   have a Wema NUBAN with balance — during migration, a user who funded via Monnify/Kora
   (no Wema NUBAN) can't be paid out from the pool unless `WEMA_SOURCE_ACCOUNT` is funded.
2. **`securityInfo` construction.** The encryption scheme (algorithm / what is signed /
   key material) is not in the OpenAPI. Implement in `utility.wema._security_info` once
   Wema supplies it. Sandbox does not enforce it.
3. **Transaction-status legend.** `confirm_transfer_status` returns a status string; the
   value set is undocumented. `reconcile_wema` matches SUCCESS/FAILED families defensively
   (`_SETTLED` / `_REVERSED`) and leaves anything else PENDING. Confirm the real values.
4. **Wallet-creation OTP response shape.** Per the OpenAPI the create endpoints return
   `ResponseModel` with no tracking id; `create_wallet_request` looks for the tracking id
   in several places to tolerate the live (undocumented) shape. Confirm where the live
   gateway returns `trackingId`/`otpTrackingID`.
5. **Inbound-credit detection.** Confirm `transhistoryV2` date format and that a genuine
   third-party deposit is distinguishable from any credit we push ourselves via
   `FundWallet` (today `credit_wallet` has no production caller, so the reconciler credits
   every `creditType == "Credit"` row — add a filter before wiring `credit_wallet`).

## No-webhook reconciliation

ALAT exposes no webhooks, so `python manage.py reconcile_wema` (render cron, every 5 min):
- **Funding:** sweeps each Wema-provisioned wallet's history and credits inbound deposits,
  idempotent on Wema's `referenceId` stored as `WEMA-CR-<referenceId>` (namespaced so it
  can never collide with a `ZTRF`/`ZPAY`/`ZFND` ledger reference).
- **Payouts:** settles/reverses PENDING bank payouts by polling `confirm_transfer_status`
  (only when `PAYOUT_PROVIDER=wema`).
