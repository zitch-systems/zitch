# Wema / ALAT migration — status & go-live checklist

The wallet money rails are being migrated to **Wema / ALAT** (Azure APIM Banking-as-a-Service).
Everything below is **opt-in** and mock/simulation-gated, so it changes no live behaviour
until the env vars are set. Kora/Monnify/VTU.ng/Prembly remain the defaults until Wema is
verified sandbox → live.

## What's wired

| Capability | Rail selector | Status |
|-----------|---------------|--------|
| Recipient name enquiry | `PAYOUT_PROVIDER=wema` | built, opt-in |
| Bank payout (transfer out) | `PAYOUT_PROVIDER=wema` | built, opt-in |
| Payout settlement (no webhook) | — | polled by `reconcile_wema` |
| Wallet funding account (NUBAN) | `PAYMENT_PROVIDER=wema` | built (OTP flow), opt-in |
| Inbound deposit crediting (no webhook) | — | polled by `reconcile_wema` |
| VAS / cards / KYC | — | not yet |

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

1. **Money-flow model (BLOCKER).** Deposits land in each user's per-user NUBAN, and the
   funding reconciler reads *per-user* transaction history. But `payout_send` debits a
   single shared `WEMA_SOURCE_ACCOUNT` pool. These two assumptions only reconcile if
   per-user NUBAN deposits **settle into that pool** (pooled/collection model). If instead
   each NUBAN holds its own balance, payouts must source from the **sender's own NUBAN**
   (thread `wallet.account_number` into `payout_send`), or the pool drains while user
   NUBANs accumulate. **Confirm which model your Wema contract uses.**
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
