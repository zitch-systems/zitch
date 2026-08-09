# End-to-end security audit — WhatsApp banking + mobile app

Audit date: August 2026. Scope: the WhatsApp channel (`backend/whatsapp/`), the
money rails it drives (`backend/wallet/`, `backend/transfers/`,
`backend/utility/`), the Expo app (`app/`, `lib/`, `components/`), and the
consistency between the two surfaces.

Findings were traced through real code paths and each fix landed with a
regression test. Findings that turned out to be wrong on inspection are recorded
too — a rejected finding is worth as much as a fixed one, and re-litigating it
later is waste.

## Fixed

### Separated identifiers escaped the model boundary (high)

`0123 456 789` and `0123-456-789` are how a Nigerian customer types an account
number. The de-identification pattern matched only *contiguous* runs of 7+
digits, so both forms went to the configured LLM provider verbatim and were
stored un-redacted in `WaMessageLog.text` — a space defeating the whole design.
Both the sanitizer and the log redactor now tolerate single spaces and dashes
between digits; the mapping stores digits only, since the bank validates the
bare number and re-hydrating the customer's spacing would push it into a field
that rejects it.

### FX skipped the velocity brake, and foreign→foreign skipped every cap (high)

`execute_fx`/`_move` change balances directly rather than through
`wallet.services.debit()`, so currency conversion was the one money-out path
that never called `velocity_exceeded` — the compromised-account brake every
other flow enforces. Worse, the tier and daily ceilings were applied only when
the SOURCE was NGN, so a foreign→foreign conversion had no amount ceiling at
all. Someone holding a stolen PIN could drain a foreign balance through
back-to-back USD→GBP→CAD hops without tripping anything.

The brake now applies to every conversion. Foreign-source conversions are
compared against the NGN equivalent of the sale, because the tier limit is
denominated in naira and "1000" of a strong currency otherwise slips under a cap
written for NGN. A pair we cannot price **fails closed** — an uncapped
conversion is precisely what the check exists to prevent.

### `setthumbprint` was reachable by deep link without a guard (high)

The app registers the `zitch://` scheme, and every other post-login screen in
the unguarded `(auth)` group is explicitly wrapped in `AuthGuard` (`resetpin`,
`securitysetup`, `kyc`, `accountdetails`). `setthumbprint` — which turns
biometric sign-in ON after a purely local scan, with no backend call — was not.
Since an idle-locked session deliberately keeps its token on the device for fast
biometric resume, anyone with an OS-unlocked device could open
`zitch://setthumbprint`, enable biometric sign-in with one scan, and thereafter
enter the account without a password. Now wrapped like its siblings.

### Bank payout could double-debit on a connectivity failure (high)

Every money screen in the app keeps its idempotency key when a request fails
with `offline` (delivery unknown), so a retry replays server-side rather than
paying twice — `spend_key()` uses the client key verbatim as the dedup key.
`banklink.tsx`'s wallet→bank payout was the exception: both the definitive
rejection and the ambiguous failure called `closeAll()`, which cleared the key,
and reopening the sheet minted a new one. A timeout on a payout that HAD
executed, followed by a natural retry, disbursed a second payout. It now keeps
the sheet and the key on an ambiguous outcome and says so.

### WhatsApp paid against a stale recipient name (medium)

The app re-runs the name enquiry in the same request as the payout and blocks on
mismatch, because routing is purely by `{account_number, bank_code}` — a stale
name means money reaching a different real person. WhatsApp resolved the name
once at the "bank" step and reused it minutes later at PIN-confirm. It now
re-resolves immediately before `execute_payout` and aborts on mismatch, matching
the app.

### Daily-limit pre-checks used the wrong bucket (medium)

Airtime and electricity pre-checked the **transfer** cap; data and cable
pre-checked no daily cap at all. The authoritative check inside `_run_vtu` and
`debit()` always used the correct **bill** bucket, so nothing could execute past
the real cap — but customers were refused purchases the app allows (transfer
spend blocking a bill purchase), and data/cable customers were sent an SMS OTP
before being told they were over. All four now pre-check the bill bucket.

### Per-transaction minimums differed by surface (medium)

WhatsApp accepted ₦10 transfers the app refused at ₦50, and ₦100 electricity the
app refused at ₦500 — the same account behaving differently depending on where
the customer stood. The electricity guard was also inconsistent with its own
error message. `MIN_TRANSFER`, `MIN_AIRTIME` and `MIN_ELECTRICITY` now live in
`common/http.py` and both surfaces read them.

### Card PAN/CVV was exposed to app-wide screen capture (medium)

Screenshots are allowed app-wide on purpose (receipts, support), but a full PAN
plus CVV on screen is the one place that policy costs something: an
in-foreground recorder catches it inside the 60-second reveal window, which the
auto-hide and background-clear cannot reach. Capture is now blocked for exactly
as long as the details are visible, then the app-wide policy is handed straight
back.

### Identity numbers lived in the chat transcript forever (high)

BVN and NIN were typed into the thread during chat KYC. The message log redacts
them and the numbers are stored hashed, but neither fact touches the artifact
that matters: the customer's own copy of what they sent. WhatsApp has no
view-once for text and lets only the **sender** delete a message, so an 11-digit
identity number sat in the conversation indefinitely — recoverable from any
backup or any handset that later opens that chat.

They are now collected on an `IDENTITY_SCREEN` in the encrypted Flow, exactly as
the PIN is, and a number typed into the chat while that Flow is open is refused
rather than read — accepting it would put in the transcript precisely what the
Flow exists to keep out. Unlike the PIN this does **not** fail closed: refusing
service would block every signup on a deploy where Flows are unconfigured, so the
fallback asks in chat and names the one removal that works. Meta serves the
published Flow version, so `pin_flow.json` must be re-published for this to take
effect.

### Any account could spend before proving who owned it (high)

`recompute_tier` required verified email, phone, BVN and NIN for Tier 1 — but
Tier 0 still carried a ₦20,000 per-transaction and daily allowance, so an
unverified account could move money. The four checks are now a floor beneath the
ceilings rather than only an input to them: `unverified_error` refuses any
outbound movement until all four pass, and the refusal names the missing step
rather than a tier number.

It is enforced in `spend_limit_error` — which `debit()` re-runs under the wallet
row lock, so it holds on every surface at once — and separately in `execute_fx`,
because conversion reaches the ledger through `_move` rather than `debit()` and
would otherwise have stayed open. Funding is deliberately still allowed while
unverified: blocking a deposit strands money in a NUBAN its owner cannot then
use.

**Operational note:** this takes effect for existing customers the moment it
deploys. Anyone currently sitting at Tier 0 is refused their next spend until
they finish verification.

### A refused email still told the customer a code was on its way (high)

Traced from a live report that WhatsApp email verification "wasn't working" while
`RESEND_API_KEY` was demonstrably set. Two defects compounded:

`_kyc_send_email_code` never looked at `send_email`'s return. The SMS branch
beside it has always checked `sent["success"]` — email did not, so a provider
rejection still printed *"📧 We sent a 6-digit code to …"* and armed a pending
action waiting for a code that had been refused at the API. The customer sees a
working flow and a mailbox that never fills.

Neither `send_email` nor `_send_sms_termii` logged its rejection reason. Both
return it in the dict, and almost every caller drops the dict by design
(anti-enumeration) — so the reason reached nobody and the operator's logs were
silent during the whole incident. Both now log `email_rejected` / `sms_rejected`
with the provider's own message. The recipient is not logged: for the email case
the fault concerns the *sender*, which is what the log records.

The trigger is worth stating plainly, because the shape recurs: **a key being
present is not the same as the rail working.** Resend refuses a send whose
`FROM_EMAIL` sits on a domain not verified on the account that key belongs to.
`RESEND_API_KEY` was set, the health endpoint reported `email_resend: true`, and
`wema_preflight` passed it — all three check only that the string is non-empty.
`send.zitch.ng` must be a verified domain on the same Resend account as the key,
and no code change can substitute for that.

### Link-code copy contradicted the code (low)

The app told users the code expires in 10 minutes; it is 30 and single-use.

## Accepted risks (deliberate, documented)

**WhatsApp cannot enforce the new-device step-up.** The device fingerprint
arrives in an HTTP header, so `device_for()` returns empty for the router and
`new_device_step_up_error` short-circuits to "allowed". The channel that
authenticates purely by phone possession is therefore the one where the
SIM-swap-oriented step-up cannot fire. Residual controls: PIN + lockout, tier
caps, daily caps, velocity. This is a real asymmetry and should be a decision
rather than an accident — a WhatsApp-native signal (e.g. step up on a large
transfer from a link created in the last N hours) would close it.

**WhatsApp cannot take a biometric.** The Cloud API exposes no biometric
capability to a business, and Flows have no such component — there is no way to
require a fingerprint or face scan for a payment authorised inside WhatsApp. The
PIN is the strongest factor available in-channel, and it is already collected in
the encrypted Flow rather than the chat. The only route to biometric
authorisation from WhatsApp is a deep link that hands the confirmation to the
app, where biometric payments already exist; that is a new surface (a signed
hand-off token, an app route, and a return path) rather than a setting, and is
not built.

**`OPS_REQUIRE_MFA` is off by default.** Operator TOTP is fully built and
tested, with replay protection; enforcement is off so a rollout cannot lock
every operator out at once. Turning it on — after the team enrols — is the
single highest-value control still available.

## Verified clean (checked, not assumed)

- **Webhook auth/replay**: HMAC-SHA256 over the raw body, fails closed in live
  mode without a secret, dedupes on Meta's message id via a DB unique
  constraint, per-sender throttle independent of Meta's shared source IP.
- **Flow crypto**: RSA-OAEP + AES-GCM per Meta's spec; flow tokens bind action
  id AND msisdn under HMAC, compared with `compare_digest`, and require the
  action still be in the PIN state and unexpired. The data-exchange endpoint
  re-verifies Meta's signature on top of envelope encryption.
- **PIN and lockout**: one implementation (`evaluate_transaction_pin`), row-locked
  on the user, 5 attempts / 15 minutes, shared by chat, Flow and app — a lockout
  on one surface is a lockout on all.
- **Idempotency / double-spend**: wallet row `select_for_update()` plus a DB
  unique constraint on `(user, idempotency_key)`; every WhatsApp execution path
  passes a stable key derived from the pending action, so duplicate PIN
  submissions collapse to one debit.
- **Payout failure modes**: the debit is flagged for reconciliation atomically
  before the provider call, so a crash mid-payout leaves a discoverable PENDING
  row rather than an orphan; ambiguous outcomes stay PENDING rather than being
  auto-settled or auto-refunded.
- **Freeze**: checked on every inbound message AND re-checked inside Flow
  execution, closing the window where an action armed before a freeze could
  still run.
- **Tier ladder**: `recompute_tier()` is the single source of truth, so the
  chat-onboarding email gate holds even when BVN/NIN provisioning is driven from
  WhatsApp.
- **The AI layer cannot move money**: intents route into the same deterministic,
  PIN-gated flows; a mis-parsed or adversarial response can at worst pick the
  wrong flow.
- **App transaction PIN storage**: OS keychain only
  (`WHEN_UNLOCKED_THIS_DEVICE_ONLY`, excluded from backups), never
  `AsyncStorage`, cleared on sign-out and on disabling biometric payments.
- **No `console.*` anywhere** in `app/`, `lib/` or `components/` — no PIN, token
  or PII leaking into device logs or a crash reporter.
- **API contract**: all 67 API paths referenced by the app resolve to real
  backend routes; no dead or misspelled endpoints.
