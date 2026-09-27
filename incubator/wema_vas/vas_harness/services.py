from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F
from django.utils import timezone

from .contracts import InvalidPayload, money, notification, search_findings
from .fixtures import CUSTOMERS
from .identity import decrypt_identity
from .models import Inflow, VirtualAccount


class ReplayConflict(Exception):
    pass


def account_details(number):
    if settings.VAS_MODE == "synthetic" and number not in CUSTOMERS:
        return None
    return VirtualAccount.objects.filter(number=number, mode=settings.VAS_MODE).first()


def account_identity(account):
    if account.mode == "synthetic":
        fixture = CUSTOMERS[account.number]
        return {"name": fixture["name"], "bvn": fixture["bvn"],
                "nin": fixture["nin"], "phone": "00000000000"}
    identity = decrypt_identity(account.encrypted_identity)
    return {"name": account.display_name, **identity}


def acknowledge(row):
    if row.held:
        return {"status": "07", "status_desc": "Inactive Account"}, 409
    return {"transactionreference": str(row.reference), "status": "00", "status_desc": "Okay"}, 200


def process_notification(body):
    fields, amount, occurred_at, fingerprint = notification(body)
    account = account_details(fields["craccount"])
    if account is None:
        return {"status": "07", "status_desc": "Invalid Account"}, 200
    if fields["craccountname"] != account_identity(account)["name"]:
        raise InvalidPayload("Account name does not match account lookup")
    # Unique constraints cover concurrent retries and cross-account/session reuse.
    # Insert and balance update commit together; never acknowledge before commit.
    try:
        with transaction.atomic():
            account = VirtualAccount.objects.select_for_update().get(pk=account.pk)
            existing = Inflow.objects.filter(session_id=fields["sessionid"]).first()
            if existing:
                if existing.fingerprint != fingerprint:
                    raise ReplayConflict()
                return acknowledge(existing)
            row = Inflow.objects.create(
                account=account, session_id=fields["sessionid"],
                payment_reference=fields["paymentreference"], fingerprint=fingerprint,
                amount=amount, source_account=fields["originatoraccountnumber"],
                source_bank=fields["bankname"], occurred_at=occurred_at, held=not account.active,
            )
            if not row.held:
                # A conditional F update also protects the SQLite harness from
                # lost updates/overflow (SQLite has no SELECT FOR UPDATE).
                updated = VirtualAccount.objects.filter(
                    pk=account.pk, active=True,
                    simulated_balance__lte=Decimal("999999999999.99") - amount,
                ).update(simulated_balance=F("simulated_balance") + amount)
                if updated != 1:
                    raise InvalidPayload("Account unavailable or simulation balance limit exceeded")
        return acknowledge(row)
    except IntegrityError:
        # A concurrent insert may have committed first. Only the EXACT same
        # session and financial identity is a successful replay.
        existing = Inflow.objects.filter(session_id=fields["sessionid"]).first()
        if existing and existing.fingerprint == fingerprint:
            return acknowledge(existing)
        raise ReplayConflict() from None


def mini_statement(account):
    rows = account.inflows.filter(held=False).order_by("-occurred_at", "-pk")
    latest = rows.first()
    if latest is None:
        return {"transactions": []}
    # Bank says ten days counting backward from the LAST TRANSACTION'S day,
    # not ten days backward from today. Nine preceding calendar dates + anchor.
    last_day = timezone.localdate(latest.occurred_at)
    rows = rows.filter(occurred_at__date__gte=last_day - timedelta(days=9), occurred_at__date__lte=last_day)
    # An incomplete statement is worse than asking the bank to retry. Its
    # pagination/large-volume handling must be agreed before live use.
    if rows.count() > 5000:
        raise OperationalError("Statement exceeds the validation limit")
    return {"transactions": [{
        "accountNo": row.source_account, "bankName": row.source_bank,
        "amount": format(row.amount, ".2f"), "direction": "Credit",
        "transactionDate": row.occurred_at.isoformat(),
    } for row in rows]}


def reconcile_snapshot(body):
    """Compare a supplied bank-search snapshot, with NO writes/egress.

    This is not evidence of authentic bank settlement. It deliberately never
    corrects balances, acknowledges bank notifications, or releases held money.
    """
    findings = search_findings(body)
    for result, bank in zip(findings, body["transactions"]):
        amount = money(bank)
        if not account_details(result["craccount"]):
            raise InvalidPayload("Reconciliation requires an enrolled account")
        local = Inflow.objects.select_related("account").filter(session_id=result["sessionid"]).first()
        if local and (local.account.number != result["craccount"] or local.amount != amount):
            result["local_comparison"] = "conflict_manual_review"
        elif local and local.held:
            result["local_comparison"] = "held_manual_review"
        elif result["outcome"] == "uncertain_contact_bank":
            result["local_comparison"] = "unresolved_no_balance_change"
        elif local is None:
            result["local_comparison"] = "missing_receipt_request_bank_repush"
        elif result["outcome"] == "notification_repush_required":
            result["local_comparison"] = "bank_ack_discrepancy_manual_review"
        else:
            result["local_comparison"] = "matched"
    return findings
