"""Find and neutralise the test data a simulation deploy leaves behind.

`WEMA_SIMULATION=true` exists so the whole app can be walked end to end without
real money. What it does NOT do is mark the results as fake in any way a live
deploy would notice: `simulate_deposit` credits through `settle_reserved_funding`,
the same path a genuine reconciled deposit takes, so the row it writes is an
ordinary ledger credit. `integrity_check` therefore cannot see it — balance and
ledger agree perfectly, because the credit really is in the ledger. It is only
the MONEY behind it that never existed.

That matters the moment live keys are set, because the fake balance is still
spendable:

  * Bank payouts are already refused for a wallet carrying a mock NUBAN
    (transfers.services, via wallet.services.is_demo_account) — but only if the
    wallet still HAS that NUBAN. A test user with a simulated balance and no
    account number falls through to WEMA_SOURCE_ACCOUNT and debits the pool.
  * VTU (airtime/data/bills) has no such guard at all, and is a separate live
    rail with its own float. Simulated naira converts straight into real airtime.

So this runs before go-live, and the two markers it keys on are the only durable
traces simulation leaves:

  * `WEMA-CR-SIM-` — the reference prefix `apply_simulated_deposit` mints.
  * `(demo)` in `Wallet.bank_name` — stamped by `utility.wema._mock_account`.

The fake credit is REVERSED, not deleted. Deleting is not available: migration
`0017_ledger_database_immutability` installs a PostgreSQL trigger that raises on
any DELETE against `wallet_transaction`, so a delete would pass the SQLite test
suite and fail in production. Appending a compensating debit is also the more
honest record — the history shows a simulated credit and its withdrawal, rather
than a gap. The wallet balance is then recomputed from the ledger, which is what
keeps `integrity_check` green immediately afterwards.

The account itself is neutralised, never deleted. The money models bind to the
user with PROTECT precisely so no single delete can erase ledger history (see
wallet.models), and a customer is frozen rather than removed. A test account is
treated the same way: the mock NUBAN is cleared so a real one can be issued, the
simulated identity is withdrawn so no tier limit rides on it, and the account is
frozen. All of it is reversible from the admin; a delete would not be.

A user with any NON-simulated ledger row is never touched. That is not caution
for its own sake — it is the case that actually needs a human: a test account
that spent fake naira on real airtime has real OUT rows, and reversing the credit
that funded them would drive the recomputed balance negative, which the wallet's
own check constraint refuses. Those are reported for manual handling instead.

Dry-run by default; `--confirm` writes. Re-running is safe: a credit that already
carries its reversal is not reversed twice. `--fail-nonzero` exits 1 while any
simulation data is still present, which makes it usable as a go-live gate
alongside `wema_preflight`.
"""
from django.core.management.base import BaseCommand
from django.db import transaction as db_transaction

from wallet.models import Transaction, Wallet
from wallet.services import DEMO_ACCOUNT_MARKER, is_demo_account, wallet_expected_balance

# The reference prefix wallet.views.apply_simulated_deposit mints, and the prefix this
# command stamps on the debit that withdraws one. Both live here so the "is this row
# simulated?" question has a single answer, and so the reversal is never mistaken for
# the real ledger activity that disqualifies an account from being purged.
SIM_CREDIT_PREFIX = "WEMA-CR-SIM-"
SIM_REVERSAL_PREFIX = "SIMREV-"

# Fields simulate_kyc sets to grant tier limits with no identity behind them. Note
# what is NOT here: email_verified and phone_verified. Those were plausibly earned by
# a real OTP round-trip at signup, and withdrawing them would lock the operator out
# of their own test account for no safety gain — the limits ride on the tier.
_SIMULATED_IDENTITY = {
    "bvn_hash": "", "bvn_last4": "", "nin_hash": "", "nin_last4": "",
    "bvn_verified": False, "nin_verified": False, "face_verified": False,
    "address_verified": False, "id_document_verified": False, "tier": 0,
}


class Command(BaseCommand):
    help = ("Report (and with --confirm, neutralise) the wallets, balances and KYC "
            "flags left behind by a WEMA_SIMULATION deploy.")

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm", action="store_true",
            help="Actually write. Without it this is a read-only report.")
        parser.add_argument(
            "--keep-active", action="store_true",
            help="Do not freeze (is_active=False) the accounts that are purged. Use "
                 "when you intend to keep testing with them on the live deploy.")
        parser.add_argument(
            "--fail-nonzero", action="store_true",
            help="Exit 1 while any simulation data remains — for a go-live gate.")

    def handle(self, *args, **options):
        purgeable, review = self._classify()

        self._report("Safe to purge (no real ledger activity)", purgeable)
        self._report("NEEDS A HUMAN (has non-simulated ledger rows)", review)

        if not purgeable and not review:
            self.stdout.write(self.style.SUCCESS("No simulation data found."))
            return

        purged = 0
        if purgeable and options["confirm"]:
            for row in purgeable:
                self._purge(row, freeze=not options["keep_active"])
                purged += 1
            self.stdout.write(self.style.SUCCESS(
                f"Purged {purged} account(s)."
                + ("" if options["keep_active"] else " They are now frozen; unfreeze from "
                                                     "the admin if you still need them.")))
        elif purgeable:
            self.stdout.write(self.style.WARNING(
                f"DRY RUN — {len(purgeable)} account(s) would be purged. "
                f"Re-run with --confirm to apply."))

        if review:
            self.stdout.write(self.style.WARNING(
                f"{len(review)} account(s) left untouched — they hold real ledger rows "
                f"(most likely fake naira already spent on a live rail such as VTU). "
                f"Settle those by hand, then re-run."))

        from whatsapp.ops import record_audit
        record_audit("ops.purge_simulation_data", actor_type="system",
                     after={"confirmed": bool(options["confirm"]),
                            "purged": purged,
                            "purgeable": [r["user_id"] for r in purgeable],
                            "needs_review": [r["user_id"] for r in review]})

        remaining = len(review) + (0 if options["confirm"] else len(purgeable))
        if remaining and options["fail_nonzero"]:
            raise SystemExit(1)

    # ---- detection -------------------------------------------------------

    @staticmethod
    def _unreversed_sim_credits(user_id):
        """Simulated credits on this user that have not already been withdrawn.

        Re-running the command must not stack a second reversal onto a credit it
        already reversed — that would drive the balance negative on an account it
        had itself just cleaned.
        """
        credits = {t.reference: t for t in Transaction.objects.filter(
            user_id=user_id, reference__startswith=SIM_CREDIT_PREFIX)}
        already = set(Transaction.objects.filter(
            user_id=user_id, reference__startswith=SIM_REVERSAL_PREFIX)
            .values_list("reference", flat=True))
        return [t for ref, t in sorted(credits.items())
                if f"{SIM_REVERSAL_PREFIX}{ref}" not in already]

    def _classify(self):
        """Split every simulation-marked account into (safe to purge, needs review)."""
        user_ids = set(
            Transaction.objects.filter(reference__startswith=SIM_CREDIT_PREFIX)
            .values_list("user_id", flat=True))
        user_ids |= set(
            Wallet.objects.filter(bank_name__icontains=DEMO_ACCOUNT_MARKER)
            .values_list("user_id", flat=True))

        purgeable, review = [], []
        for wallet in (Wallet.objects.filter(user_id__in=sorted(user_ids))
                       .select_related("user").order_by("user_id")):
            outstanding = self._unreversed_sim_credits(wallet.user_id)
            demo = is_demo_account(wallet)
            if not outstanding and not demo:
                continue    # already cleaned by an earlier run
            # The reversals this command writes are excluded alongside the credits
            # they withdraw: they are its own footprint, not evidence of real use.
            real = (Transaction.objects.filter(user_id=wallet.user_id)
                    .exclude(reference__startswith=SIM_CREDIT_PREFIX)
                    .exclude(reference__startswith=SIM_REVERSAL_PREFIX))
            row = {
                "user_id": wallet.user_id,
                "wallet": wallet,
                "phone": wallet.user.phone or "",
                "balance": wallet.balance,
                "tier": wallet.user.tier,
                "outstanding": outstanding,
                "real_rows": real.count(),
                "demo_nuban": demo,
            }
            (review if row["real_rows"] else purgeable).append(row)
        return purgeable, review

    # ---- reporting -------------------------------------------------------

    def _report(self, heading, rows):
        if not rows:
            return
        from common.http import mask_pii

        self.stdout.write(f"\n{heading}: {len(rows)}")
        for r in rows:
            marks = []
            if r["demo_nuban"]:
                marks.append("mock NUBAN")
            if r["outstanding"]:
                marks.append(f"{len(r['outstanding'])} simulated credit(s)")
            if r["real_rows"]:
                marks.append(f"{r['real_rows']} REAL ledger row(s)")
            self.stdout.write(
                f"  user={r['user_id']} phone={mask_pii(r['phone'])} "
                f"balance={r['balance']} tier={r['tier']} — {', '.join(marks) or 'no markers'}")

    # ---- the write -------------------------------------------------------

    def _purge(self, row, *, freeze):
        wallet = row["wallet"]
        user = wallet.user
        with db_transaction.atomic():
            for credit in row["outstanding"]:
                Transaction.objects.create(
                    user_id=user.id, service="simulation-reversal",
                    amount=credit.amount, currency=credit.currency,
                    direction=Transaction.OUT,
                    transaction_status=Transaction.SUCCESS,
                    reference=f"{SIM_REVERSAL_PREFIX}{credit.reference}",
                    meta={"reverses": credit.reference,
                          "reason": "WEMA_SIMULATION test credit withdrawn before go-live"})

            fields = ["balance", "updated"]
            if is_demo_account(wallet):
                # The number was never minted at the bank, so nothing can be in flight
                # to it and clearing is safe. It has to go: provision refuses to
                # REPLACE an existing NUBAN, so leaving it would trap the wallet from
                # ever receiving a real one.
                wallet.account_number = ""
                wallet.account_name = ""
                wallet.bank_name = ""
                wallet.account_reference = ""
                wallet.bank_accounts = []
                fields += ["account_number", "account_name", "bank_name",
                           "account_reference", "bank_accounts"]
            # Recompute rather than assume zero: the ledger is the source of truth, and
            # this is what keeps integrity_check green immediately after the purge.
            wallet.balance = wallet_expected_balance(user.id)
            wallet.save(update_fields=fields)

            for field, value in _SIMULATED_IDENTITY.items():
                setattr(user, field, value)
            user_fields = list(_SIMULATED_IDENTITY)
            if freeze:
                user.is_active = False
                user_fields.append("is_active")
            user.save(update_fields=user_fields)
