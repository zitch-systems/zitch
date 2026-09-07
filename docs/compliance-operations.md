# Compliance operations: AML cases, data-subject requests, disputes

The policy pack in `compliance/` was written for a regulator and makes dated,
specific commitments. Until now none of them had code behind it. This is that half,
plus an honest account of where the code and the wording differ.

| Commitment (from `WHATSAPP_PAYMENTS_COMPLIANCE_QA_v1.0.md`) | Implementation |
|---|---|
| "SARs filed with NFIU within 30 days of detection" | `AmlCase` stamps `detected` once and derives `due`; `aml_scan` pages and goes red on any open case past it |
| "threshold breaches (₦5M+), rapid velocity, structuring" | three rules in `compliance.services.scan_transactions`, run by the `zitch-aml-scan` cron |
| "records… retained for 5+ years; audit trail immutable" | erasure pseudonymises and explicitly retains the ledger, audit log and cases |
| "only a verification hash is retained" (BVN) | the export returns the hash's *existence*, never its value |

---

## 1. AML monitoring

`python manage.py aml_scan [--hours N] [--fail-on-overdue]`, daily at 06:00 Lagos.

A cron, not a hook inside `debit()`. Monitoring that runs under the wallet lock either
blocks money on a heuristic or has to be made non-blocking anyway, and neither belongs
in a money path. It is read-only over the ledger; the only writes are cases.

Three rules, each recording which one raised the case:

| Rule | Fires on | Window |
|---|---|---|
| `threshold` | one outbound movement ≥ `AML_THRESHOLD_NGN` (₦5,000,000) | the scan window |
| `structuring` | ≥3 movements each in ₦2.5m–₦5m, together ≥ ₦5m | **7 days** (`AML_STRUCTURING_WINDOW_HOURS`) |
| `velocity` | ≥40 outbound movements by one customer (`AML_VELOCITY_MIN_ROWS_24H`) | 24 hours |

Cases dedupe (`dedupe_key`), because a cron with an overlapping window would otherwise
raise the same finding every run and a real case would drown in copies of itself.

Closing a case requires a narrative, and filing additionally requires the NFIU/goAML
reference. A dismissal with no reason is indistinguishable from an unworked case, and
"we filed" with no reference is an assertion with nothing behind it.

### Two things the implementation had to confront

**The ₦5M threshold is at the ceiling of what anyone can move.** `User.TIER_LIMITS[3]`
and `DAILY_TRANSFER_LIMITS[3]` are both ₦5,000,000 — exactly the AML threshold. So:

* the `threshold` rule can only ever fire on a transfer of *precisely* the Tier-3
  maximum (the comparison is `>=`), and never for a lower tier;
* a 24-hour `structuring` window would be **mathematically unreachable**, since rows in
  the ₦2.5m–₦5m band cannot sum past ₦5m inside a day that is itself capped at ₦5m.

Hence the 7-day structuring window — which is also the better description of the
behaviour, since splitting a movement across days is precisely how a daily cap is
evaded. A test (`test_a_24_hour_structuring_window_would_be_unreachable`) pins the
relationship so that "tidying" the window back to 24 hours fails and says why.

**This is worth a decision:** the thresholds inherited from the policy were written for
a different limit ladder. If ₦5M is the intended monitoring threshold, the tier caps
imply it will almost never fire; if the caps are right, the threshold should probably be
lower. That is a compliance judgement, not a code one, and `AML_THRESHOLD_NGN` is an env
var so it can be changed without a deploy.

### Where the code deliberately differs from the policy wording

The policy says a suspicious transaction is **"blocked pending investigation"**. The
code **flags and does not block**.

Auto-blocking at ₦5M would refuse a transfer of exactly the Tier-3 limit — a legitimate
top-tier customer's largest allowed transfer — every time, and the monitoring runs after
the fact anyway, so the block would land on the next transaction rather than the flagged
one. Pinned by `test_monitoring_does_not_block_the_transaction` so the divergence cannot
be discovered by accident during an examination.

Whether to add a hold is a product and compliance decision. The mechanism to act on a
case already exists (the user-freeze ops action); what does not exist is an automatic
link from case to freeze, and that absence is deliberate.

---

## 2. Data-subject requests (NDPR/GDPR)

```
python manage.py data_subject_request --user <id|email|phone> --export [--out FILE]
python manage.py data_subject_request --user <id|email|phone> --erase --confirm
```

**Why a command and not an endpoint.** An export is the complete personal record of one
customer in a single file — the highest-value object this system can produce. Behind a
bearer token, one stolen operator session exfiltrates any customer's full history in one
request. A command needs shell access to production, which is a far smaller and better
audited set of people.

An ambiguous identifier is **refused**, never resolved to whichever row sorts first:
exporting or erasing the wrong customer's data is not a recoverable mistake.

### Erasure is pseudonymisation, and that is the point

Erasure and retention pull in opposite directions. The policy commits to keeping
transaction records for 5+ years with an immutable audit trail; the data-protection
right is to erasure of personal data. Both are satisfied by removing the *identifiers*
and keeping the financial record attached to a pseudonymous id.

| Cleared | Retained |
|---|---|
| name, email, phone, address, avatar | ledger transactions (5+ year commitment) |
| BVN/NIN last-4 **and their hashes** | audit log (immutable by design) |
| transaction PIN, sessions, WhatsApp links, linked banks, operator TOTP | AML cases (regulatory record) |

The verification **hashes** go too. They are irreversible but they are *stable
identifiers* — the same BVN always produces the same hash — so leaving them would let
anyone who can hash a BVN re-identify the "erased" account.

Sessions and channel links cannot survive: a live token or an active WhatsApp link would
keep the account reachable as if nothing had happened. The account is deactivated.

Every erasure records exactly which fields were cleared and which were retained, with
the reason, on the `DataSubjectRequest` row. That record is what an examiner asks for.

---

## 3. Disputes

Customer-facing, and the only compliance surface exposed over HTTP:

| Endpoint | |
|---|---|
| `POST /api/disputes/open/` | `{reference, reason, detail?}` |
| `POST /api/disputes/` | the customer's own disputes |
| `POST /api/disputes/withdraw/` | `{dispute_id}` |

AML cases are deliberately **invisible to the subject** — telling someone they are under
investigation is tipping off.

**A dispute moves no money.** The remedy is an existing audited path (a refund, or a
manual credit which above the ceiling now needs a second approver). Wiring an automatic
refund to a customer-submitted form would be a free-money button.

A reference that does not belong to the caller is refused with **the same message** as
one that does not exist. Different messages would make the endpoint a lookup oracle over
every customer's transaction ids.

Opening is idempotent per (customer, reference) while a dispute is open, so a frustrated
double-tap does not create two cases that get worked separately and resolved
inconsistently. Withdrawing frees the reference, so a customer who withdrew by mistake
can re-raise.

---

## 4. Working the queues

All three models are registered in Django admin at `/admin/`, with the **evidence
read-only** and the decision fields editable — a case whose evidence can be edited
before it is dismissed proves nothing. Rows cannot be added by hand (a hand-made case
would carry a deadline of whenever someone created it, not of detection) or deleted.

The portal SPA has no screens for any of this yet. That is the main gap: `/admin/` is
usable but it is not an ops console, and an AML queue nobody looks at is the same as no
queue. The `zitch-aml-scan` cron going red on an overdue case is the compensating
control until then.

## 5. Still missing

* **PEP and sanctions screening.** The policy names watchlist screening at onboarding and
  periodically. There is no screening provider wired, and building one against a list we
  do not have would be pretend coverage. Not implemented, not simulated.
* **"Layering"** in the policy's monitoring list is not implemented as a distinct rule —
  the structuring rule covers its simplest form.
* **No portal UI**, as above.
* **No automatic case → freeze link**, as above.
