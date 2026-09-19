"""Loan endpoints: eligibility/quote, request (disburse), repay."""
import hmac
from decimal import Decimal

from django.db import IntegrityError

from common.http import (
    api, fail, idempotent_replay, ok, parse_amount, require_user, spend_key, verify_transaction_pin,
)
from common.ratelimit import ratelimit
from utility.providers import bnpl_offers as provider_bnpl_offers
from wallet.services import DuplicateTransaction, InsufficientFunds, existing_for_key, get_or_create_wallet

from .models import Loan
from .services import LoanError, LoanRepaymentStale, credit_limit, disburse, repay

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


def _stale_repayment_replay(prior):
    """Replay the stable stale-loan response without bypassing key binding."""
    if prior is None:
        return None
    meta = prior.meta if isinstance(prior.meta, dict) else {}
    if meta.get("loan_repayment_outcome") != "stale_loan":
        return None
    expected = str(getattr(prior, "_requested_idempotency_fingerprint", "") or "")
    stored = str(meta.get("idempotency_fingerprint") or "")
    if not expected or not stored or not hmac.compare_digest(expected, stored):
        return None
    return fail(
        "This loan was repaid before this request could be applied. No money was taken.",
        status=409,
        code=LoanRepaymentStale.code,
        duplicate=True,
        reference=prior.reference,
    )


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
    principal = parse_amount(data.get("amount"))
    if principal is None:
        return fail("Enter a valid amount")
    if principal < MIN_PRINCIPAL:
        return fail(f"Minimum loan is ₦{MIN_PRINCIPAL:,.0f}")
    tenure = _parse_tenure(data.get("tenure_days", 30))
    if tenure is None:
        return fail("Tenure must be 15, 30 or 60 days")

    # Dedupe the disbursement: a retried/replayed request (esp. after the prior
    # loan was repaid, which clears the one-active-loan guard) must not disburse a
    # second principal. Mirrors loan_repay.
    raw_key = data.get("idempotency_key")
    if not isinstance(raw_key, str) or not raw_key.strip():
        return fail("A stable idempotency key is required for loan requests",
                    status=400, code="idempotency_key_required")
    key = spend_key(raw_key, user, "loan-request", principal, tenure)
    replay = idempotent_replay(existing_for_key(user, key))
    if replay:
        return replay

    pin_err = verify_transaction_pin(user, data.get("transaction_pin"))
    if pin_err:
        return pin_err

    if user.loans.filter(status=Loan.ACTIVE).exists():
        return fail("You already have an active loan", status=409)
    if principal > credit_limit(user):
        return fail("Amount exceeds your available credit", status=403)

    try:
        loan = disburse(user, principal, tenure, idempotency_key=key)
    except DuplicateTransaction:
        return idempotent_replay(existing_for_key(user, key)) or fail("Duplicate request", status=409)
    except LoanError as e:
        # Eligibility re-check inside the lock caught a race past the checks above.
        return fail(str(e), status=409)
    except IntegrityError:
        # DB partial-unique backstop: a concurrent disbursement won the race.
        return fail("You already have an active loan", status=409)
    wallet = get_or_create_wallet(user)
    return ok(
        success=True,
        wallet=str(wallet.balance),
        loan=_loan_dict(loan),
        reference=loan.reference,
        message="Loan disbursed",
    )


@api
@ratelimit("loan_repay", limit=12, window=60)
@require_user
def loan_repay(request):
    """POST /api/loans/repay/ {access_token, amount, transaction_pin}
    -> {success, wallet, loan}
    """
    user, data = request.user_obj, request.data
    amount = parse_amount(data.get("amount"))
    if amount is None:
        return fail("Enter a valid amount")

    # Replay before the active-loan guard: the first successful repayment can
    # close the loan, so a lost-response retry must still replay instead of
    # answering "no active loan" and tempting the client to mint a new key.
    raw_key = data.get("idempotency_key")
    if not isinstance(raw_key, str) or not raw_key.strip():
        return fail("A stable idempotency key is required for loan repayments",
                    status=400, code="idempotency_key_required")
    key = spend_key(raw_key, user, "loan-repay", amount)
    prior = existing_for_key(user, key)
    active = user.loans.filter(status=Loan.ACTIVE).first()
    if prior is not None and active is not None:
        prior_loan = str((prior.meta or {}).get("loan") or "")
        if prior_loan and prior_loan != active.reference:
            return fail(
                "That retry key belongs to a repayment on a different loan. "
                "Authorize this loan repayment again.",
                status=409,
                code="idempotency_conflict",
            )
    replay = _stale_repayment_replay(prior) or idempotent_replay(prior)
    if replay:
        return replay

    if active is None:
        return fail("You have no active loan", status=404)

    pin_err = verify_transaction_pin(user, data.get("transaction_pin"))
    if pin_err:
        return pin_err

    try:
        loan = repay(user, active, amount, idempotency_key=key)
    except DuplicateTransaction:
        prior = existing_for_key(user, key)
        return (_stale_repayment_replay(prior) or idempotent_replay(prior)
                or fail("Duplicate request", status=409))
    except LoanRepaymentStale as e:
        return fail(
            str(e),
            status=409,
            code=e.code,
            reference=e.transaction_reference,
        )
    except InsufficientFunds:
        return fail("Insufficient wallet balance", status=402)

    wallet = get_or_create_wallet(user)
    payment = existing_for_key(user, key)
    return ok(
        success=True,
        wallet=str(wallet.balance),
        loan=_loan_dict(loan),
        reference=payment.reference,
        message="Repayment successful",
    )


@api
@require_user
def bnpl_offers(request):
    """POST /api/loans/bnpl/offers/ {access_token} -> {success, offers}

    The ALAT Buy-Now-Pay-Later product offers the user is eligible for (read-only). The
    consent -> accept -> disburse commitment flow (real external credit) is built at the
    client layer (utility.wema.bnpl_*) but intentionally NOT exposed as an end-user
    endpoint yet — it creates real debt and needs product/compliance sign-off first."""
    res = provider_bnpl_offers()
    if not res.get("success"):
        return fail(res.get("message", "BNPL is unavailable right now"), status=502)
    return ok(success=True, offers=res.get("offers", []) or [], mock=bool(res.get("mock")))
