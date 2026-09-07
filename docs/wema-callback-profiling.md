# Wema/ALAT callbacks — what is done, and what is still owed

Corrected 30 July 2026 after reading the `#zitch` Slack Connect channel (Wema's
workspace). The first version of this file treated callback profiling as the open
critical-path blocker and gave you a message asking the bank to profile the URLs. That
was wrong: **it had already been done on dev two days earlier.** What follows is the
actual state, from the channel.

---

## 1. Status

| | |
|---|---|
| **Dev/sandbox callbacks** | **Profiled.** URLs sent 28 Jul 10:47; Temi Orekunrin confirmed *"this has been profiled on dev"* at 12:27 |
| **Production callbacks** | Deliberately deferred. You told Wema *"You can profile when we migrate to production"* and they agreed. **Not a blocker now — a scheduled step at migration.** |
| **Wema compliance approval** | **Granted 24 Jul.** Jumoke Fayemiwo: *"We have Compliance Approval for the Customer. Kindly assist to push the Partner live"* |
| **Bank egress IPs** | **Supplied in writing** — see §2 |
| **`securityInfo`** | Resolved 27 Jul; a value we choose. See `wema-migration.md` |
| **VAS status legend, `cardKey`, live host + keys** | **Never asked.** See §3 |

The four URLs, as profiled on dev (the token is `WEMA_CALLBACK_TOKEN`; with these
endpoints the secret *is* the URL, because ALAT signs nothing):

```
https://api.zitch.ng/webhooks/wema/account/<WEMA_CALLBACK_TOKEN>
https://api.zitch.ng/webhooks/wema/authorize/<WEMA_CALLBACK_TOKEN>
https://api.zitch.ng/webhooks/wema/transaction/<WEMA_CALLBACK_TOKEN>
https://api.zitch.ng/webhooks/wema/notification/<WEMA_CALLBACK_TOKEN>   (production only)
```

Before handing these to the bank again for production, send `GET
https://api.zitch.ng/wema-callbacks-diagnose` with `Authorization: Bearer
<WEMA_DIAG_TOKEN>` and read `ready_to_send_to_the_bank`. It returns templates without
the callback secret and checks in-process that each route resolves and — the
assertion that matters — that a *deliberately wrong* secret is refused.

## 2. Two things to change on your side, now

### Turn the callback IP allowlist on

Asked on 24 Jul for Wema to whitelist `209.97.130.65` / `68.183.254.113`. Temi's answer
on 27 Jul:

> "**Whitelisting IPs will be done from your end.** Here Egress IP1 = 135.236.18.76,
> Egress IP2 = 74.178.162.156"

So two things are true. Wema does **not** allowlist our egress — that request was a
misunderstanding, and nothing on the bank side has our IPs. And the two addresses they
gave are **exactly** the values already compiled in as the default `WEMA_CALLBACK_IPS`,
now confirmed by the bank in writing.

Production now defaults `WEMA_CALLBACK_ENFORCE_IPS` on and refuses to start the money
rail readiness gate unless the allowlist is configured. In development, first use the
callback diagnostic to verify Render's trusted-proxy hop; otherwise an allowlist can
compare Wema's addresses with an internal platform proxy and refuse every callback.

```
WEMA_CALLBACK_ENFORCE_IPS=true
WEMA_CALLBACK_IPS=135.236.18.76,74.178.162.156
```

Keep the values environment-overridable and treat any bank IP change as a controlled
configuration release. Failing closed is intentional; update the allowlist only after
written confirmation from Wema.

### Rotate the callback token before production

The token was posted twice into `#zitch` — a private channel in **Wema's** Slack
workspace, nine members, unknown retention, searchable forever.

The bank must have it; that is inherent to a secret carried in the URL. But a chat log in
another organisation's workspace is far wider exposure than the design assumes, and the
**same token was given for the dev and production URLs** — so a dev-side leak is a
production compromise.

`WEMA_CALLBACK_TOKEN_PREV` exists exactly for this: set the new token as
`WEMA_CALLBACK_TOKEN`, keep the old one in `_PREV` for the overlap, and drop `_PREV` once
the bank is calling the new URL. Use a **different** token for production than dev, and
deliver it out of band — not into the shared channel.

## 3. What is still owed by Wema

None of the following has been raised in the channel. Searching the whole history: the
25 Jul "gentle reminder" and Jumoke's 27 Jul *"we will provide a response shortly"* were
both about `securityInfo`, which was then answered.

> Subject: Outstanding integration values + production go-live — Zitch
>
> Hi Temi, Jumoke,
>
> Thanks for profiling our dev callbacks on the 28th. Four things outstanding:
>
> **1. The `transactionStatus` legends.**
> `PartnerPayment/CheckTransactionStatus` returns an integer `transactionStatus` (1–11),
> and `PartnerPayment/checktransactionstatus` for bills returns 1–9, but we can't find
> the meanings documented. Please send the integer → meaning map for both. Until we have
> it, a timed-out airtime/data/bill purchase stays PENDING indefinitely: we won't
> auto-settle (which would debit a customer for an undelivered top-up) or auto-refund
> (which would pay twice for a delivered one) on a code we can't read.
>
> **2. The `cardKey`** (card product id) for the Virtual Naira Card `virtualCard` /
> `virtual-card-details` requests, plus the shape of the opaque `data` field those
> responses carry (masked PAN / expiry / CVV). Our card rail stays disabled without both.
>
> **3. Production access.** Jumoke confirmed Compliance Approval on 24 July and asked the
> team to push the Partner live — could you confirm where that stands? We need the live
> ALAT base URL (replacing `apiplayground.alat.ng`) and confirmation our live subscription
> keys are active against it. We'll send the callback URLs for production profiling as
> soon as we cut over.
>
> **4. Two smaller confirmations,** if convenient: the date format `transhistoryV2`
> expects, and the shape of a **reversal** entry in transaction history — so we can tell a
> genuine third-party deposit from a payout of ours that bounced back.
>
> Thanks,
> Adetayo

## 4. What "profiled on dev" does and does not prove

From Temi on 2 July:

> "**Purely simulated**, the account that will be returned to your webhook is a sample
> response of what you should be expecting on production. Default OTP - 123456 will be
> used for testing as well"

So the dev callbacks are registered against a simulator returning canned shapes. That
proves routing and authentication end-to-end, and nothing about the payloads that matter:
the real reversal shape, the real `transactionStatus` integers, and the real card `data`
blob are all still unknown, which is why §3 asks for them explicitly rather than planning
to discover them in production.

The Step 7 smoke test in `wema-go-live-runbook.md` (small real money) can therefore only
run on production, as that document already says.

## 5. Confirming production profiling took effect, when you get there

Profiling is silent — the bank does not tell you it worked, and `/healthz` cannot see
bank-side state. The logs are the only signal, which is why they had to be fixed first
(#230 added the `wallet` and `banklink` loggers, without which none of this shipped):

1. **Account Creation.** Provision a NUBAN for a test user (BVN/NIN → OTP). The bank's
   callback normally fires *before* the customer finishes the OTP step. Look for
   `wema_account_provisioned` with `source=callback`.
2. **Authentication.** Send a small payout. A payout that hangs PENDING with nothing in
   the logs means the Authentication URL is not profiled.
3. **Transaction.** Watch `zitch-reconcile-wema` settle it, and the transaction callback
   arrive independently.

Cheapest check with no tooling: open a callback URL in a browser. The method check runs
before the secret check, so a live route answers **405** (POST-only). A **404** means the
route isn't deployed; **502/503** means the service is down.

## 6. Already handled — so the reply isn't over-read

* **`securityInfo` is not owed to us.** Temi, 27 Jul: *"the security info is a private key
  best known to you… All we do is to call your authentication webhook URL to confirm if
  the transactions are coming from you."* We choose the value. It was a hard preflight gate
  on the mistaken belief the bank issued it; now a soft check (#225).
* **Idempotency** is handled on all four routes, so the bank can retry freely.
* **Every inbound callback is now recorded**, including refused ones
  (`whatsapp.WebhookEvent`, #231) — so "did the bank call?" is answerable from the
  database rather than from log retention.
