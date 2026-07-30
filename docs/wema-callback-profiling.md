# Wema/ALAT callback profiling — the send-to-the-bank packet

This is the **critical-path blocker** for the whole Wema rail. ALAT's integration
guide requires the bank to profile our callback URLs before the rails function:
account creation is refused without a profiled Account Creation URL, and money
movements fail authentication without the Authentication URL. Nothing in this
repository can close it — it needs a message to Wema and a reply.

Everything below is ready to send. Do the pre-flight in §1 first: handing the bank a
URL that answers `404`, or one that accepts any secret, is worse than sending nothing.

---

## 1. Before you send anything

Open, on the deployed host:

```
https://api.zitch.ng/wema-callbacks-diagnose?token=<WEMA_DIAG_TOKEN>
```

Read **`ready_to_send_to_the_bank`**. If it is `false`, the `blockers` array says
why; fix those and re-check. It verifies in-process that each of the four routes
resolves to its handler, and — the assertion that actually matters — that a
*deliberately wrong* secret is refused. Comparing the configured token against
itself would pass trivially and prove nothing.

The output embeds the callback secret, because with these routes **the secret is the
URL** (ALAT signs nothing, so the path carries the authentication). That is why the
endpoint sits behind `WEMA_DIAG_TOKEN`, and why the URLs below must travel to Wema
the same way you would send a credential.

Prerequisites for `ready_to_send_to_the_bank: true`:

| Setting | Why |
|---|---|
| `WEMA_CALLBACK_TOKEN` | The secret in the path. Unset means the URL carries no secret at all. |
| `WEMA_DIAG_TOKEN` | Opens the diagnose page. |
| The deploy is current | A route that doesn't resolve is a `404` for the bank, and a `404` on the Account Creation callback silently means no customer ever gets a NUBAN. |

Cheapest possible check with no tooling: open a callback URL in a browser. The
method check runs before the secret check, so a live route answers **405**
(POST-only). A **404** means the route isn't deployed; a **502/503** means the
service is down.

## 2. The four URLs

Copy these from the diagnose output rather than typing them — the token must be
exact. ALAT's own field names are on the left, so the table can be read straight
against the profiling form the bank sends.

| Give the bank as | URL |
|---|---|
| Account Creation Callback URL | `https://api.zitch.ng/webhooks/wema/account/<WEMA_CALLBACK_TOKEN>` |
| Authentication Callback URL | `https://api.zitch.ng/webhooks/wema/authorize/<WEMA_CALLBACK_TOKEN>` |
| Transaction Callback URL | `https://api.zitch.ng/webhooks/wema/transaction/<WEMA_CALLBACK_TOKEN>` |
| Transaction Notification URL (production only) | `https://api.zitch.ng/webhooks/wema/notification/<WEMA_CALLBACK_TOKEN>` |

All four are `POST`-only and accept the slashed and slashless spelling, so either
form the bank stores will land. Give the bank the **slashless** form.

Profile them in **both** the sandbox/dev and production environments. Sandbox
profiling is what lets the Step 7 smoke test in `wema-go-live-runbook.md` run at
all.

## 3. The message to send

> Subject: Callback URL profiling + three outstanding integration values — Zitch
>
> Hi <name>,
>
> Please profile the following callback URLs for our channel (`<WEMA_CHANNEL_ID>`),
> in both the sandbox and production environments:
>
> - Account Creation Callback URL: `…/webhooks/wema/account/<token>`
> - Authentication Callback URL: `…/webhooks/wema/authorize/<token>`
> - Transaction Callback URL: `…/webhooks/wema/transaction/<token>`
> - Transaction Notification URL: `…/webhooks/wema/notification/<token>`
>
> All four are POST-only and live now — a GET returns 405, which confirms the route
> is up.
>
> Three values are also still outstanding on our side. Each one currently gates a
> feature closed:
>
> **1. The `transactionStatus` legends.** `PartnerPayment/CheckTransactionStatus`
> returns an integer `transactionStatus` (1–11) and
> `PartnerPayment/checktransactionstatus` for bills returns 1–9, but we can't find
> the meanings documented. Please send the integer → meaning map for both. Until we
> have it, a timed-out airtime/data/bill purchase stays PENDING indefinitely: we
> won't auto-settle (which would debit a customer for an undelivered top-up) or
> auto-refund (which would pay twice for a delivered one) on a code we can't read.
>
> **2. The `cardKey` (card product id)** for the Virtual Naira Card
> `virtualCard` / `virtual-card-details` requests, plus the shape of the opaque
> `data` field those responses carry (masked PAN / expiry / CVV). Our card rail
> stays disabled without both.
>
> **3. The live host** for the production ALAT gateway — the base URL that replaces
> `apiplayground.alat.ng` — and confirmation that our live subscription keys are
> active against it.
>
> Two smaller confirmations, if convenient: the date format
> `transhistoryV2` expects, and the shape of a **reversal** entry in transaction
> history (so we can tell a genuine third-party deposit from a payout of ours that
> bounced back).
>
> Thanks,
> <name>

## 4. Verifying it took effect

Profiling is silent — the bank does not tell you it worked. Confirm it by observing a
real call:

1. **Account Creation.** Provision a NUBAN for a test user (BVN/NIN → OTP). The
   bank's Account Creation callback normally fires *before* the customer finishes
   the OTP step. Look for `wema_account_provisioned` in the logs with
   `source=callback` — that line proves the bank reached us. (The `wallet` and
   `banklink` loggers had to be added to `LOGGING` for these to ship at all; see
   #230.)
2. **Authentication.** Send a small payout. The authorisation callback fires
   mid-flight; a payout that hangs PENDING with nothing in the logs means the
   Authentication URL is not profiled.
3. **Transaction.** Watch `zitch-reconcile-wema` (every 10 minutes) settle the
   payout, and the transaction callback arrive independently.

`/healthz` will not tell you any of this — profiling is bank-side state with no
local reflection. The logs are the only signal, which is why they had to be fixed
first.

## 5. What is already handled

Worth knowing so the reply isn't over-read:

* **`securityInfo` is not owed to us.** Wema confirmed on 2026-07-27 that it is
  "a private key best known to you" which the bank echoes back to the Authentication
  Callback. We choose the value. It was a hard preflight gate on the mistaken belief
  that the bank issued it; it is now a soft check (#225).
* **The callback IP allowlist** (`WEMA_CALLBACK_ENFORCE_IPS`) is deliberately **off**
  until a real callback confirms the observed source IP. Turning it on before the
  first profiling round would make "the bank never called" and "we rejected the
  bank" look identical.
* **Idempotency** is already handled on all four routes, so redelivery is safe and
  the bank can retry freely.
