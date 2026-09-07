# Settlement operating model

The 25 July audit's second P0 was *"prove ledger-to-bank settlement … establish an
operational settlement model, daily bank-vs-ledger reports, ownership, and alert
thresholds."* The reports and thresholds are now code (`manage.py
settlement_report`, the `zitch-settlement-report` cron). Ownership and the response
procedure are people, and they are written down here.

---

## 1. Why per-wallet reconciliation was not enough

Three checks now run, in widening scope:

| Command | Compares | Blind to |
|---|---|---|
| `integrity_check` | a wallet's stored balance vs its own ledger | anything outside our own database |
| `reconcile_balances` | a wallet's ledger vs that wallet's NUBAN at ALAT | any pot that belongs to no single user |
| `settlement_report` | **everything owed vs everything held, on every rail** | — |

The gap the third one closes is not precision, it is *category*. Customer money
does not all sit in customer NUBANs:

1. A customer funds ₦1,000. Their NUBAN holds ₦1,000; we owe ₦1,000. Balanced.
2. They buy ₦1,000 of airtime. We now owe ₦0 — and **VTU.ng debits our provider
   wallet by ₦1,000**. Their NUBAN still holds the original ₦1,000.

After step 2 every per-wallet check is content: `integrity_check` sees stored ==
ledger, and `reconcile_balances` sees the bank ahead of the ledger, which it
classifies as the benign direction (an unswept deposit) and deliberately does not
page on. Nothing anywhere records that ₦1,000 must be moved from that NUBAN to
VTU.ng. Repeat it a few thousand times and the VAS rail runs dry mid-morning while
the bank accounts are full — a customer-facing outage with the money sitting right
there.

## 2. The identity

```
held  =  Σ customer NUBAN balances  +  pool account (WEMA_SOURCE_ACCOUNT)
                                    +  VTU.ng provider wallet
owed  =  ledger liability  =  Σ(IN, Successful)  −  Σ(OUT, Pending|Successful)

position = held − owed
```

* **`position < 0` — SHORTFALL.** We owe customers more than we hold anywhere.
  Pages at `error` on every occurrence. There is no benign reading of this.
* **`position > 0` — surplus.** Expected, and it *grows with VAS volume*: it is the
  sweep backlog plus margin. Printed every run so the number is watched. Pages only
  above `--max-surplus`, because an abnormal surplus is also a bug — most likely
  debits that never reached a provider.

An unreadable rail (WEMA balance read fails, VTU credentials missing) does **not**
count as zero. It is reported as `UNREADABLE`, the position is marked
`incomplete`, and a `warning` alert fires. A position computed over a rail nobody
could read is worse than no position, because it looks authoritative.

## 3. The daily procedure

Runs 06:00 UTC / 07:00 Lagos, before the business day.

1. Read the run output (Render cron log) or the `recon.settlement_report` AuditLog
   row, which carries the full snapshot.
2. **`sweep_owed_to_provider`** is yesterday's VAS spend. That much must move from
   the bank to the VTU.ng wallet, or the rail eventually empties. Top up VTU.ng
   accordingly — this is the recurring operational action the model exists to
   produce.
3. Check `position` against the previous day. A step change with no matching
   movement in `funded_in` / `bank_payouts_out` / `vas_out` is the signal to
   investigate.
4. Any `incomplete: true` run is not evidence of anything. Fix the unreadable rail
   and re-run before drawing a conclusion.

## 4. Thresholds

| Flag | Launch value | Why |
|---|---|---|
| `--max-shortfall` | `0.00` (default) | Any shortfall pages. Raise only with a written reason recorded in this file. |
| `--max-surplus` | unset at launch | The normal sweep backlog is unknown until real volume exists. Set it after two weeks of live data to roughly 2× the observed daily VAS spend, then tighten. |
| `--fail-on-breach` | on (in `render.yaml`) | Makes the Render cron itself go red, so a breach surfaces even before `SENTRY_DSN` is wired. |

`--max-surplus` being unset means surplus never pages. That is the correct launch
posture — a ceiling guessed before there is volume produces daily false alarms and
trains everyone to ignore the cron — but it is a **deliberate gap, not coverage**.
Until it is set, only the shortfall direction is alarmed.

## 5. Ownership

| Role | Responsibility | Who |
|---|---|---|
| Settlement owner | Reads the daily statement, performs the VTU.ng sweep, sets `--max-surplus` once volume is known | **unassigned — assign before go-live** |
| Escalation for a shortfall | Owns the investigation; authority to disable the affected rail (`WEMA_SIMULATION=true`, or blank the VAS credentials) | **unassigned — assign before go-live** |
| MLRO | Notified of any shortfall that could indicate fraud rather than a settlement lag | see `compliance/ZITCH_MLRO_APPOINTMENT_LETTER_v1.0.docx` |

Both unassigned rows are a launch blocker in the same sense the missing Wema
callback profiling is: the code is ready and the human process is not. A paging
alert with no named owner is not monitoring.

## 6. What this still does not prove

* **Provider-side records.** The position compares our ledger against *balances*.
  It does not reconcile our VAS transaction list against VTU.ng's own statement, or
  our payout list against ALAT's. Divergence in individual records can hide inside a
  matching total.
* **Card and FX rails.** Neither has a readable float balance wired in, so both are
  outside `held`. While card issuance is gated off (`WEMA_CARD_KEY` unset) and FX
  volume is nil this does not distort the position; both must be added to `_held()`
  before either rail carries real money.
* **Timing.** Balances are read at one instant while movement accrues
  continuously, so a position taken mid-payout can be transiently negative by the
  size of an in-flight transfer. This is why the daily run is scheduled before the
  business day rather than during it.
