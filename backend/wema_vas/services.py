"""Atomic bank receipts on the existing customer ledger; no provider calls."""
import hashlib
from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F, Sum
from django.utils import timezone

from wallet.models import Transaction, Wallet
from wallet.services import DuplicateTransaction, credit

from .config import config
from .contracts import InvalidPayload, notification
from .identity import decrypt_identity
from .models import Receipt, VirtualAccount

MAX_BALANCE = Decimal("999999999999.99")


class VasError(ValueError):
    pass


class ReplayConflict(VasError):
    pass


def account_details(number):
    values = config()
    return VirtualAccount.objects.select_related("user").filter(
        number=number, mode=values.get("MODE", "validation"), prefix=values.get("PREFIX", "711"),
    ).first()


def account_identity(account):
    return {"name": account.display_name, **decrypt_identity(account.encrypted_identity)}


def is_restricted(user):
    return VirtualAccount.objects.filter(user_id=user.pk, active=False).exists()


def assert_can_spend(user):
    """Caller holds Wallet lock; BlockAccount takes the same lock first.

    No enable switch exists until payout/TSQ and biller contracts are implemented.
    A collection account must never silently use the archived Partnership rail.
    """
    from wallet.services import LimitExceeded
    account = VirtualAccount.objects.filter(user_id=user.pk).first()
    if account is None:
        return
    if not account.active or not user.is_active:
        raise LimitExceeded("Your account is restricted. Please contact support.")
    raise LimitExceeded("Bank migration is awaiting payment activation. Please try again later.")


def acknowledge(row):
    if row.state == Receipt.HELD:
        return {"status": "07", "status_desc": "Funds held for review"}, 503
    if row.state == Receipt.CREDITED:
        valid = Transaction.objects.filter(
            pk=row.transaction_id, user_id=row.account.user_id,
            amount=row.amount, direction=Transaction.IN, transaction_status=Transaction.SUCCESS,
            currency="NGN",
        ).exists()
        if not valid:
            raise ImproperlyConfigured("VAS receipt ledger binding requires review")
    elif row.state != Receipt.VALIDATION or row.transaction_id is not None:
        raise ImproperlyConfigured("VAS receipt state requires review")
    return {"transactionreference": str(row.reference), "status": "00", "status_desc": "Okay"}, 200


def process_notification(body):
    fields, amount, occurred_at, fingerprint = notification(body)
    account = account_details(fields["craccount"])
    if account is None:
        return {"status": "07", "status_desc": "Invalid Account"}, 200
    if fields["craccountname"] != account.display_name:
        raise InvalidPayload("Account name does not match account lookup")
    try:
        with transaction.atomic():
            # Enrollment, blocking, credits and spending use wallet -> account.
            wallet = Wallet.objects.select_for_update().get(user_id=account.user_id)
            account = VirtualAccount.objects.select_for_update().get(pk=account.pk)
            existing = Receipt.objects.filter(session_id=fields["sessionid"]).first()
            if existing:
                if existing.fingerprint != fingerprint:
                    raise ReplayConflict()
                # Previously credited receipts remain successful after a block.
                return acknowledge(existing)
            state, ledger = Receipt.HELD, None
            if account.active and account.user.is_active:
                if account.mode == VirtualAccount.VALIDATION:
                    updated = VirtualAccount.objects.filter(
                        pk=account.pk, validation_balance__lte=MAX_BALANCE - amount,
                    ).update(validation_balance=F("validation_balance") + amount)
                    if updated != 1:
                        raise InvalidPayload("Validation balance limit exceeded")
                    state = Receipt.VALIDATION
                else:
                    if wallet.balance > MAX_BALANCE - amount:
                        raise InvalidPayload("Wallet balance limit exceeded")
                    digest = hashlib.sha256(fields["sessionid"].encode()).hexdigest()
                    ledger = credit(
                        account.user, amount, "funding", reference="ZVAS" + digest[:48],
                        idempotency_key="wema-vas:" + digest,
                        meta={"provider": "wema_vas", "funding_rail": "wema_vas",
                              "sender_name": fields["originatorname"],
                              "sender_account": fields["originatoraccountnumber"],
                              "sender_bank": fields["bankname"],
                              "session_id": fields["sessionid"],
                              "payment_reference": fields["paymentreference"]},
                    )
                    state = Receipt.CREDITED
            row = Receipt.objects.create(
                account=account, session_id=fields["sessionid"], payment_reference=fields["paymentreference"],
                fingerprint=fingerprint, amount=amount, source_account=fields["originatoraccountnumber"],
                source_bank=fields["bankname"], occurred_at=occurred_at, state=state, transaction=ledger,
            )
        # The outer transaction committed before the HTTP acknowledgement.
        return acknowledge(row)
    except (IntegrityError, DuplicateTransaction):
        # Cross-account races are stopped by unique session/payment constraints.
        # Only the exact already-committed session may ever be acknowledged.
        existing = Receipt.objects.select_related("account").filter(session_id=fields["sessionid"]).first()
        if existing and existing.fingerprint == fingerprint:
            return acknowledge(existing)
        raise ReplayConflict() from None


def account_balance(account):
    if account.mode == VirtualAccount.VALIDATION:
        return account.validation_balance
    # Late Partnership credits remain in the shared historical customer wallet.
    # They are not settlement funds belonging to this VAS collection account.
    # Outgoing VAS is disabled until its separate payout/TSQ contract is supplied.
    return account.receipts.filter(state=Receipt.CREDITED).aggregate(value=Sum("amount"))["value"] or Decimal("0.00")


def mini_statement(account):
    state = Receipt.VALIDATION if account.mode == VirtualAccount.VALIDATION else Receipt.CREDITED
    # Credits are the only implemented VAS money movement. Held receipts and
    # historical Partnership movements cannot be represented as VAS settlement.
    rows = account.receipts.filter(state=state).order_by("-occurred_at", "-pk")
    time_field = "occurred_at"
    latest = rows.first()
    if latest is None:
        return {"transactions": []}
    zone = ZoneInfo("Africa/Lagos")
    latest_date = timezone.localdate(getattr(latest, time_field), timezone=zone)
    # Explicit timezone boundaries avoid dependence on the process timezone.
    from datetime import datetime, time
    start = datetime.combine(latest_date - timedelta(days=9), time.min, tzinfo=zone)
    end = datetime.combine(latest_date + timedelta(days=1), time.min, tzinfo=zone)
    rows = rows.filter(**{time_field + "__gte": start, time_field + "__lt": end})
    if rows.count() > 5000:
        raise OperationalError("Statement exceeds the configured response limit")
    result = [{"accountNo": row.source_account, "bankName": row.source_bank,
               "amount": format(row.amount, ".2f"), "direction": "Credit",
               "transactionDate": row.occurred_at.isoformat()} for row in rows]
    return {"transactions": result}
