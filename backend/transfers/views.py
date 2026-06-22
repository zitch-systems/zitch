"""Bank transfer (payout) endpoints + saved beneficiaries.

Payout to external banks needs a provider (Monnify disbursements / NIBSS); until
keys are set this runs in MOCK mode and resolves/settles automatically so the
flow is testable. Money still moves correctly out of the wallet ledger.
"""
import json

from django.views.decorators.csrf import csrf_exempt

from common.http import (
    api, check_daily_limit, check_send_limits, fail, idempotent_replay, ok, parse_amount,
    require_user, spend_key, verify_transaction_pin,
)
from common.ratelimit import ratelimit
from utility.providers import disbursement_resolve_account, payment_verify_signature
from wallet.models import Transaction
from wallet.services import existing_for_key, reverse_transfer, settle_payout

from .models import Bank
from .services import PayoutError, detect_account_banks, execute_payout


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
@ratelimit("resolve_account", limit=20, window=60)
@require_user
def resolve_account(request):
    """POST /api/transfers/resolve/ {access_token, account_number, bank?}
    -> {success, name, bank, bank_name, matches}

    With ``bank`` (our slug) it resolves at that one bank. WITHOUT it, the bank is
    auto-detected: a name-enquiry runs across the active banks and the match(es)
    are returned, so the app fills the bank in automatically once a 10-digit
    account number is typed (``matches`` lists every hit; usually exactly one).
    """
    acct = (request.data.get("account_number") or "").strip()
    if len(acct) != 10 or not acct.isdigit():
        return fail("Enter a valid 10-digit account number")

    bank_slug = str(request.data.get("bank", "") or "").strip()
    if bank_slug:  # explicit bank (manual pick / override) — resolve at just that one
        bank = Bank.objects.filter(code=bank_slug).first()
        if bank is None:
            return fail("Select a bank", status=404)
        res = disbursement_resolve_account(acct, bank.bank_code)
        if not res.get("success"):
            return fail(res.get("message", "Could not verify this account number"), status=400)
        return ok(success=True, name=res.get("name", ""), bank=bank.code, bank_name=bank.name,
                  matches=[{"bank": bank.code, "bank_name": bank.name, "name": res.get("name", "")}])

    matches = detect_account_banks(acct)  # auto-detect across banks
    if not matches:
        return fail("Couldn't detect the bank for this account number — pick the bank manually.", status=404)
    top = matches[0]
    return ok(success=True, name=top["name"], bank=top["bank"], bank_name=top["bank_name"], matches=matches)


@api
@ratelimit("bank_transfer", limit=12, window=60)
@require_user
def bank_transfer(request):
    """POST /api/transfers/send/
    {access_token, account_number, bank, name, amount, note?, transaction_pin}
    -> {success, wallet, reference}
    """
    user, data = request.user_obj, request.data

    pin_err = verify_transaction_pin(user, data.get("transaction_pin"))
    if pin_err:
        return pin_err

    acct = (data.get("account_number") or "").strip()
    if len(acct) != 10:
        return fail("Enter a valid 10-digit account number")
    bank = Bank.objects.filter(code=str(data.get("bank", ""))).first()
    if bank is None:
        return fail("Select a bank", status=404)

    amount = parse_amount(data.get("amount"))
    if amount is None:
        return fail("Enter a valid amount")
    if amount < 10:
        return fail("Minimum transfer is ₦10")

    limit_err = check_send_limits(user, amount)
    if limit_err:
        return limit_err

    key = spend_key(data.get("idempotency_key"), user, "bank", acct, bank.code, amount)
    replay = idempotent_replay(existing_for_key(user, key))
    if replay:
        return replay

    # Daily transfer cap (after replay so a retried transfer replays cleanly).
    daily_err = check_daily_limit(user, amount, "transfer")
    if daily_err:
        return daily_err

    note = data.get("note", "")
    # Resolve server-side for the authoritative account name — Monnify rejects a
    # payout whose name doesn't match the enquiry, and we don't trust the client.
    resolved = disbursement_resolve_account(acct, bank.bank_code)
    if not resolved.get("success"):
        return fail(resolved.get("message", "Could not verify this account number"), status=400)
    name = resolved.get("name") or (data.get("name") or "Bank recipient").strip()

    try:
        txn = execute_payout(user, amount, acct, bank, name, note=note, idempotency_key=key)
    except PayoutError as exc:
        if exc.kind == "duplicate":
            return idempotent_replay(existing_for_key(user, key)) or fail("Duplicate request", status=409)
        if exc.kind == "insufficient":
            return fail("Insufficient wallet balance", status=402)
        return fail(exc.message, status=502)

    from wallet.services import get_or_create_wallet
    wallet = get_or_create_wallet(user)
    if txn.transaction_status == Transaction.PENDING:
        # Rail queued it but hasn't confirmed — don't claim "sent".
        return ok(pending=True, wallet=str(wallet.balance), reference=txn.reference,
                  message="Your transfer is processing and will be confirmed shortly.")
    return ok(success=True, wallet=str(wallet.balance), reference=txn.reference, message="Money sent")


@csrf_exempt
def disbursement_webhook(request):
    """POST /api/transfers/webhook/ — Monnify disbursement (payout) callback.

    The terminal-state safety net: a success/completed event settles a payout we
    left PENDING on send; a failed/reversed event refunds the wallet. HMAC-verified,
    idempotent (status-guarded), and always 200 on accepted events so Monnify stops
    retrying.
    """
    if request.method != "POST":
        return fail("Method not allowed", status=405)
    if not payment_verify_signature(request.body, request.headers.get("monnify-signature", "")):
        return fail("Invalid signature", status=401)
    try:
        event = json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        return fail("Invalid payload", status=400)

    data = event.get("eventData", {}) or {}
    reference = data.get("reference", "")  # the merchant reference we sent (our txn ref)
    etype = event.get("eventType", "")
    if etype in ("FAILED_DISBURSEMENT", "REVERSED_DISBURSEMENT") and reference:
        reverse_transfer(reference)
    elif etype in ("SUCCESSFUL_DISBURSEMENT", "COMPLETED_DISBURSEMENT") and reference:
        # Confirm a previously-PENDING payout (we no longer settle on send).
        settle_payout(reference)
    from whatsapp.ops import record_audit
    record_audit("webhook.monnify_disbursement", actor_type="system", target=reference,
                 after={"event": event.get("eventType", ""), "signature": "verified"})
    return ok(status=True)
