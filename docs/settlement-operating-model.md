# Settlement operating model

The 25 July audit's second P0 was *"prove ledger-to-bank settlement … establish an
operational settlement model, daily bank-vs-ledger reports, ownership, and alert
thresholds."* The reports and thresholds are now code (`manage.py
settlement_report`, run daily at 06:15 UTC by the scheduler cron, `run_schedule`). Ownership and the response
procedure are people, and they are written down here.

---

## 1. Why per-wallet reconciliation was not enough

Three checks now run, in widening scope:

| Command | Compares | Blind to |
|---|---|---|
| `integrity_check` | a wallet's stored balance vs its own ledger | anything outside our own database |
| `reconcile_balances` | a wallet's ledger vs that wallet's NUBAN at ALAT | any pot that belongs to no single user |
| `settlement_report` | **everything owed vs everything held, on every rail** | — |

The gap the third one closes is not precision, it is *category*. Customer money does
not all sit in customer NUBANs — it also sits in the **pool account**
(`WEMA_SOURCE_ACCOUNT`), which belongs to no single user and which no per-wallet
check has any reason to read. The pool funds customers who have no NUBAN yet,
absorbs their VAS purchases, and is where the business tops itself up. A pool that
has quietly drained is invisible to both narrower checks, and is exactly the kind of
thing that becomes a customer-facing outage with money apparently on hand.

> **This model used to carry a third rail, and it is worth knowing why it is gone.**
> VAS ran on an external provider we pre-funded. A ₦1,000 airtime purchase dropped
> the ledger liability by ₦1,000 while the customer's NUBAN *kept* its ₦1,000 and the
> provider's float — a pot at another company — fell instead. Every per-wallet check
> stayed content (`reconcile_balances` reads bank-ahead-of-ledger as the benign
> direction), so nothing anywhere recorded that ₦1,000 had to be swept from the bank
> to the provider. That standing sweep obligation had to be read and held here, and
> it generated the one recurring manual action this document existed to produce.
> Since VAS moved to the partner bank it debits the **buyer's own NUBAN**, so held
> and owed now fall together: the position is unchanged by VAS volume, there is no
> third pot to read, and **there is no sweep**. One fewer rail that can be
> unreadable, and one fewer way for this report to be wrong.

## 2. The identity

```
held  =  Σ customer NUBAN balances  +  pool account (WEMA_SOURCE_ACCOUNT)
owed  =  ledger liability  =  Σ(IN, Successful)  −  Σ(OUT, Pending|Successful)

position = held − owed
```

* **`position < 0` — SHORTFALL.** We owe customers more than we hold anywhere.
  Pages at `error` on every occurrence. There is no benign reading of this.
* **`position > 0` — surplus.** Expected: unswept margin and float. It no longer
  grows with VAS volume (that was the retired rail's sweep backlog). Printed every
  run so the number is watched. Pages only above `--max-surplus`, because an abnormal
  surplus is also a bug — most likely debits that never reached a biller.

An unreadable rail (a NUBAN or the pool balance read fails) does **not**
count as zero. It is reported as `UNREADABLE`, the position is marked
`incomplete`, and a `warning` alert fires. A position computed over a rail nobody
could read is worse than no position, because it looks authoritative.

## 3. The daily procedure

Runs 06:00 UTC / 07:00 Lagos, before the business day.

1. Read the run output (Render cron log) or the `recon.settlement_report` AuditLog
   row, which carries the full snapshot.
2. Read `vas_out`. There is nothing to sweep — partner-bank VAS leaves the buyer's
   own NUBAN — but a `vas_out` that keeps climbing while `position` does not move is
   the signature of debits that never reached a biller, which is what the surplus
   ceiling exists to catch.
3. Check `position` against the previous day. A step change with no matching
   movement in `funded_in` / `bank_payouts_out` / `vas_out` is the signal to
   investigate.
4. Any `incomplete: true` run is not evidence of anything. Fix the unreadable rail
   and re-run before drawing a conclusion.

### Balances with no Wema account behind them

Every naira a live customer holds sits in their own Wema account. A wallet with
a balance and no account is money no bank holds: all of it is shortfall, and no
reconciliation run closes it. The report pages it as "customer balances with no
Wema account behind them", and the SHORTFALL alert says how much of its gap those
wallets explain (`from_wallets_without_wema_account`) and how much they do not
(`unexplained`). Only an `unexplained` amount above zero means money is missing from
accounts that exist.

New ones cannot be created: on a live deploy every ledger credit to a wallet without
a real Wema account is refused (`wallet.services.assert_backed_credit`). That covers
operator manual credits, loans, savings maturities (held until the account exists),
airtime-to-cash (checked before the airtime is taken) and conversions into NGN.

Existing ones are listed with where each balance came from and what resolves it:

    python manage.py unbacked_balances

* `simulated` only: fake money from a simulation deploy.
  `python manage.py purge_simulation_data --confirm` reverses it.
* `retired` only: money paid to a retired provider. Use `archive_retired_balances`
  (see `docs/retired-balance-archive.md`).
* Anything else is real customer money. The customer opens their Wema account
  (WhatsApp reply 6, or the app). The operator is then emailed at once with the
  exact next step: `prepare_settlement_funding --user-id <id> --apply
  --operator-reference '<who approved>'`, and Zitch makes the one transfer it names.
  It is matched to the account, not credited twice.

The alerts stop when `unbacked_balances` reports none.

### When the customer's old account still holds the money

A wallet whose earlier Wema account was detached (for example on 30 Oct 2026)
lists as `other` but its deposits are real `WEMA-CR-` credits, and the money
may still sit in that account. Find it before paying anything in: the detached
number is in the AuditLog (`retired_provider_row_archived`, target
`banking_previousbankarchive:*`, `after.wallet_id`). Compare that account's
statement with the ledger, then correct each difference with a reviewed command
(dry run first, `--apply` to post; each posts once and the customer is alerted):

* Transfer fees the bank took: `post_transfer_fee_correction`.
* Other bank charges (SMS alerts):
  `post_statement_correction --kind bank-charges`.
* A deposit the bank reversed or never settled:
  `post_statement_correction --kind reversed-deposit --original-reference <WEMA-CR-…>`.
* A purchase marked Failed that the bank charged and the customer received:
  `post_statement_correction --kind charged-failed-purchase --original-reference <ZTCH…>`.
  If the customer did not receive it, Zitch owes them: pay it into their account.

Nothing here credits a wallet. When the ledger and the old account agree, the
customer replies 6; the bank reports the existing account, and Zitch attaches it
(holder name must match) with an opening review. `prepare_settlement_funding` is
only for money that is not already at the bank.

## 4. Thresholds

| Flag | Launch value | Why |
|---|---|---|
| `--max-shortfall` | `0.00` (default) | Any shortfall pages. Raise only with a written reason recorded in this file. |
| `--max-surplus` | unset at launch | The normal float and margin are unknown until real volume exists. Set it after two weeks of live data, then tighten. |
| `--fail-on-breach` | on (in `render.yaml`) | Makes the Render cron itself go red, so a breach surfaces even before `SENTRY_DSN` is wired. |

`--max-surplus` being unset means surplus never pages. That is the correct launch
posture — a ceiling guessed before there is volume produces daily false alarms and
trains everyone to ignore the cron — but it is a **deliberate gap, not coverage**.
It also matters more now than it did: with the sweep gone, the surplus ceiling is
the only thing watching the "debited but never delivered" direction.
Until it is set, only the shortfall direction is alarmed.

## 5. Ownership

| Role | Responsibility | Who |
|---|---|---|
| Settlement owner | Reads the daily statement and sets `--max-surplus` once volume is known | **unassigned — assign before go-live** |
| Escalation for a shortfall | Owns the investigation; authority to disable the affected rail (`WEMA_SIMULATION=true`, or blank the VAS credentials) | **unassigned — assign before go-live** |
| MLRO | Notified of any shortfall that could indicate fraud rather than a settlement lag | see `compliance/ZITCH_MLRO_APPOINTMENT_LETTER_v1.0.docx` |

Both unassigned rows are a launch blocker in the same sense the missing Wema
callback profiling is: the code is ready and the human process is not. A paging
alert with no named owner is not monitoring.

## 6. What this still does not prove

* **Provider-side records.** The position compares our ledger against *balances*.
  It does not reconcile our VAS transaction list or our payout list against ALAT's
  own statement. Divergence in individual records can hide inside a
  matching total.
* **Card and FX rails.** Neither has a readable float balance wired in, so both are
  outside `held`. While card issuance is gated off (`WEMA_CARD_KEY` unset) and FX
  volume is nil this does not distort the position; both must be added to `_held()`
  before either rail carries real money.
* **Timing.** Balances are read at one instant while movement accrues
  continuously, so a position taken mid-payout can be transiently negative by the
  size of an in-flight transfer. This is why the daily run is scheduled before the
  business day rather than during it.
