"""Durable, single-submission payment orchestration and terminal settlement."""
import hashlib
import json
import re
import secrets
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from wallet.models import Transaction, Wallet
from wallet.services import LimitExceeded, InsufficientFunds, settle_or_refund

from .accounts import account_for, require_enabled, spendable, update_account
from .client import Client, ProviderError, config, money, phone, require_environment
from .models import Instruction, PreviousBankArchive
from .secrets import seal, unseal


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def spending_account(user, *, refresh=False):
    try:
        require_enabled(user)
    except ValueError as exc:
        raise LimitExceeded(str(exc)) from None
    bank = account_for(user)
    if not bank:
        raise LimitExceeded("Complete your 9PSB account setup first")
    if not user.email_verified or not user.phone_verified or user.tier < 1:
        raise LimitExceeded("Verify your contact details and bank identity before spending")
    if refresh:
        try:
            update_account(bank)
        except ProviderError:
            raise LimitExceeded("We could not confirm your bank account status. Try again shortly") from None
    if bank.status != "active" or bank.pnd is not False or bank.frozen is not False:
        raise LimitExceeded("Your bank account is restricted or awaiting activation. Contact support")
    if bank.available_balance is None or bank.opening_review_required:
        raise LimitExceeded("Your bank balance needs confirmation before spending")
    if not bank.checked_at or bank.checked_at < timezone.now() - timedelta(minutes=5):
        raise LimitExceeded("Your bank account status needs to be refreshed")
    archive = PreviousBankArchive.objects.filter(wallet=bank.wallet).first()
    if archive and archive.pending_transactions:
        raise LimitExceeded("An earlier payment needs review before new spending")
    return bank


def bank_limit_error(user, amount):
    try:
        bank = spending_account(user)
    except LimitExceeded as exc:
        return str(exc)
    # Deposit and balance limits are NOT outbound spending limits. Unknown bank
    # limits are not guessed; the bank still enforces them on every instruction.
    try:
        single = json.loads(config().get("SINGLE_OUTBOUND_LIMITS", "{}"))
        daily = json.loads(config().get("DAILY_OUTBOUND_LIMITS", "{}"))
        cap = Decimal(str(single[str(bank.tier)])) if str(bank.tier) in single else None
        daily_cap = Decimal(str(daily[str(bank.tier)])) if str(bank.tier) in daily else None
        if (cap is not None and (not cap.is_finite() or cap < 0)) or (daily_cap is not None and (not daily_cap.is_finite() or daily_cap < 0)):
            return "Bank limits need configuration review"
    except (ValueError, TypeError, ArithmeticError):
        return "Bank limits need configuration review"
    if cap is not None and amount > cap:
        return "This exceeds your bank's single outgoing payment limit"
    if daily_cap is not None:
        from django.db.models import Sum
        start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
        spent = Transaction.objects.filter(user=user, meta__provider="9psb", direction=Transaction.OUT,
            currency="NGN", created__gte=start, transaction_status__in=[Transaction.PENDING, Transaction.SUCCESS]).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        if amount + spent > daily_cap:
            return "This exceeds your bank's daily outgoing payment limit"
    return None


def _result(state, instruction, *, token="", code="", receipt=None):
    value = {}
    if state == "success":
        # Replays and background completion retain purchased value.
        meta = instruction.transaction.meta or {}
        value = {key: meta[key] for key in ("token", "electricity_units")
                 if isinstance(meta.get(key), str) and meta[key]}
        value.update(receipt or {})
        if token:
            value["token"] = token
    return {"success": state == "success", "pending": state == "pending",
        "status": {"success": "SUCCESS", "failed": "FAILED", "pending": "PENDING"}[state],
        "reference": instruction.transaction.reference, "provider_reference": instruction.reference,
        "vas_rail": "9psb", "vas_type": instruction.operation,
        "receipt_pending": state == "success" and instruction.receipt_pending,
        "token": "", **value, "code": code,
        "message": "Payment is processing." if state == "pending" else "Payment could not be completed." if state == "failed" else "Payment completed."}


def bind_instruction(txn, product, operation, payload):
    """Create immutable dispatch material before the first HTTP request."""
    bank = spending_account(txn.user)
    if product == "vas" and not config().get("VAS_ENABLED"):
        raise LimitExceeded("Bill payments are temporarily unavailable")
    request_hash = digest([product, operation, payload])
    with transaction.atomic():
        Wallet.objects.select_for_update().get(pk=bank.wallet_id)
        locked = Transaction.objects.select_for_update().get(pk=txn.pk)
        prior = Instruction.objects.filter(transaction=locked).first()
        if prior:
            if prior.request_digest != request_hash:
                raise LimitExceeded("Payment details changed for an existing reference")
            return prior
        if locked.transaction_status != Transaction.PENDING or locked.direction != Transaction.OUT:
            raise LimitExceeded("A pending payment reservation is required")
        fee = Decimal(str(payload.get("fee", "0")))
        principal = Decimal(money(payload.get("amount")))
        if (not fee.is_finite() or fee < 0 or fee != fee.quantize(Decimal("0.01"))
                or (product != "waas" and fee != 0)
                or str(payload.get("source")) != bank.number or principal + fee != locked.amount):
            raise LimitExceeded("Payment source or amount does not match its reservation")
        if locked.meta.get("provider") != "9psb":
            raise LimitExceeded("A previous provider's reservation cannot use the new bank")
        return Instruction.objects.create(transaction=locked, account=bank, product=product,
            operation=operation, amount=Decimal(money(payload["amount"])),
            reference="Z9" + secrets.token_hex(8).upper(), request_digest=request_hash,
            encrypted_request=seal(payload), next_check=timezone.now())


def _invoke(instruction, body):
    client = Client(instruction.product)
    ref = instruction.reference
    if instruction.operation == "OTHER_BANKS":
        return client.transfer(ref, body["amount"], body["source"], body["destination"],
            body["bank_code"], body["destination_name"], instruction.account.name, body["narration"])
    if instruction.operation in {"airtime", "data"}:
        return client.topup(instruction.operation, ref, body["amount"], body["source"],
            body["phone"], body["network"], product_id=body.get("product_id", ""))
    if instruction.operation == "bill":
        return client.pay_bill(ref, body["amount"], body["source"], biller=body["biller_id"],
            item=body["item_id"], customer=body["customer"], mobile=body["phone"],
            name=body["customer_name"], other_field=body.get("other_field", ""))
    raise ValueError("Unsupported payment operation")


def _vas_response_matches(instruction, data, body):
    """Every echoed identifier must agree, including contradictory aliases."""
    try:
        for key in ("transactionId", "transactionReference", "reference"):
            if key in data and data[key] != instruction.reference:
                return False
        if "amount" in data and money(data["amount"]) != money(instruction.amount):
            return False
        for key in ("debitAccount", "accountNumber"):
            if key in data and data[key] != instruction.account.number:
                return False
        if instruction.operation in {"airtime", "data"}:
            for key in ("recipient", "phoneNumber"):
                if key in data and (not isinstance(data[key], str) or phone(data[key]) != body["phone"]):
                    return False
            if "network" in data and (not isinstance(data["network"], str)
                    or data["network"].strip().upper() != body["network"].strip().upper()):
                return False
            if "productId" in data and data["productId"] != body.get("product_id"):
                return False
        else:
            for key, expected in (("billerId", "biller_id"), ("itemId", "item_id"), ("customerId", "customer")):
                if key in data and data[key] != body.get(expected):
                    return False
    except (ValueError, KeyError):
        return False
    return True


def dispatch(instruction):
    require_environment(instruction.account.environment)
    # A CAS claim closes API/API and API/worker races. Only 'prepared' may send.
    claimed = Instruction.objects.filter(pk=instruction.pk, state="prepared").update(
        state="sending", attempts=F("attempts") + 1, next_check=timezone.now() + timedelta(minutes=2))
    if not claimed:
        instruction.refresh_from_db()
        return _result(instruction.state if instruction.state in {"success", "failed"} else "pending", instruction)
    try:
        body = unseal(instruction.encrypted_request)
        reply = _invoke(instruction, body)
        state = reply.payment_state(product=instruction.product)
        data = reply.data if isinstance(reply.data, dict) else {}
        if instruction.product == "vas" and not _vas_response_matches(instruction, data, body):
            return finish(instruction, "pending", error="vas_response_mismatch")
        from .vas import receipt as bill_receipt
        receipt = bill_receipt(data) if instruction.operation == "bill" and state == "success" else {}
        receipt_pending = instruction.operation == "bill" and state == "success" and data.get("isToken") is True and not receipt.get("token")
        # Retain a bounded machine code for operator diagnosis, never the raw
        # response message (which can echo account or identity information).
        response_code = str(reply.body.get("responseCode", ""))
        error = (f"{instruction.product}_response_{response_code}"
                 if state == "failed" and re.fullmatch(r"[0-9]{2,4}", response_code) else "")
    except ProviderError as exc:
        state = "pending" if exc.uncertain else "failed"
        receipt, error, receipt_pending = {}, exc.code, False
    except (ValueError, KeyError):
        state, receipt, error, receipt_pending = "failed", {}, "invalid_instruction", False
    return finish(instruction, state, receipt=receipt, receipt_pending=receipt_pending, error=error)


@transaction.atomic
def finish(instruction, state, *, token="", error="", receipt=None, receipt_pending=False):
    # Match the existing money state machine's wallet -> transaction lock order.
    Wallet.objects.select_for_update().get(pk=instruction.account.wallet_id)
    txn = Transaction.objects.select_for_update().get(pk=instruction.transaction_id)
    locked = Instruction.objects.select_for_update().get(pk=instruction.pk)
    if locked.state in {"success", "failed"}:
        if locked.state == "success" and locked.receipt_pending:
            # Delivery is already confirmed. Receipt recovery can enrich that
            # receipt, but no failed/unknown lookup may refund a delivered bill.
            if state == "success" and (receipt or {}).get("token"):
                txn.meta = {**txn.meta, **receipt, "receipt_pending": False}
                txn.save(update_fields=["meta"])
                locked.receipt_pending, locked.encrypted_request, locked.next_check = False, "", None
                locked.last_error = ""
                locked.save(update_fields=["receipt_pending", "encrypted_request", "next_check", "last_error", "updated"])
                return _result("success", locked, receipt=receipt)
            locked.next_check = timezone.now() + timedelta(minutes=5)
            locked.last_error = "conflicting_terminal_status" if state == "failed" else (error or "receipt_unavailable")
            locked.save(update_fields=["next_check", "last_error", "updated"])
            return _result("success", locked)
        if state in {"success", "failed"} and state != locked.state:
            locked.last_error = "conflicting_terminal_status"
            locked.save(update_fields=["last_error", "updated"])
        return _result(locked.state, locked)
    locked.receipt_pending = state == "success" and receipt_pending
    result = _result(state, locked, token=token, code=error, receipt=receipt)
    outcome = settle_or_refund(txn, result)
    if outcome in {"success", "failed"}:
        state = outcome
        result = _result(state, locked, token=token if state == "success" else "", code=error, receipt=receipt)
    elif outcome == "quarantined":
        state = "pending"
        result = _result(state, locked, code="settlement_review")
    locked.state, locked.last_error = state, error
    locked.receipt_pending = state == "success" and locked.receipt_pending
    locked.next_check = timezone.now() + timedelta(minutes=5 if locked.receipt_pending else 2) if state == "pending" or locked.receipt_pending else None
    if state != "pending" and not locked.receipt_pending:
        locked.encrypted_request = ""
    locked.save(update_fields=["state", "last_error", "next_check", "encrypted_request", "receipt_pending", "updated"])
    return result


def requery(instruction):
    require_environment(instruction.account.environment)
    if instruction.state in {"success", "failed"} and not instruction.receipt_pending:
        return _result(instruction.state, instruction)
    if instruction.state == "prepared":
        # No worker may create a new instruction merely by running reconciliation.
        # Prepared material was already authorized and durably reserved.
        return dispatch(instruction)
    try:
        client = Client(instruction.product)
        if instruction.product == "vas":
            response = client.vas_status(instruction.reference, bill=instruction.operation == "bill")
        else:
            response = client.transaction_status(instruction.reference, instruction.amount,
                instruction.operation, timezone.localtime(instruction.created).date().isoformat(), instruction.account.number)
        state = response.payment_state(product=instruction.product, query=True)
        data = response.data if isinstance(response.data, dict) else {}
        if instruction.product == "vas":
            if not _vas_response_matches(instruction, data, unseal(instruction.encrypted_request)):
                return finish(instruction, "pending", error="vas_status_mismatch")
            from .vas import receipt as bill_receipt
            receipt = bill_receipt(data, query=True) if state == "success" and instruction.operation == "bill" else {}
            receipt_pending = instruction.operation == "bill" and state == "success" and data.get("isToken") is True and not receipt.get("token")
            return finish(instruction, state, receipt=receipt, receipt_pending=receipt_pending)
        # Bind the queried payment whenever the bank includes its reference or
        # amount. Contradictions are review cases, never terminal settlement.
        returned = data.get("transactionId") or data.get("transactionReference") or data.get("reference")
        if returned is not None and str(returned) != instruction.reference:
            return finish(instruction, "pending", error="status_reference_mismatch")
        if data.get("amount") is not None and Decimal(money(data["amount"])) != instruction.amount:
            return finish(instruction, "pending", error="status_amount_mismatch")
        return finish(instruction, state)
    except (ProviderError, ValueError):
        Instruction.objects.filter(pk=instruction.pk).update(next_check=timezone.now() + timedelta(minutes=5), last_error="status_unavailable")
        return _result("success" if instruction.state == "success" else "pending", instruction, code="status_unavailable")


def submit_existing(reference, product, operation, payload):
    txn = Transaction.objects.select_related("user").get(reference=reference)
    try:
        instruction = bind_instruction(txn, product, operation, payload)
    except (ValueError, LimitExceeded):
        return {"success": False, "pending": False, "not_charged": True,
                "message": "Payment details could not be confirmed. Please try again."}
    return dispatch(instruction)
