# WhatsApp production operations

The WhatsApp webhook is an authenticated ingress, not a job runner. In production it
stores each Meta message in Postgres, acknowledges only after the insert commits, and a
dedicated worker executes the command. Broadcasts use the same database-backed outbox.

## Safety properties

- Meta webhook bodies require `X-Hub-Signature-256` and the configured phone-number ID.
- `wa_message_id` is unique, so Meta redelivery cannot execute a command twice.
- Original command text is encrypted with `WHATSAPP_QUEUE_KEY` and erased after success
  or terminal dead-lettering. The support log retains only redacted text.
- A five-minute lease recovers jobs abandoned by a crashed worker. Retryable responses
  (currently HTTP 429) back off; an ambiguous timeout/5xx is recorded as `unknown` and
  is never blindly resent.
- Every broadcast requires a different operator to approve it. Recipients are
  materialised before the first provider call, and marketing recipients must have
  `marketing_opt_in=true`.
- A production worker refuses to start unless the channel is fully live. This prevents
  it executing a financial command while unable to send a receipt.

## Required Render topology

`render.yaml` defines:

1. `zitch-api` (paid web service),
2. `zitch-whatsapp-worker` (paid background worker),
3. `zitch-cache` (private shared Key Value), and
4. `zitch-db` with no public Postgres allow-list entries.

The worker references the API service's secret environment variables with Render
`fromService.envVarKey`; do not copy or independently rotate them. The API and worker
must share `DJANGO_SECRET_KEY`, `DJANGO_KYC_HASH_KEY`, `DJANGO_OTP_HASH_KEY`,
`WHATSAPP_QUEUE_KEY`, `DATABASE_URL`, and `REDIS_URL`.

To rotate the queue key without losing an in-flight command, first copy the old value
to `WHATSAPP_QUEUE_KEY_PREV`, set a new 32-byte-or-longer `WHATSAPP_QUEUE_KEY`, and deploy
both API and worker. After the queue contains no jobs encrypted before that deployment,
remove `_PREV`. New payloads are always encrypted with the current key.

The connected Render workspace was inspected on 2026-08-01. At that point there was no
Key Value, worker, or declared cron service; the web service was on the free plan with a
stale start command, and Postgres allowed `0.0.0.0/0`. Repository changes do not alter
those live resources.
An operator must review the paid resources and either sync the Blueprint against the
existing service or apply the equivalent dashboard changes. Confirm that Render links
the existing display-named service (`zitch main app`, slug `zitch-api`) rather than
creating a duplicate.

## The variables that switch the channel on

Every `WHATSAPP_*` key in `render.yaml` is `sync: false` — blank until an operator
types it into the Render dashboard. With them blank, production resolves to
`WHATSAPP_MODE=disabled`, and a disabled channel makes the webhook return **404 to
Meta**: no message is ever seen, no row is ever written, and every other reading
says the service is healthy. `/healthz` reports `whatsapp_mode` so this is visible
without a login.

Set all of these on **zitch-api** (the worker inherits them via `fromService`):

| Variable | Where it comes from |
| --- | --- |
| `WHATSAPP_MODE` | `live` |
| `WHATSAPP_TOKEN` | Meta → WhatsApp → API Setup → permanent access token |
| `WHATSAPP_PHONE_NUMBER_ID` | Meta → WhatsApp → API Setup (the ID, not the number) |
| `WHATSAPP_BUSINESS_NUMBER` | the display number in international form, e.g. `2348…` |
| `WHATSAPP_VERIFY_TOKEN` | any string you choose — paste the SAME one into Meta's callback form |
| `WHATSAPP_APP_SECRET` | Meta → App Settings → Basic → App Secret |
| `WHATSAPP_QUEUE_KEY` | any 32+ byte random string (encrypts queued command text at rest) |

Then in the Meta dashboard set the callback URL to
`https://api.zitch.ng/webhooks/whatsapp`, verify it (Meta calls `GET` with your
verify token), and **subscribe it to the `messages` field**. A saved-but-unsubscribed
callback is the classic silent misconfiguration: verification passes, and no message
is ever delivered.

Confirm with `/healthz`: `whatsapp_mode: "live"` and, after sending one message,
`whatsapp_webhook_reached: true` with `whatsapp_outbound_failing: false`.

Those answer different halves of the question and both are needed. `reached` covers
the inbound leg; `outbound_failing` covers the reply. A channel can receive
perfectly and still be mute — an expired token fails only the send — so a green
`reached` on its own has never been proof the bot works.

Use a **permanent System User token**, not the one on the API Setup page: that one
expires after 24 hours, at which point every reply is refused while inbound carries
on working normally. That flag counts only calls we **accepted** — a
signed call naming the wrong phone-number id is refused with a 400 and does not
count, which is the point: it is the one wrong value no boot check can catch.

Note what booting proves. A live channel fails closed at startup if any of `TOKEN`,
`PHONE_NUMBER_ID`, `APP_SECRET`, `VERIFY_TOKEN`, `BUSINESS_NUMBER` or a 32-byte
`QUEUE_KEY` is missing (`settings.py`), so a *running* production service has all of
them set. What no boot check can verify is whether each holds the **correct value** —
which is why a wrong `APP_SECRET` or `PHONE_NUMBER_ID` is the residual failure mode
once the service is up, and why both now have their own counter above.

## Chat signup (onboarding new customers in WhatsApp)

`WHATSAPP_ALLOW_CHAT_SIGNUP=true` lets a brand-new number open an account in the
chat: first name, last name, email, 4-digit PIN. The account is created at the
**unverified floor** (internal tier 0 — ₦20,000/txn, the regulatory Tier-1
equivalent) with **no app password** and the email stored **unverified**.

Raising limits is deliberately app-only, and both contact channels are re-proven
on the way:

1. The customer downloads the app and taps **Forgot password** — the account has
   no usable password, so this is the only door in, and its SMS OTP re-verifies
   the phone.
2. The KYC screen requires confirming the chat-collected email (a code sent to
   the inbox) before any identity step — every KYC endpoint returns 403 until
   then. Until that confirmation, the address also never receives password-reset
   codes: it was typed into a chat, one typo from someone else's inbox.
3. BVN/NIN and above proceed as normal.

### Verifying identity in the chat (menu 8)

`8` / `verify` runs the whole KYC ladder without the app:

1. **Phone** — a 6-digit SMS code to the number on the account. Possession of
   the WhatsApp chat is deliberately NOT accepted as proof: a messenger session
   outlives a SIM swap, so the code goes to the SIM and must come back.
2. **Email** — a 6-digit code to the address itself (never by SMS; a code
   delivered to the phone proves nothing about the inbox). `change` corrects a
   mistyped address mid-flow.
3. **BVN**, then **NIN** — both required. The numbers are stored hashed and are
   masked out of the message log by the same rule that masks PINs.

Codes are single-use, expire in 10 minutes, and burn the flow after 3 wrong
attempts.

**Both rails must be configured or the step refuses honestly.** `send_sms` and
`send_email` each return a silent-success dict when they have no API key, so a
caller that trusts `success` announces a code it never delivered. The step
therefore checks `sms_live()` / `email_live()` BEFORE sending and tells the
customer the channel is unavailable rather than pointing them at a phone that
will never buzz. In practice: no `TERMII_API_KEY` means no phone verification,
no `RESEND_API_KEY` means no email verification.

For a demo deploy with no rails, the existing `TEST_OTP_PHONE` + `TEST_OTP_CODE`
pair (the same switch app signup honours, gated by `ALLOW_PRODUCTION_TEST_OTP`
off DEBUG) makes the walkthrough possible with a fixed code. `wema_preflight`
already hard-fails while it is set, so it cannot quietly survive to go-live.

**The one-identity limit.** Our bank has no standalone BVN/NIN lookup — the
real, name-matched check happens during NUBAN creation, and it verifies exactly
ONE identity. The second is therefore stored (hashed) and queued for the
operator KYC review in the portal, which is what lifts the flag; `recompute_tier`
picks it up from there. The customer is told it is under review rather than
being dead-ended, and the tier is NOT granted while it is pending.

### Choosing the model provider

The console (**AI controls → Model provider**) picks which LLM reads a
customer's sentence: Claude, OpenAI, Gemini, Grok, Groq, DeepSeek, Kimi, Qwen,
or any OpenAI-compatible endpoint. Two wire formats cover all of them, so
adding a provider is usually a row in `whatsapp/llm.PROVIDERS`.

Swapping the provider cannot change what the platform will DO. The model only
proposes an intent; `router.py` validates it and is the only thing that moves
money. A worse model means worse comprehension and a fallback to the
deterministic menus — never a payment the rules would not allow.

Operational notes:

- The API key is **encrypted at rest** (Fernet, keyed from `SECRET_KEY`) and is
  never returned by any endpoint — the console shows the last four only. Saving
  a model change does not require re-pasting the key.
- **Test connection** does a live round-trip, so a wrong key is found by an
  operator rather than by customers.
- A **custom** endpoint must be HTTPS and must resolve to a public address.
  Private and link-local targets are refused: a configurable base URL otherwise
  turns the backend into an SSRF probe carrying a bearer header, with cloud
  metadata (169.254.169.254) the obvious target.
- Config falls back to the `LLM_*` env vars when nothing is stored, so an
  untouched deployment behaves exactly as before.
- Rotating `SECRET_KEY` makes a stored key undecryptable; it reads as "not
  configured" and is re-entered in the console.

### What the model may see — and what it may not

Two rules, enforced in `whatsapp/ai.sanitize_for_model` before any provider is
called (and mirrored in the support-log redaction):

1. **Secrets never reach a model.** Card numbers (Luhn-confirmed, however they
   are spaced), and any short digit group in a message that mentions
   pin/otp/cvv, are REMOVED — not tokenized. No intent needs them; PIN entry
   lives in the encrypted Flow, OTP entry in its deterministic state, and card
   details belong in the app, never in free text.
2. **Identifiers are tokenized in, re-hydrated out.** Account, meter, smartcard
   and phone numbers become opaque tokens (`num_ref_1`, …); the model routes a
   de-identified sentence and copies tokens into fields; the router swaps the
   real values back before dispatch. The stored `intent_json` keeps only the
   tokens, so the ops console shows routing quality without customer numbers.

Amounts survive: "send 5000" keeps its 5000 — the secret-context words are the
discriminator between an amount and a PIN, since the shapes are identical.

### Receipts carry the sender, never the balance

A receipt is designed to be forwarded as proof of payment, so every receipt
leads with who paid — full name plus the last four digits of the sender's phone
(never the whole number, which on a shared artifact invites impersonation).

The balance is never a receipt row. It is sent as a **separate message after**
the receipt, so the customer can forward the receipt to whoever they paid
without handing over their account balance, and can delete the balance message
independently.

### Linking is PIN-gated, single use, 30 minutes

A link binds a channel that can move money, so `/api/whatsapp/link/start/`
requires the transaction PIN — an unlocked phone must not be enough to bind a
stranger's WhatsApp to an account. The PIN check shares the app's brute-force
lockout.

Codes last 30 minutes and are consumed on first use. A code arriving from a
number that is not the one on the account is **refused and burned**: that is the
shape of a leaked code being tried from an attacker's WhatsApp, and leaving it
live would let them keep trying from other numbers. The owner mints a fresh one
in the app.

### The signup PIN never enters the chat

WhatsApp gives a business no way to delete or expire a message it received, and
there is no view-once for text — so a PIN sent as chat text stays in the
customer's history for good. The only real protection is never asking for it
there, which is what the signup does, in the same order the money flows use:

1. **Secure Flow** (`WHATSAPP_FLOW` configured) — the PIN is typed into a native
   masked field and submitted encrypted. Set-then-confirm runs as two
   data_exchange round-trips on the already-published `PIN_SCREEN`, so no Flow
   JSON change or re-publishing with Meta is needed.
2. **Dev/test only** — chat entry, masked in the message log.
3. **Production without Flows** — no PIN is collected at all. The account is
   created without one and the customer sets it in the app; nothing that spends
   money works until they do.

A PIN typed into the chat anyway is masked in our log by shape as well as by
flow state, and the customer is told to delete it from their own thread — the
one place we cannot reach.

### Funding account (NUBAN) from the chat

Signup rolls straight into minting the customer's dedicated Wema account —
choose BVN or NIN, enter it (masked out of the message log), then the bank's
SMS OTP. Menu option **6 (Add money)** runs the same setup any time, and **7
(My account details)** shows name, phone, email, tier and the funding account.

It drives the same `wallet.views` code the app screen does
(`_start_wema_attempt` → `complete_wema_provisioning`), so the identity checks,
holder-name match, PND lift and tier binding are identical — there is no
WhatsApp-specific provisioning path. When `WEMA_FUNDING` is off, signup skips
the offer and option 6 says setup is unavailable.

The verified email is not only a chat-signup gate: **Tier 1 requires a verified
email for every account** (BVN + NIN + verified email; the phone is proven by
construction — app signup requires the SMS OTP, WhatsApp signup is possession
of the number). App-signup accounts see the same "Confirm your email" row on
the KYC screen and are held at the unverified floor until it's done.

## Activation sequence

1. Merge only after CI is green and apply migrations before starting the worker.
2. Provision/link `zitch-cache`; confirm both processes receive the same `REDIS_URL`.
3. Configure identical WhatsApp/provider secrets on `zitch-api`; worker references them.
4. Keep `WHATSAPP_MODE=disabled` until Meta verification, templates, callback URL and
   (if used) Flow public key are complete.
5. Set `WHATSAPP_MODE=live` and start the worker. A missing live credential causes a
   fail-fast boot error.
6. Send one signed test message, then run an approved one-recipient utility template.
   Confirm inbound `processed_at`, recipient status, and audit entries.
7. Monitor queue age, `processing_error`, broadcast `unknown`, worker restarts, Meta
   delivery failures, and Sentry. Treat `unknown` as a manual reconciliation case.

## When the bot stops replying

This failure is quiet by construction. The webhook's job is to store and acknowledge,
so it answers Meta `200` whether or not anything downstream is working; the router —
where every log line and audit row is written — is only reached by a queue consumer. A
chat can therefore go silent while the service looks perfectly healthy from `/healthz`,
the Render metrics, and Meta's delivery reports alike.

The quietest case of all is a callback that never arrives: it leaves no queue row, no
log line and no audit trail, so every other reading says "idle and healthy". That is
why the verdict checks reachability first and everything else second.

Start at **`/admin/diagnostics/` → WhatsApp**, which needs nothing but an admin login,
and read `verdict` first. (`GET /whatsapp-diagnose` returns the same JSON for
scripting.) The causes it distinguishes, in the order they actually occur:

| What you see | What it means | Fix |
| --- | --- | --- |
| `webhook.ever_accepted_a_call: false` | **Meta has never reached us.** Nothing below the webhook can explain the silence, and no other number shows this — an uncalled webhook leaves no queue row, which reads exactly like an idle, healthy channel | In the Meta dashboard, confirm the callback URL is saved AND subscribed to the `messages` field, and that `WHATSAPP_VERIFY_TOKEN` matches |
| `webhook.rejected_signature` climbing, `ever_accepted_a_call: false` | Meta is calling; we are refusing every call | `WHATSAPP_APP_SECRET` does not match the app secret in the Meta dashboard |
| `webhook.rejected_wrong_phone_number_id` climbing, `ever_accepted_a_call: false` | Meta is calling AND the signature verifies, but each call names a phone-number id we do not recognise, so it is refused with a 400 and no message is read | `WHATSAPP_PHONE_NUMBER_ID` is not the id of the number being messaged. The boot check only proves it is set — never that it is the right id |
| Rows stuck at `attempt 0`, `processing_error` empty, nothing in the logs | The web-drain daemon thread is being reaped before it runs — the signature of a hobby-tier instance that recycles or spins down right after the response. The thread dies silently: no `wa_web_drain_failed`, no claim, no trace | Set `WHATSAPP_PROCESS_INLINE=true` on the web service: the message is processed inside the webhook request itself — the one execution context such a host guarantees — and the drain thread is skipped entirely. Costs webhook latency; right for chat volumes on a host without a trustworthy worker or thread |
| `worker_appears_stalled: true`, `unprocessed` climbing | The messages are **failing**, not merely unattended — the web service drains the queue itself after every callback, so an old backlog is not an idle-worker symptom | Open those rows and read `processing_error`. If it is empty, no webhook has arrived since they queued: start `zitch-whatsapp-worker`, or select the rows in **WhatsApp → Message logs** (filter `processed_at` = empty) and run **Process now** |
| `mode: disabled` | No token / phone-number id, so the webhook returns `404` and Meta gets nothing | Set the WhatsApp credentials and `WHATSAPP_MODE=live` |
| `mode: sandbox` | Inbound is routed, outbound is mocked — replies are generated and thrown away | Same as above |
| `dead_lettered` above zero | The worker ran and kept failing | Open those rows and read `processing_error` |
| `handed_to_human` lists a number | Deliberate: `status=human` mutes the bot so an agent can take over, and it stays muted until someone closes it | **WhatsApp → Conversations** → **Return to bot** |

The worker refuses to boot unless the channel is fully live, so a half-configured
deploy shows up as a stalled queue rather than as replies sent without receipts.

**The web service drains the queue too.** After acknowledging a callback it starts a
bounded background drain (one at a time per process, five messages, every failure
swallowed). Rows are claimed with `SELECT FOR UPDATE` and a lease, so this never
double-processes alongside a running worker — whichever claims a row first wins.
The worker remains the primary consumer and is what you want for throughput and for
draining broadcasts; the web drain exists so that a worker which is stopped, crashed
or never created cannot silently swallow every reply. Set `WHATSAPP_WEB_DRAIN=false`
to turn it off once a dedicated worker is confirmed healthy.

## Commands

```bash
# One safe local/test drain
python manage.py whatsapp_worker --once

# Production worker command (already in render.yaml)
python manage.py whatsapp_worker --batch-size 50 --poll-seconds 0.5

# Schema and regression gates
python manage.py makemigrations --check --dry-run
python manage.py test whatsapp admin_api.test_operator_controls admin_api.tests
```

Do not run more workers as a throughput shortcut without load-testing row-lock
contention and Meta rate limits. Database claims prevent duplicate ownership, but Meta
quota and downstream financial-provider capacity remain external constraints.
