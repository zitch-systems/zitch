"""Bank payout orchestration — shared by the HTTP API and the WhatsApp channel.

Keeps a single implementation of the money movement (debit -> provider payout ->
settle/refund -> save beneficiary) so both entry points behave identically.
Callers do their own auth / PIN / tier-limit checks and name-enquiry first, then
hand a resolved account name to `execute_payout`.
"""
from decimal import Decimal

from utility.providers import payout_send
from wallet.models import Transaction
from wallet.services import (
    DuplicateTransaction,
    InsufficientFunds,
    debit,
    refund,
)

from .models import Bank, Beneficiary


def detect_account_banks(account_number: str) -> list[dict]:
    """Auto-detect which bank(s) a 10-digit NUBAN belongs to.

    A NUBAN can't be mapped to a bank offline, so we run a name-enquiry across the
    active banks in parallel and keep the ones that resolve, returning
    ``[{"bank", "bank_name", "name"}]`` (``bank`` is our slug code). Usually one
    match; a number that's a valid account at two banks (different holders) returns
    both. Cached briefly per account number so a re-resolve/retry doesn't re-sweep.

    MOCK mode (no Monnify keys) returns a single deterministic match — fanning out
    there would make every bank "match" the stub — so the flow stays testable.
    """
    from concurrent.futures import ThreadPoolExecutor

    from django.core.cache import cache

    from utility.providers import payout_live, payout_resolve_account

    ckey = f"acctdetect:{account_number}"
    cached = cache.get(ckey)
    if cached is not None:
        return cached

    banks = list(Bank.objects.filter(active=True).exclude(bank_code=""))
    if not payout_live():
        matches = ([{"bank": banks[0].code, "bank_name": banks[0].name, "name": "ADEYEMI WILLIAM"}]
                   if banks else [])
        cache.set(ckey, matches, 60)
        return matches

    def probe(b):
        res = payout_resolve_account(account_number, b.bank_code)
        if res.get("success") and res.get("name"):
            return {"bank": b.code, "bank_name": b.name, "name": res["name"]}
        return None

    matches = []
    if banks:
        with ThreadPoolExecutor(max_workers=min(8, len(banks))) as ex:
            matches = [r for r in ex.map(probe, banks) if r]
    cache.set(ckey, matches, 600)
    return matches


class PayoutError(Exception):
    """A payout could not be completed.

    `kind` is one of: ``duplicate`` (idempotency key already used),
    ``insufficient`` (balance too low), or ``provider`` (the rail rejected it,
    wallet already refunded). `message` is safe to show the user.
    """

    def __init__(self, kind: str, message: str):
        self.kind = kind
        self.message = message
        super().__init__(message)


def execute_payout(user, amount: Decimal, account_number: str, bank, name: str,
                   note: str = "", idempotency_key: str = "") -> Transaction:
    """Debit the wallet, send the payout, settle/refund, and save the beneficiary.

    The caller must already have verified the PIN + tier limits and resolved
    `name` via the provider's name-enquiry. Raises PayoutError on a duplicate,
    insufficient funds, or a provider failure (wallet auto-refunded). Returns the
    settled (Successful) ledger transaction.
    """
    try:
        txn = debit(
            user, amount, f"Transfer to {name}",
            meta={"account": account_number, "bank": bank.name, "note": note},
            idempotency_key=idempotency_key,
        )
    except DuplicateTransaction:
        raise PayoutError("duplicate", "This request was already processed.")
    except InsufficientFunds:
        raise PayoutError("insufficient", "Insufficient wallet balance.")

    result = payout_send(amount, txn.reference, note or f"Transfer to {name}",
                         bank.bank_code, account_number, name)
    if not result.get("success"):
        refund(txn)
        raise PayoutError("provider", result.get("message", "Transfer failed"))

    if (result.get("status") or "").upper() in ("PENDING", "PROCESSING"):
        # Accepted by the rail but not yet confirmed (queued / awaiting auth). Keep
        # the row PENDING and flag it for the disbursement webhook + reconciliation
        # rather than claiming success — the money stays debited, and the webhook
        # later settles it (settle_payout) or reverses it (reverse_transfer). This
        # avoids telling the user "sent" for money that may not have moved.
        meta = dict(txn.meta or {})
        meta["reconcile"] = True
        txn.meta = meta
        txn.save(update_fields=["meta"])
    else:
        txn.transaction_status = Transaction.SUCCESS
        txn.save(update_fields=["transaction_status"])

    # Auto-save / dedupe the beneficiary for next time.
    Beneficiary.objects.get_or_create(
        user=user, account_number=account_number, bank_name=bank.name,
        defaults={"name": name, "bank_code": bank.bank_code, "color": bank.color or "#0FA295"},
    )
    return txn
