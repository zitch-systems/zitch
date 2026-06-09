"""Loan endpoints: eligibility/quote, request (disburse), repay."""
from decimal import Decimal

from django.db import IntegrityError

from common.http import (
    api, fail, idempotent_replay, ok, parse_amount, require_user, spend_key, verify_transaction_pin,
)
from common.ratelimit import ratelimit
from wallet.services import DuplicateTransaction, InsufficientFunds, existing_for_key, get_or_create_wallet

from .models import Loan
from .services import LoanError, credit_limit, disburse, repay

ALLOWED_TENURES = {15, 30, 60}
MIN_PRINCIPAL = Decimal("10000")


def _parse_tenure(value):
    """Tenure (days) as an int, or None if not a clean integer in the allow-list."""
    try:
        tenure = int(value)
    except (TypeError, ValueError):
        return None
    return tenure if tenure in ALLOWED_TENURES else None


def _loan_dict(loan: Loan) -> dict:
    return {
        "reference": loan.reference,
        "principal": str(loan.principal),
        "interest": str(loan.interest),
        "tenure_days": loan.tenure_days,
        "total_repayment": str(loan.total_repayment),
        "outstanding": str(loan.outstanding),
        "amount_repaid": str(loan.amount_repaid),
        "status": loan.status,
        "due_date": loan.due_date.strftime("%Y-%m-%d"),
    }


@api
@require_user
def loan_status(request):
    """POST /api/loans/status/ {access_token}
    -> {limit, available, active_loan, quote_rate}
    """
    user = request.user_obj
    active = user.loans.filter(status=Loan.ACTIVE).first()
    return ok(
        limit=str(Loan.DEFAULT_LIMIT),
        available=str(credit_limit(user)),
        quote_rate=str(Loan.RATE),
        active_loan=_loan_dict(active) if active else None,
    )


@api
@require_user
def loan_quote(request):
    """POST /api/loans/quote/ {access_token, amount, tenure_days}
    -> {principal, interest, total_repayment, tenure_days}
    """
    principal = parse_amount(request.data.get("amount"))
    if principal is None:
        return fail("Enter a valid amount")
    tenure = _parse_tenure(request.data.get("tenure_days", 30))
    if tenure is None:
        return fail("Tenure must be 15, 30 or 60 days")
    interest = Loan.quote(principal, tenure)
    return ok(
        principal=str(principal),
        interest=str(interest),
        total_repayment=str(principal + interest),
        tenure_days=tenure,
    )


@api
@ratelimit("loan_request", limit=10, window=60)
@require_user
def loan_request(request):
    """POST /api/loans/request/ {access_token, amount, tenure_days, transaction_pin}
    -> {success, wallet, loan}
    """
    user, data = request.user_obj, request.data

    pin_err = verify_transaction_pin(user, data.get("transaction_pin"))
    if pin_err:
        return pin_err

    if user.loans.filter(status=Loan.ACTIVE).exists():
        return fail("You already have an active loan", status=409)

    principal = parse_amount(data.get("amount"))
    if principal is None:
        return fail("Enter a valid amount")
    if principal < MIN_PRINCIPAL:
        return fail(f"Minimum loan is ₦{MIN_PRINCIPAL:,.0f}")
    if principal > credit_limit(user):
        return fail("Amount exceeds your available credit", status=403)

    tenure = _parse_tenure(data.get("tenure_days", 30))
    if tenure is None:
        return fail("Tenure must be 15, 30 or 60 days")

    try:
        loan = disburse(user, principal, tenure)
    except LoanError as e:
        # Eligibility re-check inside the lock caught a race past the checks above.
        return fail(str(e), status=409)
    except IntegrityError:
        # DB partial-unique backstop: a concurrent disbursement won the race.
        return fail("You already have an active loan", status=409)
    wallet = get_or_create_wallet(user)
    return ok(success=True, wallet=str(wallet.balance), loan=_loan_dict(loan), message="Loan disbursed")


@api
@ratelimit("loan_repay", limit=12, window=60)
@require_user
def loan_repay(request):
    """POST /api/loans/repay/ {access_token, amount, transaction_pin}
    -> {success, wallet, loan}
    """
    user, data = request.user_obj, request.data

    pin_err = verify_transaction_pin(user, data.get("transaction_pin"))
    if pin_err:
        return pin_err

    active = user.loans.filter(status=Loan.ACTIVE).first()
    if active is None:
        return fail("You have no active loan", status=404)

    amount = parse_amount(data.get("amount"))
    if amount is None:
        return fail("Enter a valid amount")

    key = spend_key(data.get("idempotency_key"), user, "loan-repay", active.reference, amount)
    replay = idempotent_replay(existing_for_key(user, key))
    if replay:
        return replay

    try:
        loan = repay(user, active, amount, idempotency_key=key)
    except DuplicateTransaction:
        return idempotent_replay(existing_for_key(user, key)) or fail("Duplicate request", status=409)
    except InsufficientFunds:
        return fail("Insufficient wallet balance", status=402)

    wallet = get_or_create_wallet(user)
    return ok(success=True, wallet=str(wallet.balance), loan=_loan_dict(loan), message="Repayment successful")
