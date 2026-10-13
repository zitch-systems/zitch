"""Bounded, read-only evidence for investigating ledger/bank differences.

This never creates credits, fees, refunds, history checkpoints or approvals.
Reported reference/amount matches are investigative hints, not settlement proof.
"""
import json
import re
from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count
from django.utils import timezone
from django.utils.crypto import salted_hmac

from utility import wema
from wallet.models import BankHistoryCheckpoint, Transaction, Wallet, WemaProvisioningAttempt
from wallet.reconciliation import _credit_date
from wallet.services import wallet_expected_balance, wema_provisioned_wallets


def _fingerprint(value):
    return (salted_hmac("wallet.reconciliation_evidence", str(value), algorithm="sha256")
            .hexdigest()[:16]) if value else ""


def _money(value):
    return format(value, ".2f") if value is not None else None


def _category(text):
    # Only allowlisted categories leave the process. Narrations can contain
    # customer names, phones, accounts and arbitrary provider error text.
    for category in ("vat", "fee", "charge", "levy", "interest", "transfer", "airtime", "data"):
        if re.search(r"\b" + category + r"s?\b", str(text or ""), re.I):
            return category
    return "other"


def _http_status(result):
    diagnostic = result.get("diagnostic")
    value = diagnostic.get("http_status") if isinstance(diagnostic, dict) else None
    return value if type(value) is int and 100 <= value <= 599 else None


def pool_evidence():
    """One redacted pool/account-contract probe; message classification is a hint."""
    account = str(settings.WEMA.get("SOURCE_ACCOUNT") or "").strip()
    collection = str(getattr(settings, "WEMA_VAS", {}).get("COLLECTION_ACCOUNT") or "").strip()
    valid = re.fullmatch(r"[0-9]{10}", account) is not None
    report = {"scope": "pool", "read_only": True, "configured": bool(account),
              "ten_digit_format": valid,
              "matches_wallet_account": bool(account) and Wallet.objects.filter(account_number=account).exists(),
              "matches_vas_collection": bool(account and collection) and account == collection,
              "failure_hint_is_inference": True}
    if not account or not valid or not wema.wema_live():
        return {**report, "status": "not_configured" if not account else
                "invalid_source_format" if not valid else "bank_not_live"}
    result = wema.get_balance(account)
    status = _http_status(result)
    readable = bool(result.get("success") and not result.get("mock") and result.get("balance_naira") is not None)
    raw = result.get("raw")
    message = str(result.get("message") or "").casefold()
    if isinstance(raw, dict):
        message += " " + " ".join(wema._first_text(raw.get(key)).casefold()
                                  for key in ("message", "errors", "errorMessage", "errorMessages"))
    hint = None
    if not readable:
        if status in (401, 403) or any(word in message for word in ("unauthor", "forbidden", "access denied")):
            hint = "authorization"
        elif any(word in message for word in ("not found", "does not exist", "no account", "no record")):
            hint = "not_found"
        elif "account" in message and any(word in message for word in ("invalid", "not valid")):
            hint = "invalid_account"
        else:
            hint = "bank_rejected" if status is not None and status >= 400 else "unreadable"
    return {**report, "status": "readable" if readable else "unreadable",
            "balance_readable": readable, "http_status": status, "failure_hint": hint}


def build_evidence(wallet, *, days=90, max_rows=50):
    today = timezone.localdate()
    start = max(timezone.localdate(wallet.created), today - timedelta(days=days - 1))
    checkpoints = BankHistoryCheckpoint.objects.filter(wallet=wallet)
    current = checkpoints.filter(account_number=wallet.account_number).first()
    ledger = wallet_expected_balance(wallet.user_id)
    report = {
        "user_id": wallet.user_id, "read_only": True,
        "account_set": bool(wallet.account_number),
        "account_fingerprint": _fingerprint(wallet.account_number),
        "stored_balance": _money(wallet.balance), "ledger_balance": _money(ledger),
        "checkpoint_count": checkpoints.count(),
        "current_checkpoint": None if current is None else {
            "opening_review_required": current.opening_review_required,
            "covered_through": current.covered_through.isoformat() if current.covered_through else None,
            "last_completed_at": current.last_completed_at.isoformat() if current.last_completed_at else None,
            "has_error": bool(current.last_error_code),
        },
        "provisioning_attempts": list(WemaProvisioningAttempt.objects.filter(user_id=wallet.user_id)
            .values("status", "identity_type").annotate(count=Count("id")).order_by("status", "identity_type")),
        "from": start.isoformat(), "to": today.isoformat(),
        "limitation": "Bounded observations only; opening balances, bank holds and fee/refund rules "
                      "remain unverified. No balance adjustment or settlement is authorized by this report.",
    }
    if not wallet.account_number:
        return {**report, "status": "no_bank_account"}
    if not wema_provisioned_wallets().filter(pk=wallet.pk).exists():
        return {**report, "status": "unrecognized_account_provenance"}
    if not wema.wema_live():
        return {**report, "status": "bank_not_live"}

    result = wema.get_balance(wallet.account_number)
    raw = result.get("raw")
    raw_balance = raw.get("result") if isinstance(raw, dict) else None
    report["balance_field_names"] = sorted(
        field for field in ("availableBalance", "ledgerBalance", "bookBalance", "currentBalance",
                            "holdBalance", "lienAmount")
        if isinstance(raw_balance, dict) and field in raw_balance)
    # Preserve distinct provider-reported amounts for investigation without
    # assuming that a book balance is available to spend or that a difference
    # proves a hold. Never print raw strings, unknown fields or failed responses.
    report["reported_bank_balances"] = {}
    report["reported_bank_balance_semantics"] = "provider_reported_unverified"
    if result.get("success") is True and not result.get("mock"):
        for field in report["balance_field_names"]:
            amount = wema._naira(raw_balance[field])
            if amount is not None:
                report["reported_bank_balances"][field] = _money(amount)
    bank = result.get("balance_naira") if result.get("success") and not result.get("mock") else None
    report.update(bank_available_balance=_money(bank), balance_http_status=_http_status(result),
                  ledger_minus_bank_available=_money(ledger - bank) if bank is not None else None)

    # Index all prior debits: a bank posting can land later than the ledger date.
    outgoing_qs = Transaction.objects.filter(user_id=wallet.user_id, currency="NGN",
        direction=Transaction.OUT).only("reference", "amount", "transaction_status", "meta", "service", "created")
    report["ledger_reference_scope_truncated"] = outgoing_qs.count() > 5000
    outgoing = list(outgoing_qs.order_by("-created", "-pk")[:5000])
    references = defaultdict(list)
    categories = Counter()
    ledger_totals = defaultdict(lambda: Decimal("0"))
    for txn in outgoing:
        references[txn.reference].append(txn)
        meta = txn.meta if isinstance(txn.meta, dict) else {}
        transfer = meta.get("wema_transfer")
        platform = transfer.get("platform_reference") if isinstance(transfer, dict) else None
        if isinstance(platform, str) and platform and platform != txn.reference:
            references[platform].append(txn)
        if start <= timezone.localdate(txn.created) <= today:
            categories[(_category(txn.service), txn.transaction_status)] += 1
            ledger_totals[(_category(txn.service), txn.transaction_status)] += txn.amount
    report["ledger_debit_counts"] = [
        {"category": category, "status": status, "count": count,
         "amount": _money(ledger_totals[(category, status)])}
        for (category, status), count in sorted(categories.items())]

    rows_out, totals, counts = [], defaultdict(lambda: Decimal("0")), Counter()
    seen, duplicate_rows, malformed_rows, windows = set(), 0, 0, 0
    complete, failure_status = True, None
    cursor = start
    while cursor <= today:
        end = min(today, cursor + timedelta(days=6))
        result = wema.get_transactions(wallet.account_number, cursor.isoformat(), end.isoformat())
        windows += 1
        rows = result.get("transactions")
        if (not result.get("success") or result.get("mock") or result.get("complete") is not True
                or not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows)):
            complete, failure_status = False, _http_status(result)
            break
        for row in rows:
            norm = wema.normalize_transaction(row)
            day = _credit_date(row)
            direction = str(row.get("creditType") or "").strip().casefold()
            if (day is None or not cursor <= day <= end or direction not in {"credit", "debit"}
                    or norm["amount_naira"] is None or not norm["reference"]):
                malformed_rows += 1
                complete = False
                continue
            key = (norm["reference"], direction, norm["amount_naira"], day)
            if key in seen:
                duplicate_rows += 1
                complete = False
                continue
            seen.add(key)
            status = norm["status"] if norm["status"] in {
                "successful", "successfull", "default", "pending", "failed", "reversed"} else "unknown"
            counts[(direction, status)] += 1
            totals[(direction, status)] += norm["amount_naira"]
            if direction != "debit":
                continue
            matches = {txn.pk: txn for field in ("referenceId", "tranId", "rrn")
                       for txn in references.get(str(row.get(field) or "").strip(), [])}
            amount_matches = [txn for txn in matches.values() if txn.amount == norm["amount_naira"]]
            if len(rows_out) < max_rows:
                rows_out.append({
                    "date": day.isoformat(), "reference_fingerprint": _fingerprint(norm["reference"]),
                    "amount": _money(norm["amount_naira"]), "status": status,
                    "category_hint": _category(norm["narration"]),
                    "posted_balance": _money(wema._naira(row.get("balance"))),
                    "exact_reference_matches": len(matches), "same_amount_matches": len(amount_matches),
                    "ledger_statuses": sorted({txn.transaction_status for txn in matches.values()}),
                })
        cursor = end + timedelta(days=1)
    report.update(
        status="observations_available" if complete and bank is not None else "observations_incomplete",
        bank_history_range_complete=complete, history_windows=windows, history_http_status=failure_status,
        duplicate_bank_rows=duplicate_rows, invalid_bank_rows=malformed_rows,
        bank_totals=[{"direction": direction, "status": status, "count": counts[(direction, status)],
                      "amount": _money(total)} for (direction, status), total in sorted(totals.items())],
        bank_debit_observations=rows_out,
        debit_observations_truncated=sum(count for (direction, _), count in counts.items()
                                         if direction == "debit") > len(rows_out),
    )
    return report


class Command(BaseCommand):
    help = "Read-only redacted bank/ledger evidence for named user IDs; never adjusts money."

    def add_arguments(self, parser):
        parser.add_argument("--user-ids", required=True, help="Comma-separated numeric user IDs (maximum 20).")
        parser.add_argument("--days", type=int, default=90, help="History days, 1–90 (default 90).")
        parser.add_argument("--max-rows", type=int, default=50, help="Reported debit observations per user, 1–200.")

    def handle(self, *args, **options):
        raw_ids = options["user_ids"].split(",")
        if not raw_ids or len(raw_ids) > 20 or any(
                not re.fullmatch(r"[1-9][0-9]{0,18}", value.strip()) for value in raw_ids):
            raise CommandError("Provide 1–20 positive user IDs separated by commas.")
        user_ids = {int(value.strip()) for value in raw_ids}
        if any(value > 9223372036854775807 for value in user_ids):
            raise CommandError("User IDs must fit the database integer range.")
        if not 1 <= options["days"] <= 90 or not 1 <= options["max_rows"] <= 200:
            raise CommandError("days must be 1–90 and max-rows must be 1–200.")
        try:
            pool = pool_evidence()
        except Exception:
            pool = {"scope": "pool", "read_only": True, "status": "evidence_collection_failed"}
        self.stdout.write(json.dumps(pool, sort_keys=True))
        for user_id in sorted(user_ids):
            try:
                wallet = Wallet.objects.filter(user_id=user_id).first()
                report = ({"user_id": user_id, "read_only": True, "status": "wallet_not_found"}
                          if wallet is None else build_evidence(wallet, days=options["days"], max_rows=options["max_rows"]))
            except Exception:
                # Exception strings/tracebacks can include provider request URLs,
                # identifiers or SQL. This is a diagnostic printed into build logs;
                # one account's failure must stay redacted and allow the next read.
                report = {"user_id": user_id, "read_only": True, "status": "evidence_collection_failed"}
            self.stdout.write(json.dumps(report, sort_keys=True))
