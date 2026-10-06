"""Inspect operator-supplied INBOUND Search evidence, with no financial writes.

The supplied document does not contain Wema's production URL/authentication or
pagination contract. This module never invents them, queries a bank, books money,
refunds a payout, releases a hold, or treats a JSON file as authenticated evidence.
"""
from collections import Counter
from decimal import Decimal

from django.utils.crypto import salted_hmac

from wallet.models import Transaction

from .contracts import InvalidPayload, notification, search_findings, search_request
from .models import Receipt, VirtualAccount


def _fingerprint(value):
    # A raw hash leaks low-entropy identifiers to enumeration. This report only
    # keeps a keyed, domain-separated session pseudonym and no account identifier.
    return salted_hmac("wema_vas.search_report.session", value, algorithm="sha256").hexdigest()[:16]


def _notification_fields_match(bank, receipt):
    # requestdate is not documented as the notification's created_at. Compare
    # every other field in the immutable notification fingerprint using the
    # already-stored occurrence time; never infer or overwrite its date.
    comparable = {**bank, "created_at": receipt.occurred_at.isoformat()}
    try:
        _fields, _amount, _occurred_at, fingerprint = notification(comparable)
    except InvalidPayload:
        return False
    return fingerprint == receipt.fingerprint


def _local_result(bank, account, receipt, payment_receipt):
    if account is None:
        return "unmapped_account_manual_review"
    if account.mode != VirtualAccount.LIVE:
        return "validation_account_excluded"
    if payment_receipt is not None and payment_receipt != getattr(receipt, "pk", None):
        return "reference_conflict_manual_review"
    if receipt is not None:
        if (receipt.account_id != account.pk or receipt.amount != Decimal(bank["amount"])
                or receipt.payment_reference != bank["paymentreference"]
                or receipt.source_account != bank["originatoraccountnumber"]
                or receipt.source_bank != bank["bankname"]
                or account.display_name != bank["craccountname"]
                or not _notification_fields_match(bank, receipt)):
            return "financial_identity_conflict_manual_review"
        if receipt.state == Receipt.HELD:
            return "held_funds_manual_review"
        movement = receipt.transaction
        if (receipt.state != Receipt.CREDITED or movement is None
                or movement.user_id != account.user_id or movement.amount != receipt.amount
                or movement.direction != Transaction.IN or movement.currency != "NGN"
                or movement.transaction_status != Transaction.SUCCESS):
            return "ledger_binding_invalid_manual_review"
    # The detailed Search specification explicitly calls non-00 NIBSS outcomes
    # uncertain. The generic portal FAQ's shorthand never authorizes a refund.
    if bank["outcome"] == "uncertain_contact_bank":
        return "bank_status_uncertain_contact_support"
    if receipt is None:
        return "missing_receipt_request_bank_repush"
    if bank["outcome"] == "notification_repush_required":
        return "bank_ack_discrepancy_request_repush"
    return "matched_in_supplied_rows"


def reconcile_snapshot(body, *, session_id="", account=""):
    """Compare scoped bank-search rows and return a redacted, read-only report."""
    request = search_request(session_id=session_id, account=account)
    banks = search_findings(body)
    for bank in banks:
        if (("sessionid" in request and bank["sessionid"] != request["sessionid"])
                or ("craccount" in request and bank["craccount"] != request["craccount"])):
            raise InvalidPayload("Search response is outside the requested scope")
    accounts = {row.number: row for row in VirtualAccount.objects.filter(number__in={row["craccount"] for row in banks})}
    sessions = {row["sessionid"] for row in banks}
    receipts = {row.session_id: row for row in Receipt.objects.select_related("transaction").filter(session_id__in=sessions)}
    payments = dict(Receipt.objects.filter(payment_reference__in={row["paymentreference"] for row in banks})
                    .values_list("payment_reference", "pk"))
    findings = []
    for index, bank in enumerate(banks, start=1):
        code = _local_result(bank, accounts.get(bank["craccount"]), receipts.get(bank["sessionid"]),
                             payments.get(bank["paymentreference"]))
        findings.append({"row": index, "session_fingerprint": _fingerprint(bank["sessionid"]),
                         "bank_outcome": bank["outcome"], "local_comparison": code})
    counts = dict(sorted(Counter(row["local_comparison"] for row in findings).items()))
    action_required = any(row["local_comparison"] != "matched_in_supplied_rows" for row in findings)
    return {
        "read_only": True, "source": "operator_supplied_search_snapshot", "source_authenticated": False,
        "query_type": "session" if session_id else "account", "observed_count": len(findings),
        "scope_complete": False, "full_reconciliation_confirmed": False,
        "status": "no_rows_in_supplied_snapshot" if not findings else "review_required" if action_required else "supplied_rows_match",
        "action_required": action_required, "counts": counts, "findings": findings,
        "next_step": ("Contact Wema support for uncertain statuses, notification re-pushes or evidence conflicts. "
                      "Successful Search results do not create credits; only authenticated inflow notifications do."),
        "limitation": "Production Search URL/authentication and pagination are not supplied in the documentation. "
                      "This report does not verify source authenticity, collection settlement or an entire account history.",
    }
