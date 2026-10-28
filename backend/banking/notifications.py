"""Durable notification ingestion; external confirmation precedes ledger credit."""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from wallet.models import Transaction, Wallet
from wallet.services import credit

from .client import require_environment, Client, ProviderError, config, money
from .models import BankAccount, Instruction, Notification
from .payments import digest, requery

ACK = {"success": True, "code": "00", "status": "SUCCESS", "message": "Acknowledged"}


def parse_transfer(body):
    order = body.get("order") if isinstance(body.get("order"), dict) else {}
    transfer = body.get("transaction") if isinstance(body.get("transaction"), dict) else {}
    customer = body.get("customer") if isinstance(body.get("customer"), dict) else {}
    account = customer.get("account") if isinstance(customer.get("account"), dict) else {}
    number = str(body.get("accountnumber") or account.get("number") or "")
    ref = str(body.get("nipsessionid") or transfer.get("externalreference") or "")
    if not ref or len(ref) > 160 or len(number) != 10 or not number.isascii() or not number.isdigit():
        raise ValueError("Missing bank account or session reference")
    amount = Decimal(money(body.get("amount", order.get("amountpaid"))))
    if body.get("amount") is not None and order.get("amountpaid") is not None and amount != Decimal(money(order["amountpaid"])):
        raise ValueError("Conflicting notification amounts")
    if order.get("currency", "NGN") != "NGN":
        raise ValueError("Unsupported currency")
    if body.get("accountnumber") and account.get("number") and body["accountnumber"] != account["number"]:
        raise ValueError("Conflicting notification accounts")
    reversal = str(body.get("narration", "")).upper().startswith(("RVSL", "REVERSAL"))
    return number, ref, amount, reversal


def ingest(event, body):
    if event == "account-upgrade":
        data = body.get("data") if isinstance(body.get("data"), dict) else body
        number = str(body.get("accountNumber") or data.get("accountNumber") or "")
        bank = BankAccount.objects.filter(number=number, environment=config().get("ENVIRONMENT", "sandbox")).first()
        if not bank:
            raise ValueError("Unbound account notification")
        ref = digest([number, data.get("status"), data.get("message")])
        amount = None
    elif event in {"transfer", "reversal"}:
        number, ref, amount, reversal = parse_transfer(body)
        if reversal:
            event = "reversal"
        bank = BankAccount.objects.filter(number=number, environment=config().get("ENVIRONMENT", "sandbox")).first()
        if not bank:
            raise ValueError("Unknown bank account")
    else:
        raise ValueError("Unsupported notification event")
    # Persist only routing and financial facts, not the sender's personal data.
    binding = digest([event, number, ref, str(amount)])
    with transaction.atomic():
        notice, created = Notification.objects.get_or_create(event=event, reference=ref, account=bank,
            defaults={"amount": amount, "payload_digest": binding, "next_check": timezone.now()})
        if not created and notice.payload_digest != binding:
            Notification.objects.filter(pk=notice.pk).update(last_error="conflicting_delivery")
            raise ValueError("Conflicting notification")
    return notice


def process(notice):
    require_environment(notice.account.environment)
    if notice.state == "complete":
        return True
    # Lease prevents overlapping scheduler deliveries without holding a database
    # lock over bank HTTP. An expired lease is retried using the same reference.
    claimed = Notification.objects.filter(pk=notice.pk, next_check__lte=timezone.now()).update(
        next_check=timezone.now() + timedelta(minutes=2), attempts=F("attempts") + 1)
    if not claimed:
        return False
    try:
        if notice.event == "account-upgrade":
            from .upgrades import refresh_upgrade
            refresh_upgrade(notice.account)
            Notification.objects.filter(pk=notice.pk).update(state="complete", next_check=None)
            return True
        response = Client().notification(notice.reference, notice.account.number)
        if not response.ok or not isinstance(response.data, dict):
            raise ProviderError("inflow_not_confirmed")
        number, ref, amount, reversal = parse_transfer(response.data)
        body = response.data
        order = body.get("order") if isinstance(body.get("order"), dict) else {}
        if (number != notice.account.number or ref != notice.reference or amount != notice.amount
                or str(body.get("code", "")) != "00"
                or str(body.get("message", "")).upper() != "SUCCESS"
                or order.get("status", "PAID") != "PAID"):
            raise ProviderError("inflow_binding_mismatch")
        if reversal or notice.event == "reversal":
            # An inflow alone cannot identify which debit/fee to reverse. Requery
            # the bound original if available; preserve an explicit review item
            # until the bank supplies unambiguous reversal correlation.
            original = Instruction.objects.filter(reference=ref, account=notice.account).first()
            if original:
                requery(original)
            Notification.objects.filter(pk=notice.pk).update(state="review", last_error="reversal_correlation_required", next_check=None)
            return False
        ledger_ref = "9PIN" + digest([notice.account.environment, number, ref])[:56]
        with transaction.atomic():
            wallet = Wallet.objects.select_for_update().get(pk=notice.account.wallet_id)
            locked = Notification.objects.select_for_update().get(pk=notice.pk)
            if locked.state == "complete":
                return True
            prior = Transaction.objects.filter(reference=ledger_ref).first()
            if prior:
                if (prior.user_id != wallet.user_id or prior.amount != amount or prior.direction != Transaction.IN
                        or prior.transaction_status != Transaction.SUCCESS
                        or (prior.meta or {}).get("provider") != "9psb"
                        or (prior.meta or {}).get("environment") != notice.account.environment
                        or (prior.meta or {}).get("bank_notification") != ref):
                    raise ProviderError("duplicate_inflow_conflict")
                txn = prior
            else:
                txn = credit(wallet.user, amount, "Bank transfer received", reference=ledger_ref,
                    meta={"provider": "9psb", "bank_notification": ref, "account_number": number,
                          "environment": notice.account.environment})
            locked.state, locked.transaction, locked.next_check = "complete", txn, None
            locked.last_error = ""
            locked.save(update_fields=["state", "transaction", "next_check", "last_error"])
        return True
    except (ProviderError, ValueError) as exc:
        code = exc.code if isinstance(exc, ProviderError) else "invalid_inflow_confirmation"
        Notification.objects.filter(pk=notice.pk).update(last_error=code, next_check=timezone.now() + timedelta(minutes=5))
        return False
