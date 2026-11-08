"""Fixed Save lifecycle: lock funds, pay out at maturity.

Locking debits the wallet; maturity payout credits principal + interest. Both
go through the wallet ledger atomically. Payout is idempotent per plan.
"""
from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction as db_transaction
from django.utils import timezone

from common.http import spend_limit_error
from common.products import require_product
from wallet.models import Transaction, Wallet
from wallet.services import (DuplicateTransaction, InsufficientFunds,
                             LimitExceeded, assert_customer_spending_available,
                             bankless_provider, credit,
                             customer_spendable_balance, make_reference,
                             with_idempotency_fingerprint)

from .models import FixedSave


#: The ledger label for a lock. One constant because it is used twice: to pick the
#: caps that are CHECKED, and on the row that is WRITTEN. Two literals could drift
#: apart, and then the bucket a spend counts toward would stop being the bucket it
#: was measured against — the same reasoning as `common.http.daily_kind_for`.
LOCK_SERVICE = "Fixed Save locked"


@db_transaction.atomic
def lock(user, principal, days: int, idempotency_key: str = "") -> FixedSave:
    """Debit the wallet and create an active Fixed Save plan.

    With an `idempotency_key`, a duplicate request raises DuplicateTransaction
    (nothing locked), so a retry/race can't lock twice or create two plans.

    Locking is a customer-initiated debit, so it answers to the same gate as every
    other one — identity floor, account standing, available (not merely stored)
    balance, tier ceiling, velocity brake — by running the same checks
    `wallet.services.debit` runs, in the same order, under the same row lock.
    Raises LimitExceeded when a cap or standing check refuses the spend.

    Account standing is the one check that depends on a partner bank, so with no
    bank configured it is skipped rather than failed: there is no custodian to
    report a freeze, a PND or an unconfirmed balance. Everything else still
    applies, and the available-balance check still does its job, because without
    a bank the available figure is the local ledger's own spendable balance.

    This is not belt-and-braces. Before it, savings was a hole in the gate: an
    account frozen, PND'd, under balance review, or short of its identity floor
    could still move its whole wallet into a 365-day plan, and the money came back
    out at maturity as a clean credit. Every other debit path refused that account.
    """
    require_product("savings")
    principal = Decimal(str(principal))
    interest = FixedSave.quote(principal, days)
    rate = FixedSave.RATES.get(days, Decimal("0"))
    ref = make_reference("ZSAV")

    # Inside the lock, like `debit`: read outside it, the caps and the velocity
    # brake are advisory, because two concurrent requests both read the same
    # "spent today" and both pass.
    wallet = Wallet.objects.select_for_update().get(user=user)
    if not bankless_provider():
        # Account standing is the bank's judgement. With no bank there is nobody
        # to ask, and asking anyway made locking savings impossible.
        assert_customer_spending_available(user)
    if wallet.balance < principal:
        raise InsufficientFunds("Insufficient wallet balance")
    if customer_spendable_balance(user, wallet=wallet) < principal:
        raise InsufficientFunds("Insufficient available balance")
    breach = spend_limit_error(user, principal, LOCK_SERVICE)
    if breach:
        raise LimitExceeded(breach)
    wallet.balance -= principal
    wallet.save(update_fields=["balance", "updated"])

    try:
        with db_transaction.atomic():  # savepoint: contain the unique violation
            Transaction.objects.create(
                user=user, service=LOCK_SERVICE, amount=principal,
                direction=Transaction.OUT, transaction_status=Transaction.SUCCESS,
                reference=ref, meta=with_idempotency_fingerprint(
                    {"days": days, "rate": str(rate)}, idempotency_key),
                idempotency_key=idempotency_key,
            )
    except IntegrityError:
        if idempotency_key:
            raise DuplicateTransaction(idempotency_key)
        raise
    return FixedSave.objects.create(
        user=user, principal=principal, interest=interest, rate=rate,
        duration_days=days, reference=ref,
        matures_at=timezone.now() + timedelta(days=days),
    )


@db_transaction.atomic
def pay_out(plan: FixedSave) -> FixedSave | None:
    """Credit principal + interest to the wallet, exactly once.

    Locks the plan row and guards on `paid_out` so a duplicate maturity run
    (e.g. the cron job overlapping) can't pay twice. Returns the plan if this
    call performed the payout, else None.
    """
    require_product("savings")
    plan = FixedSave.objects.select_for_update().get(pk=plan.pk)
    if plan.paid_out:
        return None
    credit(plan.user, plan.maturity_value, "Fixed Save matured",
           meta={"plan": plan.reference}, reference=f"{plan.reference}-M")
    plan.status = FixedSave.MATURED
    plan.paid_out = True
    plan.save(update_fields=["status", "paid_out", "updated"])
    return plan


def run_maturities() -> int:
    """Pay out every active plan whose maturity date has passed. Returns count."""
    due = FixedSave.objects.filter(status=FixedSave.ACTIVE, paid_out=False, matures_at__lte=timezone.now())
    n = 0
    for plan in due:
        if pay_out(plan):
            n += 1
    return n


def settle_user_maturities(user) -> int:
    """Pay out this user's matured plans (idempotent). Returns count.

    Lets maturities settle the moment a user opens their savings, so payouts
    don't depend on a cron sweep — useful on Render's free tier, which has no
    cron service. `run_maturities` still sweeps everyone (e.g. users who never
    open the app) when run on a paid plan.
    """
    due = user.savings.filter(status=FixedSave.ACTIVE, paid_out=False, matures_at__lte=timezone.now())
    return sum(1 for plan in due if pay_out(plan))
