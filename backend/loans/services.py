"""Loan lifecycle: eligibility, disbursement, repayment.

Disbursement credits the wallet; repayment debits it. Both go through the
wallet ledger so the balance and the loan state move together atomically.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction as db_transaction
from django.utils import timezone

from wallet.models import Wallet
from wallet.services import InsufficientFunds, credit, make_reference

from .models import Loan

User = get_user_model()


class LoanError(Exception):
    """Eligibility violated at disbursement time (raced past the view's checks)."""


def credit_limit(user) -> Decimal:
    """Available credit = limit minus outstanding on any active loan.

    A simple model to start; replace with a behaviour-based score later.
    """
    active = user.loans.filter(status=Loan.ACTIVE).first()
    if active:
        # Never report negative head-room (outstanding includes interest, so a
        # loan taken at the full limit leaves slightly negative arithmetic).
        return max(Decimal("0.00"), Loan.DEFAULT_LIMIT - active.outstanding)
    return Loan.DEFAULT_LIMIT


@db_transaction.atomic
def disburse(user, principal, tenure_days: int) -> Loan:
    """Create an active loan and credit the principal to the wallet.

    The view's eligibility checks (one-active-loan, credit limit) run outside any
    lock, so two concurrent requests could both pass them. Here we take a row
    lock on the user to serialise concurrent disbursements and RE-ASSERT
    eligibility inside the lock; a partial unique constraint on the Loan table
    (one active loan per user) is the final DB-level backstop.
    """
    principal = Decimal(str(principal))
    # Serialise concurrent loan_requests for this user on the user row.
    User.objects.select_for_update().get(pk=user.pk)
    if Loan.objects.filter(user=user, status=Loan.ACTIVE).exists():
        raise LoanError("You already have an active loan")
    if principal > credit_limit(user):
        raise LoanError("Amount exceeds your available credit")

    interest = Loan.quote(principal, tenure_days)
    ref = make_reference("ZLN")
    loan = Loan.objects.create(
        user=user,
        principal=principal,
        interest=interest,
        tenure_days=tenure_days,
        reference=ref,
        due_date=timezone.now() + timedelta(days=tenure_days),
    )
    credit(user, principal, "Loan disbursed", meta={"loan": ref}, reference=ref)
    return loan


@db_transaction.atomic
def repay(user, loan: Loan, amount, idempotency_key: str = "") -> Loan:
    """Debit the wallet toward a loan; mark repaid when fully settled.

    Locks the loan and wallet rows; raises InsufficientFunds if the balance is
    short. Over-payment is capped at the outstanding amount. With an
    `idempotency_key`, a retried repay (same user + key) raises
    DuplicateTransaction with nothing debited — without it, a lost-response
    retry would debit the wallet a second time (the reference is random per
    call, so it can't dedupe on its own).
    """
    from django.db import IntegrityError

    from wallet.models import Transaction
    from wallet.services import DuplicateTransaction

    amount = Decimal(str(amount))
    loan = Loan.objects.select_for_update().get(pk=loan.pk)
    if loan.status == Loan.REPAID:
        return loan

    pay = min(amount, loan.outstanding)
    wallet = Wallet.objects.select_for_update().get(user=user)
    if wallet.balance < pay:
        raise InsufficientFunds("Insufficient wallet balance")

    wallet.balance -= pay
    wallet.save(update_fields=["balance", "updated"])
    try:
        with db_transaction.atomic():  # savepoint: contain the unique violation
            Transaction.objects.create(
                user=user,
                service="Loan repayment",
                amount=pay,
                direction=Transaction.OUT,
                transaction_status=Transaction.SUCCESS,
                reference=make_reference(f"{loan.reference}-R"),
                meta={"loan": loan.reference},
                idempotency_key=idempotency_key,
            )
    except IntegrityError:
        if idempotency_key:
            raise DuplicateTransaction(idempotency_key)
        raise

    loan.amount_repaid += pay
    if loan.outstanding <= Decimal("0.00"):
        loan.status = Loan.REPAID
    loan.save(update_fields=["amount_repaid", "status", "updated"])
    return loan
