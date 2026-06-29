"""Open-banking (Mono) endpoints: link an external bank, view it, and fund the
wallet from it via DirectPay.

Account login happens entirely in Mono's Connect widget client-side; only the
short-lived auth code reaches us here. Funding reuses the wallet's FundingIntent
+ settle_funding path (idempotent), stamped meta.provider="mono".
"""
import json
import logging

from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from common.http import (
    api, fail, ok, parse_amount, require_user,
    verify_transaction_pin, check_send_limits, check_daily_limit,
    spend_key, idempotent_replay,
)
from utility import mono
from wallet.models import FundingIntent, Transaction
from wallet.services import make_reference, settle_funding, existing_for_key, get_or_create_wallet
from transfers.models import Bank
from transfers.services import detect_account_banks, execute_payout, PayoutError

from .models import LinkedBankAccount

log = logging.getLogger("banklink")


def _serialize(a: LinkedBankAccount) -> dict:
    return {
        "id": a.id,
        "bank_name": a.bank_name,
        "account_number": a.masked_number,
        "account_name": a.account_name,
        "balance": (str(a.balance) if a.balance is not None else None),
        "balance_updated": (a.balance_updated.isoformat() if a.balance_updated else None),
        "status": a.status,
    }


@api
@require_user
def connect(request):
    """POST /api/banklink/connect/ {access_token, code}
    -> {success, account} — exchange a Mono Connect auth code and link the account.
    """
    user = request.user_obj
    code = (request.data.get("code") or "").strip()
    if not code:
        return fail("Missing Mono auth code")

    res = mono.exchange_token(code)
    if not res.get("success"):
        return fail(res.get("message", "Could not link your bank"), status=502)
    account_id = res["account_id"]

    details = mono.get_account(account_id)  # best-effort snapshot
    acct, _ = LinkedBankAccount.objects.update_or_create(
        mono_account_id=account_id,
        defaults={
            "user": user,
            "bank_name": details.get("bank_name", ""),
            "account_number": details.get("account_number", ""),
            "account_name": details.get("account_name", ""),
            "balance": details.get("balance_naira"),
            "balance_updated": timezone.now() if details.get("success") else None,
            "status": LinkedBankAccount.ACTIVE,
        },
    )
    return ok(success=True, account=_serialize(acct), message="Bank linked")


@api
@require_user
def list_accounts(request):
    """POST /api/banklink/list/ {access_token} -> {accounts: [...]}"""
    items = request.user_obj.linked_banks.filter(status=LinkedBankAccount.ACTIVE)
    return ok(accounts=[_serialize(a) for a in items])


@api
@require_user
def refresh(request):
    """POST /api/banklink/refresh/ {access_token, linked_id} -> {success, account}
    Re-pulls the linked account's balance from Mono and caches it.
    """
    acct = request.user_obj.linked_banks.filter(
        id=request.data.get("linked_id"), status=LinkedBankAccount.ACTIVE).first()
    if acct is None:
        return fail("Linked account not found", status=404)
    res = mono.get_balance(acct.mono_account_id)
    if res.get("success") and res.get("balance_naira") is not None:
        acct.balance = res["balance_naira"]
        acct.balance_updated = timezone.now()
        acct.save(update_fields=["balance", "balance_updated", "updated"])
    return ok(success=True, account=_serialize(acct))


@api
@require_user
def unlink(request):
    """POST /api/banklink/unlink/ {access_token, linked_id} -> {success}"""
    acct = request.user_obj.linked_banks.filter(id=request.data.get("linked_id")).first()
    if acct is None:
        return fail("Linked account not found", status=404)
    acct.status = LinkedBankAccount.UNLINKED
    acct.save(update_fields=["status", "updated"])
    return ok(success=True, message="Bank unlinked")


@api
@require_user
def fund(request):
    """POST /api/banklink/fund/ {access_token, linked_id, amount}
    -> {success, reference, authorization_url}

    Starts a Mono DirectPay debit from the linked bank. The wallet is credited
    only when Mono confirms via webhook (settle_funding, idempotent).
    """
    user = request.user_obj
    acct = user.linked_banks.filter(
        id=request.data.get("linked_id"), status=LinkedBankAccount.ACTIVE).first()
    if acct is None:
        return fail("Linked account not found", status=404)
    amount = parse_amount(request.data.get("amount"))
    if amount is None:
        return fail("Enter a valid amount")
    if amount < 100:
        return fail("Minimum funding amount is ₦100")

    reference = make_reference("ZMONO")
    FundingIntent.objects.create(user=user, reference=reference, amount=amount,
                                 meta={"provider": "mono", "linked_id": acct.id})
    email = user.email or f"{user.phone}@zitch.app"
    name = (user.get_full_name() or user.phone or "").strip()
    res = mono.initiate_directpay(amount, reference, email=email, name=name)
    if not res.get("success"):
        return fail(res.get("message", "Could not start bank funding"), status=502)
    return ok(success=True, reference=res.get("reference", reference),
              authorization_url=res.get("authorization_url", ""), mock=res.get("mock", False))


@api
@require_user
def payout(request):
    """POST /api/banklink/payout/ {access_token, linked_id, amount, pin, idempotency_key}
    -> {success, wallet, reference}

    Move money OUT of the Zitch wallet to the user's own linked bank account
    (PIN-verified). Reuses the transfers payout rail (detect bank -> execute_payout),
    so balance/limit/idempotency guards and the Kora settlement webhook all apply.
    """
    user, data = request.user_obj, request.data

    pin_err = verify_transaction_pin(user, data.get("pin") or data.get("transaction_pin"))
    if pin_err:
        return pin_err

    acct_obj = user.linked_banks.filter(
        id=data.get("linked_id"), status=LinkedBankAccount.ACTIVE).first()
    if acct_obj is None:
        return fail("Linked account not found", status=404)
    acct = (acct_obj.account_number or "").strip()
    if len(acct) != 10:
        return fail("This linked account can't receive a payout.", status=400)

    amount = parse_amount(data.get("amount"))
    if amount is None:
        return fail("Enter a valid amount")
    if amount < 100:
        return fail("Minimum payout is ₦100")

    limit_err = check_send_limits(user, amount)
    if limit_err:
        return limit_err

    key = spend_key(data.get("idempotency_key"), user, "mono_payout", str(acct_obj.id), amount)
    replay = idempotent_replay(existing_for_key(user, key))
    if replay:
        return replay

    daily_err = check_daily_limit(user, amount, "transfer")
    if daily_err:
        return daily_err

    # Route the payout by detecting the bank for the linked account number (the
    # linked account stores the bank name, not a routable code).
    matches = detect_account_banks(acct)
    bank = Bank.objects.filter(code=matches[0]["bank"]).first() if matches else None
    if bank is None:
        return fail("Couldn't route a payout to this bank. Please try a normal transfer.", status=400)
    name = (matches[0].get("name") or acct_obj.account_name or "Bank recipient").strip()

    try:
        txn = execute_payout(user, amount, acct, bank, name,
                             note=f"To {acct_obj.bank_name}".strip(), idempotency_key=key)
    except PayoutError as exc:
        if exc.kind == "duplicate":
            return idempotent_replay(existing_for_key(user, key)) or fail("Duplicate request", status=409)
        if exc.kind == "insufficient":
            return fail("Insufficient wallet balance", status=402)
        return fail(exc.message, status=502)

    wallet = get_or_create_wallet(user)
    if txn.transaction_status == Transaction.PENDING:
        return ok(pending=True, wallet=str(wallet.balance), reference=txn.reference,
                  message="Your payout is processing and will be confirmed shortly.")
    return ok(success=True, wallet=str(wallet.balance), reference=txn.reference,
              message=f"{name} funded")


@csrf_exempt
def webhook(request):
    """POST /api/banklink/webhook/ — Mono callback.

    Verifies the shared-secret header, then: marks accounts active on
    account_connected, and credits the wallet (idempotently) on a successful
    DirectPay payment. Always 200 on accepted events so Mono stops retrying.
    """
    if request.method != "POST":
        return fail("Method not allowed", status=405)
    try:
        event = json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        return fail("Invalid payload", status=400)
    signature = request.headers.get("mono-webhook-secret", "")
    if not mono.verify_webhook(event, signature):
        log.warning("mono_webhook_bad_signature has_header=%s", bool(signature))
        return fail("Invalid signature", status=401)

    etype = (event.get("event") or "").lower()
    data = event.get("data", {}) or {}
    if "payment" in etype and ("success" in etype or "received" in etype):
        reference = data.get("reference", "") or data.get("merchant_ref", "")
        if reference:
            settle_funding(reference)  # idempotent; uses the FundingIntent amount
            log.info("mono_funding_settled ref=%s", reference)
    elif "account" in etype and ("connected" in etype or "updated" in etype):
        account_id = data.get("id", "") or data.get("account", "")
        LinkedBankAccount.objects.filter(mono_account_id=account_id).update(
            status=LinkedBankAccount.ACTIVE)
        log.info("mono_account_event=%s acct=%s", etype, account_id)
    else:
        log.info("mono_webhook ignored_event=%s", etype)
    return ok(status=True)
