# Operator controls: second factor and dual approval

Two controls `docs/hardening/GAP_ANALYSIS.md` deferred on the grounds that multi-admin
workflows matter once there is a real ops team. That reasoning is right about most of
the portal and wrong about one endpoint, which is why they exist now.

Both are **off by default**. Enabling either is a deliberate act, for reasons given
under each.

---

## 1. Operator MFA (TOTP)

Scoped to operators, not customers. An operator session can credit wallets, decide KYC
and change runtime settings, so a stolen operator password is worth far more than a
customer's — and customers already have a second factor on the thing that matters (the
transaction PIN on every money movement) rather than on sign-in.

RFC 6238, SHA-1, 6 digits, 30-second step — the interoperable defaults that Google
Authenticator, Authy and 1Password all handle. Implemented on the standard library
(`accounts/totp.py`); `django-otp` is not a dependency and adding one for ~40 lines of
HMAC would be a poor trade.

### Enrolling

| Call | Does |
|---|---|
| `POST /api/ops/mfa/` (or `/api/admin/mfa/status`) | whether you are enrolled, and whether your role requires it |
| `POST /api/ops/mfa-enroll/` | issues a secret + `otpauth://` URI **once** |
| `POST /api/ops/mfa-confirm/` `{code}` | proves it was stored, turns it on |
| `POST /api/ops/mfa-disable/` `{code}` | turns it off — a current code required |

The secret is returned once and never re-displayed. An endpoint that could re-read it
would turn any authenticated session into a permanent bypass of the factor. Replacing
a *confirmed* secret likewise needs a current code from the existing authenticator,
because otherwise a hijacked session could swap the factor for one the attacker
controls.

Enrolment is two-step, and an **unconfirmed** row does not gate login. A half-finished
enrolment must not lock an operator out of the portal.

### At login

Both operator login forms enforce it — `/api/ops/login/` and `/api/admin/login` — via
one shared helper. A gate on one form and not the other is no gate.

* Enrolled and no code → `401` with `code: "mfa_required"`, so the client prompts for a
  code instead of showing "wrong password" for a correct one.
* Wrong or reused code → `401 mfa_invalid`. A code is single-use: the consumed step is
  persisted, so a code read over someone's shoulder cannot be replayed inside its own
  30-second window. That is what makes the ±1 step clock-drift tolerance safe.
* The check runs **after** the password and staff gates, so a wrong password and a
  missing code look identical from outside — otherwise the endpoint would confirm which
  identifiers are real operator accounts.

### `OPS_REQUIRE_MFA`

Default `false`. With it on, operators in **money- or settings-capable roles**
(`super_admin`, `finance`) cannot sign in without an enrolled factor; `support` and
`read_only` are unaffected, because a read-only account can move nothing and forcing
enrolment on it buys nothing.

It is off by default because switching it on before anyone has enrolled locks out every
operator at once, **including whoever would have to fix it**. The order is: enrol, then
flip the flag.

An operator who has enrolled is always challenged regardless of the flag.

---

## 2. Maker/checker on manual wallet credits

A manual wallet credit is the one operator action that **creates money from nothing**.
Every control downstream — tier caps, the velocity brake, the ledger — treats the
resulting balance as legitimate, because as far as the ledger is concerned it is. So a
single compromised or dishonest operator account can mint a balance and withdraw it.

The existing guardrails bound that (a per-credit ceiling `ADMIN_MAX_MANUAL_CREDIT`,
default ₦500,000, and a per-operator rolling-24h cap), and the over-ceiling error
message has always said *"a larger credit needs a second approver"* — while no second
approver existed. This implements what that message promised.

### The flow

1. An operator requests a credit **above** `ADMIN_MAX_MANUAL_CREDIT`.
2. With `OPS_REQUIRE_DUAL_APPROVAL` on, nothing is credited: an `ApprovalRequest` is
   stored and the response says so (`pending_approval: true`).
3. A **different** operator with the `money` capability approves it via
   `POST /api/ops/approvals-decide/` `{id, approve, note}` — or from Django admin.
4. Only then does the credit run, through the **same money core** the direct path uses
   (`_perform_manual_credit`), so it takes exactly the code path it would have taken.

Four refusals carry the weight:

* **Self-approval** is refused in the service, not the view, so no endpoint can forget
  it. A maker/checker control the maker can self-check is an extra click.
* **A non-pending request** cannot be decided again — deciding twice would credit twice.
* The decision re-reads the row **under a lock**, so two approvers clicking at once
  cannot both execute.
* A **failing execution** is recorded on the request as `failed` with the reason, not
  swallowed. Execution happens outside the lock, so a crash leaves the request
  approved-not-executed: visible and re-runnable rather than lost.

The per-operator rolling-24h cap is **not** waived on the approved path. Only the
per-credit ceiling is, because that ceiling's documented remedy *is* a second approver.
Two colluding operators is a different threat from one, and an unbounded approved path
would become the weakest link.

### `OPS_REQUIRE_DUAL_APPROVAL`

Default `false`, and this one matters: **the portal SPA has no approval screen yet.**
With the flag on, the queue is reachable by API (`/api/ops/approvals/`,
`/api/admin/approvals/list`) and from Django admin at `/admin/` — where the model is
registered with the action and payload read-only, so a checker cannot rewrite what the
maker submitted before approving it.

Turning the flag on without a way to drain the queue would silently break a working
workflow: every over-ceiling credit would return 202 and sit there. Enable it once
somebody is actually watching the queue.

With the flag off, an over-ceiling credit is refused exactly as it is today.

---

## What is not covered

* **Only `wallet.credit` is gated.** The other high-risk ops actions — KYC decisions,
  card freezes, runtime settings, the AI kill switch — are single-operator. They are
  audited and reversible; a minted balance that has been withdrawn is not. Adding one is
  a `@approvals.register` executor plus a branch in the endpoint.
* **No portal UI**, as above.
* **No recovery codes.** An operator who loses their authenticator needs a super admin
  to clear their `OperatorTotp` row (from `/admin/`). Recovery codes are the usual
  answer and are a genuine gap — they are also a second credential to store and leak,
  so the deliberate interim answer is a human with database access.
