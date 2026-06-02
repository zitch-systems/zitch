"""Fixed Save lifecycle: lock funds, pay out at maturity.

Locking debits the wallet; maturity payout credits principal + interest. Both
go through the wallet ledger atomically. Payout is idempotent per plan.
"""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction as db_transaction
from django.utils import timezone

from wallet.models import Transaction, Wallet
from wallet.services import InsufficientFunds, credit, make_reference

from .models import FixedSave


@db_transaction.atomic
def lock(user, principal, days: int) -> FixedSave:
    """Debit the wallet and create an active Fixed Save plan."""
    principal = Decimal(str(principal))
    interest = FixedSave.quote(principal, days)
    rate = FixedSave.RATES.get(days, Decimal("0"))
    ref = make_reference("ZSAV")

    wallet = Wallet.objects.select_for_update().get(user=user)
    if wallet.balance < principal:
        raise InsufficientFunds("Insufficient wallet balance")
    wallet.balance -= principal
    wallet.save(update_fields=["balance", "updated"])

    Transaction.objects.create(
        user=user, service="Fixed Save locked", amount=principal,
        direction=Transaction.OUT, transaction_status=Transaction.SUCCESS,
        reference=ref, meta={"days": days, "rate": str(rate)},
    )
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
