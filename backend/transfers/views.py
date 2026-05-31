"""Bank transfer (payout) endpoints + saved beneficiaries.

Payout to external banks needs a provider (Paystack Transfers / NIBSS); until
keys are set this runs in MOCK mode and resolves/settles automatically so the
flow is testable. Money still moves correctly out of the wallet ledger.
"""
from decimal import Decimal, InvalidOperation

from common.http import api, check_send_limits, fail, ok, require_user
from wallet.models import Transaction
from wallet.services import InsufficientFunds, debit, refund

from .models import Bank, Beneficiary


@api
def list_banks(request):
    """POST /api/transfers/banks/ -> {banks: [{code, name, color}]}"""
    banks = Bank.objects.filter(active=True)
    return ok(banks=[{"code": b.code, "name": b.name, "color": b.color} for b in banks])


@api
@require_user
def list_beneficiaries(request):
    """POST /api/transfers/beneficiaries/ {access_token}
    -> {beneficiaries: [{id, name, account_number, bank_name, initials, color}]}
    """
    items = request.user_obj.beneficiaries.all()
    return ok(beneficiaries=[
        {
            "id": b.id, "name": b.name, "account_number": b.account_number,
            "bank_name": b.bank_name, "initials": b.initials, "color": b.color,
        }
        for b in items
    ])


@api
@require_user
def resolve_account(request):
    """POST /api/transfers/resolve/ {access_token, account_number, bank}
    -> {success, name}

    MOCK mode returns a placeholder name; replace with the provider's
    account-name enquiry for production.
    """
    acct = (request.data.get("account_number") or "").strip()
    if len(acct) != 10:
        return fail("Enter a valid 10-digit account number")
    bank = Bank.objects.filter(code=str(request.data.get("bank", ""))).first()
    if bank is None:
        return fail("Select a bank", status=404)
    # TODO: real name enquiry via payout provider.
    return ok(success=True, name="ADEYEMI WILLIAM")


@api
@require_user
def bank_transfer(request):
    """POST /api/transfers/send/
    {access_token, account_number, bank, name, amount, note?, transaction_pin}
    -> {success, wallet, reference}
    """
    user, data = request.user_obj, request.data

    pin = (data.get("transaction_pin") or "").strip()
    if not user.transaction_pin:
        return fail("No transaction PIN set on this account", status=403)
    if not user.check_transaction_pin(pin):
        return fail("Incorrect transaction PIN", status=403)

    acct = (data.get("account_number") or "").strip()
    if len(acct) != 10:
        return fail("Enter a valid 10-digit account number")
    bank = Bank.objects.filter(code=str(data.get("bank", ""))).first()
    if bank is None:
        return fail("Select a bank", status=404)

    try:
        amount = Decimal(str(data.get("amount")))
    except (InvalidOperation, TypeError, ValueError):
        return fail("Enter a valid amount")
    if amount < 10:
        return fail("Minimum transfer is ₦10")

    limit_err = check_send_limits(user, amount, bool(data.get("face_confirmed")))
    if limit_err:
        return limit_err

    name = (data.get("name") or "Bank recipient").strip()
    note = data.get("note", "")

    try:
        txn = debit(user, amount, f"Transfer to {name}",
                    meta={"account": acct, "bank": bank.name, "note": note})
    except InsufficientFunds:
        return fail("Insufficient wallet balance", status=402)

    # TODO: real payout via provider; mock settles immediately.
    txn.transaction_status = Transaction.SUCCESS
    txn.save(update_fields=["transaction_status"])

    # Auto-save / dedupe the beneficiary.
    Beneficiary.objects.get_or_create(
        user=user, account_number=acct, bank_name=bank.name,
        defaults={"name": name, "bank_code": bank.bank_code, "color": bank.color or "#0FA295"},
    )

    from wallet.services import get_or_create_wallet
    wallet = get_or_create_wallet(user)
    return ok(success=True, wallet=str(wallet.balance), reference=txn.reference, message="Money sent")
