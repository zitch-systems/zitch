"""Bank transfer (payout) endpoints + saved beneficiaries.

Payout to external banks needs a provider (Monnify disbursements / NIBSS); until
keys are set this runs in MOCK mode and resolves/settles automatically so the
flow is testable. Money still moves correctly out of the wallet ledger.
"""
from decimal import Decimal, InvalidOperation

from common.http import api, check_send_limits, fail, ok, require_user
from utility.providers import disbursement_resolve_account, disbursement_send
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
    res = disbursement_resolve_account(acct, bank.bank_code)
    if not res.get("success"):
        return fail(res.get("message", "Could not verify this account number"), status=400)
    return ok(success=True, name=res.get("name", ""))


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

    limit_err = check_send_limits(user, amount)
    if limit_err:
        return limit_err

    note = data.get("note", "")
    # Resolve server-side for the authoritative account name — Monnify rejects a
    # payout whose name doesn't match the enquiry, and we don't trust the client.
    resolved = disbursement_resolve_account(acct, bank.bank_code)
    if not resolved.get("success"):
        return fail(resolved.get("message", "Could not verify this account number"), status=400)
    name = resolved.get("name") or (data.get("name") or "Bank recipient").strip()

    try:
        txn = debit(user, amount, f"Transfer to {name}",
                    meta={"account": acct, "bank": bank.name, "note": note})
    except InsufficientFunds:
        return fail("Insufficient wallet balance", status=402)

    result = disbursement_send(amount, txn.reference, note or f"Transfer to {name}",
                               bank.bank_code, acct, name)
    if not result.get("success"):
        refund(txn)
        return fail(result.get("message", "Transfer failed"), status=502)
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
