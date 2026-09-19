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


class LoanRepaymentStale(LoanError):
    """The quoted loan closed before this repayment acquired its row lock.

    ``transaction_reference`` identifies the FAILED audit row which durably
    claims the request key without moving the wallet balance.
    """

    code = "loan_repayment_stale"

    def __init__(self, loan_reference: str, transaction_reference: str = ""):
        super().__init__(
            "This loan was repaid before this request could be applied. "
            "No money was taken."
        )
        self.loan_reference = loan_reference
        self.transaction_reference = transaction_reference


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
def disburse(user, principal, tenure_days: int, idempotency_key: str = "") -> Loan:
    """Create an active loan and credit the principal to the wallet.

    The view's eligibility checks (one-active-loan, credit limit) run outside any
    lock, so two concurrent requests could both pass them. Here we take a row
    lock on the user to serialise concurrent disbursements and RE-ASSERT
    eligibility inside the lock; a partial unique constraint on the Loan table
    (one active loan per user) is the final DB-level backstop.

    The one-active-loan constraint blocks a fast retry, but once a loan is repaid
    a stale retry of the same request would pass the active-loan check and disburse
    a SECOND principal. With an `idempotency_key`, the principal credit raises
    DuplicateTransaction on a reused key and the whole disbursement (loan row +
    credit) rolls back, so a replayed request never double-disburses.
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
    credit(user, principal, "Loan disbursed", meta={"loan": ref}, reference=ref,
           idempotency_key=idempotency_key)
    return loan


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
    from wallet.services import DuplicateTransaction, with_idempotency_fingerprint

    amount = Decimal(str(amount))
    stale_detected = False
    stale_reference = ""
    with db_transaction.atomic():
        loan = Loan.objects.select_for_update().get(pk=loan.pk)
        if loan.status != Loan.ACTIVE or loan.outstanding <= Decimal("0.00"):
            stale_detected = True
            # The view may have selected this row while it was ACTIVE, then wait
            # behind another repayment which closed it.  A silent return here
            # used to make the loser report success despite taking no money and,
            # worse, left its idempotency key free for a future loan.  Claim that
            # key with a FAILED evidence row. FAILED OUT rows have no balance
            # effect, and the original loan reference prevents reuse against a
            # later loan.
            if idempotency_key:
                # The active repayment path caps an overpayment to the loan's
                # outstanding amount. Keep the audit row within that same
                # ledger-safe bound even if an untrusted request supplied more
                # digits than Transaction.amount can store; the exact request
                # remains bound by its fingerprint and requested_amount below.
                marker_amount = min(
                    amount, max(loan.total_repayment, Decimal("0.01")),
                )
                try:
                    with db_transaction.atomic():  # contain a same-key race
                        marker = Transaction.objects.create(
                            user=user,
                            service="Loan repayment conflict",
                            amount=marker_amount,
                            direction=Transaction.OUT,
                            transaction_status=Transaction.FAILED,
                            reference=make_reference("ZLRF"),
                            meta=with_idempotency_fingerprint({
                                "loan": loan.reference,
                                "loan_repayment_outcome": "stale_loan",
                                "requested_amount": str(amount),
                                "balance_movement": "0.00",
                                "internal_evidence": True,
                            }, idempotency_key),
                            idempotency_key=idempotency_key,
                        )
                except IntegrityError:
                    raise DuplicateTransaction(idempotency_key)
                stale_reference = marker.reference
        else:
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
                        meta=with_idempotency_fingerprint(
                            {"loan": loan.reference}, idempotency_key),
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

    # Raise only after the inner transaction commits, otherwise the durable key
    # claim above would roll back with the exception.
    if stale_detected:
        raise LoanRepaymentStale(loan.reference, stale_reference)
    return loan
